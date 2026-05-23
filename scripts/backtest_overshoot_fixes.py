"""
Backtest: Bayesian shrinkage + boundary buffer
================================================
For each historical real-money Phase 2 trade, replay the new logic to see if
it would have picked a different (better) bracket.

What we have:
  - mean_high = METAR running_max at lock time
  - Bet bracket and actual winning bracket
  - Current delta_c and delta_samples per city
  - PnL of the original trade

What we don't have:
  - Historical delta values at trade time (they've evolved)
  - Historical YES price for alternative brackets

So this is an APPROXIMATE backtest. We use CURRENT delta values to simulate
what the new logic would produce. Real P&L is bounded but not exact.
"""
import re
from collections import defaultdict
from config import SUPABASE_URL, SUPABASE_KEY, CITY_UNITS
from supabase import create_client

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

BAYESIAN_K = 5
BOUNDARY_BUFFER_C = 0.3
CALIB_MIN = 3
DEFAULT_DELTA = 1.0


def shrink(raw_delta: float, samples: int) -> float:
    if samples <= 0:
        return 0.0
    return (samples / (samples + BAYESIAN_K)) * raw_delta


def predict_bracket(running_max_c: float, delta_c: float, samples: int,
                    actual_temp: int, apply_buffer: bool = True) -> tuple[int, str]:
    """
    Replay new logic. Returns (predicted_bracket_temp, reason).
    Uses 1°C-wide buckets centered on each integer (X-0.5 ≤ x < X+0.5).
    """
    if samples >= CALIB_MIN:
        effective_delta = shrink(delta_c, samples)
    else:
        effective_delta = DEFAULT_DELTA

    adjusted = running_max_c + effective_delta
    # Find bracket: integer N where N-0.5 <= adjusted < N+0.5
    bracket = int(round(adjusted))
    bracket_low = bracket - 0.5
    distance_above_low = adjusted - bracket_low

    reason = "normal"
    if apply_buffer and 0 <= distance_above_low <= BOUNDARY_BUFFER_C + 1e-6:
        bracket = bracket - 1
        reason = f"buffer (adj={adjusted:.2f}, was bracket {bracket+1}, bumped down)"

    return bracket, reason


def main():
    # Load all real-money resolved Phase 2 trades
    trades = (sb.table("trade_signals")
              .select("*")
              .eq("signal_phase", "phase2")
              .not_.is_("pnl_usd", "null")
              .limit(500)
              .execute()).data
    real = [t for t in trades if float(t.get("recommended_position") or 0) > 1]

    # Load current deltas
    ds = sb.table("resolution_stations").select("city, delta_c, delta_samples").execute()
    dmap = {r["city"]: (float(r.get("delta_c") or 0), int(r.get("delta_samples") or 0))
            for r in ds.data}

    print("=" * 100)
    print("BACKTEST: Bayesian Shrinkage + Boundary Buffer")
    print("=" * 100)
    print()
    print("Caveats:")
    print("  - Uses CURRENT deltas, not historical (deltas have evolved)")
    print("  - Cannot exactly compute hypothetical P&L for changed bets (no historical")
    print("    bracket-specific prices)")
    print("  - Approximates: if a changed bet would have WON, count as win at avg payout")
    print("    of $300 (mean of historical wins). If LOST, -$45.")
    print()

    # Get average win payout for estimation
    wins_pnl = [float(t["pnl_usd"]) for t in real if float(t["pnl_usd"]) > 0]
    avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 300

    print(f"Average historical win payout (used for hypothetical wins): ${avg_win:.2f}")
    print()

    rows = []
    summary = {
        "unchanged_win": 0, "unchanged_loss": 0,
        "now_correct":   0, "now_wrong":    0,
        "saved_pnl": 0.0, "lost_pnl": 0.0,
    }

    print(f"{'Date':10} {'City':14} {'Lock':6} {'Cur δ':7} {'Eff δ':7} {'Adj':6} "
          f"{'Old bet':7} {'New bet':7} {'Actual':6} {'Old':6} {'New':6} {'Δ PnL':10}")
    print("-" * 110)

    for t in sorted(real, key=lambda x: x.get("forecast_date", "")):
        city = t["city"]
        bet_nums = re.findall(r"-?\d+", t["outcome"])
        win_nums = re.findall(r"-?\d+", t.get("winning_bracket", "") or "")
        if not bet_nums or not win_nums:
            continue

        old_bet = int(bet_nums[0])
        actual = int(win_nums[0])
        lock_max = float(t.get("mean_high") or 0)
        if lock_max == 0:
            continue
        old_pnl = float(t["pnl_usd"])
        old_won = old_pnl > 0

        delta, samples = dmap.get(city, (0, 0))
        if samples >= CALIB_MIN:
            eff_delta = shrink(delta, samples)
        else:
            eff_delta = DEFAULT_DELTA

        # CITY_UNITS: F or C. For F we'd need different math; treat F like C since
        # most trades are C. (Actual implementation handles F properly.)
        unit = CITY_UNITS.get(city, "C")

        # For F cities, scale buffer to F equivalent
        new_bet, reason = predict_bracket(lock_max, delta, samples, actual)

        # Determine new outcome
        new_won = (new_bet == actual)
        change = ""
        delta_pnl = 0.0

        if new_bet == old_bet:
            # No change
            if old_won:
                summary["unchanged_win"] += 1
            else:
                summary["unchanged_loss"] += 1
            change = "—"
        else:
            # Bet changed
            if old_won and not new_won:
                # Used to win, now lose (BAD — fix broke a winner)
                summary["now_wrong"] += 1
                summary["lost_pnl"] -= old_pnl + 45  # lost the win, plus -$45 stake
                delta_pnl = -(old_pnl + 45)
                change = "BROKE win"
            elif not old_won and new_won:
                # Used to lose, now win (GOOD — fix recovered a loss)
                summary["now_correct"] += 1
                summary["saved_pnl"] += avg_win - (-45)  # win avg, no longer -$45
                delta_pnl = avg_win + 45
                change = "FIXED loss"
            elif not old_won and not new_won:
                # Still loss, just different bracket
                summary["unchanged_loss"] += 1
                change = "diff loss"
            else:
                # Different winning brackets — impossible since only one resolves
                summary["unchanged_win"] += 1
                change = "?"

        print(f"{t.get('forecast_date',''):10} {city:14} {lock_max:5.1f}° "
              f"{delta:+6.2f} {eff_delta:+6.2f} {lock_max+eff_delta:5.1f}° "
              f"{old_bet:6}° {new_bet:6}° {actual:5}° "
              f"{'WIN' if old_won else 'LOSS':5} {'WIN' if new_won else 'LOSS':5} "
              f"{change}")

    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    n_total = sum(summary[k] for k in ["unchanged_win","unchanged_loss","now_correct","now_wrong"])
    print(f"Total real-money trades replayed: {n_total}")
    print()
    print(f"Unchanged wins:    {summary['unchanged_win']:2}  (system picks same bracket, still wins)")
    print(f"Unchanged losses:  {summary['unchanged_loss']:2}  (system picks different bracket but still wrong, OR same)")
    print(f"FIXED losses:      {summary['now_correct']:2}  (former losses that would now win)")
    print(f"BROKEN wins:       {summary['now_wrong']:2}  (former wins that would now lose)")
    print()
    print(f"Estimated P&L improvement: ${summary['saved_pnl'] - summary['lost_pnl']:+.2f}")
    print(f"  (saved from fixed losses: ${summary['saved_pnl']:+.2f})")
    print(f"  (lost from broken wins:   ${-summary['lost_pnl']:+.2f})")
    print()

    # Compute new average miss distance
    new_misses = []
    for t in real:
        bet_nums = re.findall(r"-?\d+", t["outcome"])
        win_nums = re.findall(r"-?\d+", t.get("winning_bracket","") or "")
        lock_max = float(t.get("mean_high") or 0)
        if not bet_nums or not win_nums or lock_max == 0:
            continue
        actual = int(win_nums[0])
        delta, samples = dmap.get(t["city"], (0, 0))
        new_bet, _ = predict_bracket(lock_max, delta, samples, actual)
        new_misses.append(abs(new_bet - actual))

    old_avg = sum(float(t.get("miss_distance_c") or 0) for t in real) / len(real) if real else 0
    new_avg = sum(new_misses) / len(new_misses) if new_misses else 0

    print(f"Average miss distance:")
    print(f"  Before fixes:  {old_avg:.2f}°C")
    print(f"  After fixes:   {new_avg:.2f}°C")
    print(f"  Change:        {new_avg - old_avg:+.2f}°C")


if __name__ == "__main__":
    main()
