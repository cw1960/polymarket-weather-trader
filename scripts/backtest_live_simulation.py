"""
backtest_live_simulation.py — full-system replay of the new gate, top-3-per-city
cap, week-by-week sizing, and the four guardrails, over the same Phase 1 signals
backtest_counterfactual.py used.

Output: per-day P&L breakdown, per-city breakdown, guardrail trigger events,
sizing sensitivity at $5/$10/$15.

Assumption being tested (CLAUDE.md Rule 2):
  Across resolved markets in the post-WU-source-fix window, if the live bot
  had been running with the new gate + top-3 per city + week-1 sizing + four
  guardrails, would the simulated P&L curve survive the 8% daily-loss limit
  and the 45% 3-day win-rate floor?

Falsifying outcomes:
  - Worst-day P&L exceeds -8% bankroll on a non-trivial day → guardrail trips
  - Any 3-day rolling window has win rate < 45% with n>=15 → 3day guardrail trips
  - Either of those would force a pause in production, so we'd want to know.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, str(Path(__file__).parent))

from supabase import create_client  # noqa: E402
from forecast_bias import get_correction as get_forecast_bias  # noqa: E402
# Reuse parsing + delta lookup from the counterfactual backtest
from backtest_counterfactual import (  # noqa: E402
    parse_bracket, old_delta_mean, get_forecast_row,
    ORACLE_BUG, fetch_signals,
)

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

# Parameters from production (matches sizing_schedule.week_1 + system_config)
NEW_EDGE      = 0.08
NEW_MIN_PROB  = 0.55
TOP_N_PER_CITY = 3
TRADE_SIZES   = [5.0, 10.0, 15.0]   # week 1 / week 2 / week 3+
DAILY_LOSS_PCT = 0.08
MIN_3DAY_WIN_RATE = 0.45
MIN_3DAY_RESOLVED = 15
BANKROLL_FLOOR = 1500.0


def regen_prob_for_row(r):
    forecast = get_forecast_row(r["city"], r["forecast_date"])
    if not forecast:
        return None
    members = [float(m) for m in (forecast.get("raw_members") or []) + (forecast.get("ecmwf_members") or []) if m is not None]
    if not members:
        return None
    month   = datetime.fromisoformat(r["forecast_date"]).month
    shift   = get_forecast_bias(r["city"]) - old_delta_mean(r["city"], month)
    shifted = [m + shift for m in members]
    parsed  = parse_bracket(r.get("outcome") or "")
    if not parsed:
        return None
    low_c, high_c, _ = parsed
    return sum(1 for m in shifted if low_c <= m <= high_c) / len(shifted)


def won_for_row(r):
    a = str(r.get("actual_outcome"))
    if r.get("side") == "YES":
        return a == "true"
    return a == "false"


def pnl_for_trade(side, market_price, won, size):
    """Per-trade P&L on a $size bet at market_price for the chosen side."""
    if won:
        return size * (1.0 - market_price) / market_price
    return -size


def main():
    raw = fetch_signals()
    rows = [r for r in raw if (r["city"], r["forecast_date"]) not in ORACLE_BUG]

    # Compute regen prob_for_side for every row (cache forecasts)
    print(f"Regenerating probs for {len(rows)} resolved signals...")
    enriched = []
    for r in rows:
        py = regen_prob_for_row(r)
        if py is None:
            continue
        prob_side = (1 - py) if r["side"] == "NO" else py
        r["_regen_prob_yes"]      = py
        r["_regen_prob_for_side"] = prob_side
        r["_edge"]                = prob_side - r["market_price"]
        r["_won"]                 = won_for_row(r)
        enriched.append(r)
    print(f"  enriched: {len(enriched)}")

    # ── Apply gate, group by (city, date), take top-N per city/date ─────
    by_market: dict[tuple[str, str], list] = defaultdict(list)
    for r in enriched:
        if r["_regen_prob_for_side"] < NEW_MIN_PROB:
            continue
        if r["_edge"] < NEW_EDGE:
            continue
        by_market[(r["city"], r["forecast_date"])].append(r)

    fired: list = []
    for k, rs in by_market.items():
        rs.sort(key=lambda x: x["_edge"], reverse=True)
        fired.extend(rs[:TOP_N_PER_CITY])
    print(f"  trades after gate + top-{TOP_N_PER_CITY}/city: {len(fired)}")

    # ── Sizing sensitivity ────────────────────────────────────────────
    print("\n=== SIZING SENSITIVITY (whole window) ===")
    print(f"{'size':>6} | {'trades':>7} | {'wins':>5} | {'wr':>6} | {'P&L':>9} | {'$/trade':>8}")
    for sz in TRADE_SIZES:
        wins = sum(1 for r in fired if r["_won"])
        pnl  = sum(pnl_for_trade(r["side"], r["market_price"], r["_won"], sz) for r in fired)
        wr   = wins / len(fired) if fired else 0
        print(f"${sz:>5.0f} | {len(fired):>7d} | {wins:>5d} | {wr:>5.1%} | ${pnl:>+8.2f} | ${pnl/len(fired):>+7.2f}")

    # ── Per-day P&L using $5 sizing (week 1 baseline) ──────────────────
    print("\n=== PER-DAY P&L AT $5/trade (week 1 sizing) ===")
    by_day: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for r in fired:
        d = r["forecast_date"]
        by_day[d]["trades"] += 1
        if r["_won"]:
            by_day[d]["wins"] += 1
        by_day[d]["pnl"] += pnl_for_trade(r["side"], r["market_price"], r["_won"], 5.0)
    print(f"{'date':12} | {'trades':>7} | {'wins':>5} | {'WR':>6} | {'P&L':>9}")
    for d in sorted(by_day):
        s = by_day[d]
        wr = s["wins"] / s["trades"] if s["trades"] else 0
        print(f"{d} | {s['trades']:>7d} | {s['wins']:>5d} | {wr:>5.1%} | ${s['pnl']:>+8.2f}")

    # ── Guardrail simulation ───────────────────────────────────────────
    print("\n=== GUARDRAIL SIMULATION at $5/trade ===")
    # Assume bankroll start = $1600 (week-2 simulated, after $500 deposit + refunds)
    bankroll = 1600.0
    print(f"  starting bankroll: ${bankroll:.0f}")
    print(f"  daily loss limit (={DAILY_LOSS_PCT*100:.0f}% of bankroll): ${-DAILY_LOSS_PCT*bankroll:.2f}")
    print(f"  min 3-day win rate (after {MIN_3DAY_RESOLVED}+ resolved trades): {MIN_3DAY_WIN_RATE*100:.0f}%")
    print()
    triggered_daily_loss = []
    triggered_3day = []
    dates = sorted(by_day)
    for i, d in enumerate(dates):
        # Daily loss check
        pnl = by_day[d]["pnl"]
        if pnl <= -DAILY_LOSS_PCT * bankroll:
            triggered_daily_loss.append((d, pnl, -DAILY_LOSS_PCT * bankroll))
        # 3-day win rate check
        window = dates[max(0, i - 2):i + 1]
        ww = sum(by_day[x]["wins"] for x in window)
        wn = sum(by_day[x]["trades"] for x in window)
        if wn >= MIN_3DAY_RESOLVED:
            wrr = ww / wn
            if wrr < MIN_3DAY_WIN_RATE:
                triggered_3day.append((d, ww, wn, wrr))
        # Update bankroll
        bankroll += pnl
        if bankroll < BANKROLL_FLOOR:
            print(f"  BANKROLL FLOOR TRIPPED on {d}: bankroll=${bankroll:.2f} < ${BANKROLL_FLOOR}")

    if triggered_daily_loss:
        print(f"  ⚠️  daily-loss guardrail would have tripped on {len(triggered_daily_loss)} day(s):")
        for d, p, lim in triggered_daily_loss:
            print(f"     {d}: P&L ${p:+.2f} ≤ limit ${lim:+.2f}")
    else:
        print(f"  ✓ daily-loss guardrail NEVER tripped")

    if triggered_3day:
        print(f"  ⚠️  3-day-win-rate guardrail would have tripped on {len(triggered_3day)} day(s):")
        for d, w, n, r in triggered_3day:
            print(f"     {d}: {w}/{n} = {r:.1%} < {MIN_3DAY_WIN_RATE*100:.0f}%")
    else:
        print(f"  ✓ 3-day-win-rate guardrail NEVER tripped (given enough samples)")

    # ── Per-city breakdown at $5 ─────────────────────────────────────
    print("\n=== PER-CITY BREAKDOWN at $5/trade ===")
    by_city: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for r in fired:
        c = r["city"]
        by_city[c]["trades"] += 1
        if r["_won"]:
            by_city[c]["wins"] += 1
        by_city[c]["pnl"] += pnl_for_trade(r["side"], r["market_price"], r["_won"], 5.0)
    print(f"{'city':16} | {'trades':>7} | {'wins':>5} | {'WR':>6} | {'P&L':>8}")
    sorted_cities = sorted(by_city.items(), key=lambda x: x[1]["pnl"], reverse=True)
    for c, s in sorted_cities:
        wr = s["wins"] / s["trades"] if s["trades"] else 0
        print(f"{c:16} | {s['trades']:>7d} | {s['wins']:>5d} | {wr:>5.1%} | ${s['pnl']:>+7.2f}")


if __name__ == "__main__":
    main()
