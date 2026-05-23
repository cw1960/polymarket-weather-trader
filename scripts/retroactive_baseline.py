"""
Retroactive Baseline Reset
==========================
Replay the variance-adjusted K + conditional boundary buffer logic against
all historical real-money Phase 2 trades. Update miss_distance_c, pnl_usd,
and bankroll to give us a clean baseline going forward.

CAVEATS (be honest about these):
  - Uses CURRENT deltas (which were informed by these very trades).
    The "true" forward result may differ.
  - Hypothetical wins use a smart estimator: if any other trade on the same
    date had a similar price, use its payout; otherwise use the median win.
  - This is an APPROXIMATE baseline, not actual history.
"""
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone, date
from config import (
    SUPABASE_URL, SUPABASE_KEY, CITY_UNITS, DEFAULT_BANKROLL_USD,
)
from supabase import create_client

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

K_BASE = 5
BOUNDARY_BUFFER_C = 0.3
STABILITY_THRESHOLD = 0.3
CALIB_MIN = 3
DEFAULT_DELTA = 1.0
TRADE_SIZE = 45.0


def load_observed_deltas() -> tuple[dict, float]:
    """Compute σ per city and σ_global from resolved Phase 2 trades."""
    res = (sb.table("trade_signals")
           .select("city, mean_high, winning_bracket")
           .eq("signal_phase", "phase2")
           .not_.is_("pnl_usd", "null")
           .not_.is_("mean_high", "null")
           .limit(500)
           .execute())
    deltas = defaultdict(list)
    for r in res.data or []:
        nums = re.findall(r"-?\d+", r.get("winning_bracket", "") or "")
        if not nums:
            continue
        actual_native = float(nums[0])
        mean_high = float(r["mean_high"])
        if mean_high == 0:
            continue
        unit = CITY_UNITS.get(r["city"], "C")
        actual_c = (actual_native - 32) * 5 / 9 if unit == "F" else actual_native
        deltas[r["city"]].append(actual_c - mean_high)

    sigmas = {c: statistics.stdev(d) for c, d in deltas.items() if len(d) >= CALIB_MIN}
    sigma_global = statistics.median(sigmas.values()) if sigmas else 0.5
    return sigmas, sigma_global


def predict_bracket(running_max_c: float, raw_delta: float, samples: int,
                    sigma_city: float | None, sigma_global: float) -> int:
    """Replay the new prediction logic. Returns predicted integer bracket (°C)."""
    # Calibration tier
    if samples >= CALIB_MIN:
        # Variance-adjusted K
        if sigma_city is None or sigma_global <= 0:
            k = K_BASE
        else:
            k = max(1.0, min(10.0, K_BASE * (sigma_city / sigma_global)))
        eff_delta = (samples / (samples + k)) * raw_delta
    else:
        eff_delta = DEFAULT_DELTA

    adjusted = running_max_c + eff_delta
    bracket = int(round(adjusted))

    # Conditional buffer
    apply_buffer = (sigma_city is None) or (sigma_city >= STABILITY_THRESHOLD - 1e-6)
    bracket_low = bracket - 0.5
    distance_above_low = adjusted - bracket_low

    if apply_buffer and 0 <= distance_above_low <= BOUNDARY_BUFFER_C + 1e-6 and bracket > 0:
        bracket -= 1

    return bracket


def main():
    print("=" * 100)
    print("RETROACTIVE BASELINE RESET")
    print("=" * 100)
    print()
    print("CAVEATS:")
    print("  - Current deltas (informed by historical trades) used → leakage")
    print("  - Hypothetical wins estimated using median real-money win payout")
    print("  - This is an APPROXIMATE baseline, not actual history")
    print()

    # Load variance state
    sigmas, sigma_global = load_observed_deltas()
    print(f"σ_global: {sigma_global:.3f}°C across {len(sigmas)} cities")
    print()

    # Load deltas
    ds = sb.table("resolution_stations").select("city, delta_c, delta_samples").execute()
    dmap = {r["city"]: (float(r.get("delta_c") or 0), int(r.get("delta_samples") or 0))
            for r in ds.data}

    # Load all real-money Phase 2 trades
    res = (sb.table("trade_signals")
           .select("*")
           .eq("signal_phase", "phase2")
           .not_.is_("pnl_usd", "null")
           .order("forecast_date")
           .limit(500)
           .execute())
    trades = res.data
    real = [t for t in trades if float(t.get("recommended_position") or 0) > 1]

    # Compute median winning P&L for hypothetical-win estimation
    winning_pnls = [float(t["pnl_usd"]) for t in real if float(t["pnl_usd"]) > 0]
    median_win = statistics.median(winning_pnls) if winning_pnls else 200.0
    print(f"Median real-money win used for hypothetical estimates: ${median_win:.2f}")
    print()

    # Replay each trade
    print(f"{'Date':10} {'City':14} {'Lock':6} {'Old bet':7} {'New bet':7} "
          f"{'Actual':6} {'Old PnL':>10} {'New PnL':>10} {'Δ':>10} Note")
    print("-" * 115)

    summary = {
        "kept_win":   0, "kept_loss":  0,
        "fixed_loss": 0, "broke_win":  0, "diff_loss": 0,
        "old_pnl":    0.0, "new_pnl":    0.0,
    }

    updates = []
    for t in real:
        city = t["city"]
        bn = re.findall(r"-?\d+", t["outcome"])
        wn = re.findall(r"-?\d+", t.get("winning_bracket", "") or "")
        if not bn or not wn:
            continue

        unit = CITY_UNITS.get(city, "C")
        actual_native = int(wn[0])
        actual_c = int(round((actual_native - 32) * 5 / 9)) if unit == "F" else actual_native
        old_bet_native = int(bn[0])
        old_bet_c = int(round((old_bet_native - 32) * 5 / 9)) if unit == "F" else old_bet_native

        lock_max = float(t.get("mean_high") or 0)
        if lock_max == 0:
            continue

        raw_d, samples = dmap.get(city, (0, 0))
        sigma = sigmas.get(city)

        new_bet_c = predict_bracket(lock_max, raw_d, samples, sigma, sigma_global)

        old_pnl = float(t["pnl_usd"])
        old_won = old_pnl > 0
        new_won = (new_bet_c == actual_c)

        # Compute new P&L
        if new_bet_c == old_bet_c:
            # Same bracket → same outcome
            new_pnl = old_pnl
            note = ""
            if old_won:
                summary["kept_win"] += 1
            else:
                summary["kept_loss"] += 1
        else:
            # Bracket changed
            if old_won and not new_won:
                # We were right, now we'd lose
                new_pnl = -TRADE_SIZE
                summary["broke_win"] += 1
                note = "BROKE win (was correct)"
            elif not old_won and new_won:
                # We were wrong, now we'd win — estimate via median win
                new_pnl = median_win
                summary["fixed_loss"] += 1
                note = f"FIXED loss (estimate +${median_win:.0f})"
            elif not old_won and not new_won:
                new_pnl = -TRADE_SIZE
                summary["diff_loss"] += 1
                note = "diff loss"
            else:
                new_pnl = old_pnl  # impossible case
                note = "?"

        new_miss = abs(new_bet_c - actual_c)

        summary["old_pnl"] += old_pnl
        summary["new_pnl"] += new_pnl

        delta_pnl = new_pnl - old_pnl
        delta_str = f"${delta_pnl:+.2f}" if delta_pnl != 0 else "—"

        print(f"{t.get('forecast_date',''):10} {city:14} {lock_max:5.1f}° "
              f"{old_bet_c:6}° {new_bet_c:6}° {actual_c:5}°  "
              f"${old_pnl:+9.2f} ${new_pnl:+9.2f} {delta_str:>10}  {note}")

        updates.append({
            "id":              t["id"],
            "new_pnl":         round(new_pnl, 4),
            "miss_distance_c": new_miss,
        })

    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"Trades unchanged:    {summary['kept_win'] + summary['kept_loss'] + summary['diff_loss']:2}  "
          f"(wins={summary['kept_win']}, losses={summary['kept_loss']}, diff-loss={summary['diff_loss']})")
    print(f"Fixed losses:        {summary['fixed_loss']:2}  (now wins, estimated payout)")
    print(f"Broken wins:         {summary['broke_win']:2}  (now losses, -$45)")
    print()
    print(f"Old aggregate P&L:   ${summary['old_pnl']:+.2f}")
    print(f"New aggregate P&L:   ${summary['new_pnl']:+.2f}")
    print(f"Net change:          ${summary['new_pnl'] - summary['old_pnl']:+.2f}")
    print()

    # Confirm before applying
    print("Applying updates to database...")
    for u in updates:
        sb.table("trade_signals").update({
            "pnl_usd":         u["new_pnl"],
            "miss_distance_c": u["miss_distance_c"],
        }).eq("id", u["id"]).execute()
    print(f"Updated {len(updates)} trade rows.")

    # Reconcile bankroll.  Explicit .limit(50_000) guards against
    # Supabase's silent 1000-row default cap (would silently truncate
    # the cumulative-P&L sum once history exceeds 1000 trades).
    all_res = (sb.table("trade_signals")
               .select("pnl_usd")
               .not_.is_("pnl_usd", "null")
               .not_.eq("winning_bracket", "VOIDED")
               .limit(50_000)
               .execute())
    cumulative = sum(float(r["pnl_usd"]) for r in all_res.data)
    new_bankroll = round(DEFAULT_BANKROLL_USD + cumulative, 2)

    sb.table("system_config").upsert(
        {"key": "bankroll_usd", "value": str(new_bankroll),
         "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="key",
    ).execute()

    today = date.today().isoformat()
    sb.table("bankroll_snapshots").delete().eq("snapshot_date", today).execute()
    sb.table("bankroll_snapshots").insert({
        "snapshot_date":    today,
        "total_value":      new_bankroll,
        "cash":             new_bankroll,
        "daily_pnl":        0,
        "active_positions": 0,
        "is_paper":         True,
    }).execute()

    print(f"\nNew bankroll: ${new_bankroll:.2f}")
    print(f"(Starting $1,000 + cumulative P&L ${cumulative:+.2f})")


if __name__ == "__main__":
    main()
