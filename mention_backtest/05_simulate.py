"""
Simulate trades from model predictions + market prices, compute pre-committed
falsification statistics.

Trade rule (pre-committed):
  edge = p_hat - p_market_yes
  buy Yes  if edge >  +0.05
  buy No   if edge <  -0.05
  no trade otherwise

Cost / PnL ($100 stake):
  effective_entry = p + 0.015        (3% spread, half to each side)
  Yes: PnL = (1 - effective_entry) * 100   if resolved Yes  else  -effective_entry * 100
  No : effective_entry_no = (1-p) + 0.015
        PnL = (1 - effective_entry_no) * 100  if resolved No  else  -effective_entry_no * 100

Pre-committed falsification (Rule 2):
  Build production iff
    avg_ROI_per_trade > 0
    AND mean/std > 0.30 across per-trade PnL
    AND NOT concentrated (removing any single event does not flip sign)
  Otherwise: walk away, no v2, no re-tune.

Exclusions: records with n_priors < 5 or saturated_at_entry == True.
"""
import json
import math
import statistics
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent
MODEL = ROOT / "model.json"
OUT = ROOT / "simulation.json"

STAKE = 100.0
SPREAD_HALF = 0.015  # 3% round-trip spread, half each side
EDGE_THRESHOLD = 0.05
MIN_PRIORS = 5


def main() -> None:
    records = json.loads(MODEL.read_text())
    eligible = [
        r for r in records
        if r["n_priors"] >= MIN_PRIORS and not r["saturated_at_entry"]
    ]
    print(f"model records: {len(records)} | eligible: {len(eligible)}")

    trades = []
    for r in eligible:
        p_h = r["p_hat"]
        p_m = r["p_market_yes"]
        edge = p_h - p_m
        side = None
        if edge > EDGE_THRESHOLD:
            side = "YES"
            entry = p_m + SPREAD_HALF
            payoff = (1 - entry) * STAKE if r["resolved_yes"] == 1 else -entry * STAKE
        elif edge < -EDGE_THRESHOLD:
            side = "NO"
            entry = (1 - p_m) + SPREAD_HALF
            payoff = (1 - entry) * STAKE if r["resolved_yes"] == 0 else -entry * STAKE
        else:
            continue
        trades.append(
            {
                "event_slug": r["event_slug"],
                "event_date": r["event_date"],
                "bracket": r["bracket"],
                "side": side,
                "p_hat": p_h,
                "p_market_yes": p_m,
                "edge": edge,
                "resolved_yes": r["resolved_yes"],
                "pnl": round(payoff, 2),
                "roi": round(payoff / STAKE, 4),
            }
        )

    n = len(trades)
    if n == 0:
        print("NO TRADES TRIGGERED — model never exceeded 5pp edge threshold.")
        print("Verdict: NO EDGE DETECTED (also: model is not separating from market).")
        OUT.write_text(json.dumps({"trades": [], "verdict": "no_trades"}, indent=2))
        return

    pnls = [t["pnl"] for t in trades]
    total = sum(pnls)
    avg_roi = total / (n * STAKE)
    mean_pnl = total / n
    sd = statistics.stdev(pnls) if n > 1 else 0.0
    ratio = mean_pnl / sd if sd > 0 else 0.0
    n_win = sum(1 for p in pnls if p > 0)

    print(f"\n== aggregate ==")
    print(f"trades         : {n}")
    print(f"wins / losses  : {n_win} / {n - n_win}  ({100*n_win/n:.1f}% win rate)")
    print(f"total PnL      : ${total:+.2f}")
    print(f"avg ROI/trade  : {avg_roi*100:+.2f}%")
    print(f"sd PnL/trade   : ${sd:.2f}")
    print(f"mean/std ratio : {ratio:.3f}")

    # Side breakdown
    for side in ("YES", "NO"):
        side_trades = [t for t in trades if t["side"] == side]
        if not side_trades:
            continue
        side_pnl = sum(t["pnl"] for t in side_trades)
        side_win = sum(1 for t in side_trades if t["pnl"] > 0)
        print(f"  {side}: n={len(side_trades)} wins={side_win} ({100*side_win/len(side_trades):.1f}%) "
              f"PnL=${side_pnl:+.2f} avg_roi={100*side_pnl/(len(side_trades)*STAKE):+.2f}%")

    # Sign-flip stress test: remove each event in turn, recompute total.
    by_event = defaultdict(list)
    for t in trades:
        by_event[t["event_slug"]].append(t["pnl"])
    print(f"\n== sign-flip stress (drop one event at a time) ==")
    flips = 0
    impacts = []
    for slug, ev_pnls in by_event.items():
        without = total - sum(ev_pnls)
        impacts.append((slug, sum(ev_pnls), without))
        if (total > 0) != (without > 0):
            flips += 1
    impacts.sort(key=lambda x: abs(x[1]), reverse=True)
    print(f"events with trades: {len(by_event)}")
    print(f"single-event removals that flip overall sign: {flips}")
    print(f"top 5 events by absolute PnL:")
    for slug, ev_total, without in impacts[:5]:
        print(f"  {slug[:60]:60} | this_ev=${ev_total:+8.2f} | rest=${without:+8.2f}")

    # Verdict
    sign_positive = total > 0
    pass_roi = avg_roi > 0
    pass_ratio = ratio > 0.30
    pass_concentration = flips == 0 and sign_positive
    verdict = (
        "BUILD_V2" if (pass_roi and pass_ratio and pass_concentration)
        else "WALK_AWAY"
    )
    print(f"\n== verdict ==")
    print(f"  avg ROI > 0      : {pass_roi}  (avg_roi={avg_roi*100:+.2f}%)")
    print(f"  mean/std > 0.30  : {pass_ratio}  (ratio={ratio:.3f})")
    print(f"  no sign-flip     : {pass_concentration}  (flips={flips})")
    print(f"  ==> {verdict}")

    OUT.write_text(
        json.dumps(
            {
                "n_trades": n,
                "n_events_traded": len(by_event),
                "total_pnl": round(total, 2),
                "avg_roi_per_trade": round(avg_roi, 4),
                "mean_std_ratio": round(ratio, 4),
                "single_event_sign_flips": flips,
                "verdict": verdict,
                "trades": trades,
            },
            indent=2,
        )
    )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
