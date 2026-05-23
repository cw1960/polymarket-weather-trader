"""Compute realized P&L for fridius2 by fetching market resolutions."""
import json
import time
from collections import defaultdict

import requests

GAMMA = "https://gamma-api.polymarket.com/markets"
ROWS_FILE = "trader5_analysis.json"


def fetch_markets(condition_ids):
    """Fetch market metadata for a list of conditionIds, both open and closed."""
    out = {}
    # Batch into chunks of ~20 ids per URL
    cids = list(condition_ids)
    for closed_flag in ["true", "false"]:
        for i in range(0, len(cids), 20):
            batch = cids[i:i + 20]
            params = [("closed", closed_flag), ("limit", "100")]
            for c in batch:
                params.append(("condition_ids", c))
            r = requests.get(GAMMA, params=params, timeout=30)
            r.raise_for_status()
            for m in r.json():
                out[m["conditionId"]] = m
            time.sleep(0.2)
    return out


def main():
    rows = json.load(open(ROWS_FILE))
    trades = [r for r in rows if r.get("type") == "TRADE"]

    # Aggregate per (conditionId, asset/tokenId, outcome) — for each market+outcome, net size and cost
    # In Polymarket binary markets, buying YES at price p gives 1 token that pays $1 if YES resolves.
    # SELL reduces position (and brings in usdcSize cash).
    # We compute net token position and net cash spent per (cid, asset).
    positions = defaultdict(lambda: {"net_size": 0.0, "net_cash": 0.0, "outcome": "", "title": "", "slug": ""})
    for t in trades:
        key = (t["conditionId"], t["asset"])
        sign = 1 if t["side"] == "BUY" else -1
        positions[key]["net_size"] += sign * t["size"]
        positions[key]["net_cash"] += sign * t["usdcSize"]  # cash spent (BUY +, SELL -)
        positions[key]["outcome"] = t["outcome"]
        positions[key]["title"] = t["title"]
        positions[key]["slug"] = t["slug"]
        positions[key]["conditionId"] = t["conditionId"]
        positions[key]["asset"] = t["asset"]

    cids = list({k[0] for k in positions.keys()})
    print(f"Fetching resolution data for {len(cids)} markets...")
    markets = fetch_markets(cids)
    print(f"  got data for {len(markets)} markets")

    resolved_yes_pnl = 0.0
    resolved_no_pnl = 0.0
    open_cost = 0.0
    open_value = 0.0
    n_resolved = 0
    n_resolved_yes_win = 0
    n_resolved_no_win = 0
    n_open = 0

    # For hit rate analysis: bucket by buy price (use weighted avg buy price per position)
    # We need original buys to assign cost-basis price. Recompute per-position avg buy price.
    buys_by_key = defaultdict(list)
    for t in trades:
        if t["side"] == "BUY":
            buys_by_key[(t["conditionId"], t["asset"])].append(t)

    bucket_stats = defaultdict(lambda: {"n": 0, "win": 0, "cost": 0.0, "payout": 0.0, "n_resolved": 0})

    for key, pos in positions.items():
        cid = key[0]
        m = markets.get(cid)
        if not m:
            # market not found — treat as unknown / open
            n_open += 1
            open_cost += pos["net_cash"]
            continue

        # outcomePrices is JSON-string list like ["0","1"] or ["1","0"] (resolved) or ["0.05","0.95"] (open)
        op = json.loads(m.get("outcomePrices", "[]"))
        is_closed = m.get("closed") is True
        # Determine YES price (outcomes order = ["Yes","No"])
        yes_price = float(op[0]) if op else 0.0
        no_price = float(op[1]) if len(op) > 1 else 0.0

        # Buy prices for bucket
        buys = buys_by_key.get(key, [])
        total_buy_cost = sum(b["usdcSize"] for b in buys)
        total_buy_size = sum(b["size"] for b in buys)
        avg_buy_price = (total_buy_cost / total_buy_size) if total_buy_size > 0 else 0.0
        if avg_buy_price < 0.02: bucket = "<0.02"
        elif avg_buy_price < 0.05: bucket = "0.02-0.05"
        elif avg_buy_price < 0.10: bucket = "0.05-0.10"
        elif avg_buy_price < 0.25: bucket = "0.10-0.25"
        else: bucket = ">=0.25"

        side_outcome = pos["outcome"]  # "Yes" or "No"

        if is_closed:
            n_resolved += 1
            # Resolution: if YES resolved, YES tokens = $1, NO = $0
            yes_won = yes_price > 0.5
            if yes_won: n_resolved_yes_win += 1
            else: n_resolved_no_win += 1

            # Token payout per unit held: $1 if holding the winning outcome
            token_pays_1 = (side_outcome == "Yes" and yes_won) or (side_outcome == "No" and not yes_won)
            payout = pos["net_size"] * (1.0 if token_pays_1 else 0.0)
            pnl = payout - pos["net_cash"]

            if token_pays_1:
                resolved_yes_pnl += pnl
            else:
                resolved_no_pnl += pnl

            # Track bucket stats (only resolved positions)
            bucket_stats[bucket]["n_resolved"] += 1
            bucket_stats[bucket]["cost"] += pos["net_cash"]
            bucket_stats[bucket]["payout"] += payout
            if token_pays_1: bucket_stats[bucket]["win"] += 1
        else:
            n_open += 1
            open_cost += pos["net_cash"]
            # Mark-to-market: position size × current YES (or NO) price
            mark_price = yes_price if side_outcome == "Yes" else no_price
            open_value += pos["net_size"] * mark_price

        bucket_stats[bucket]["n"] += 1

    print(f"\n=== Resolved markets: {n_resolved} ===")
    print(f"  YES resolutions: {n_resolved_yes_win}")
    print(f"  NO resolutions:  {n_resolved_no_win}")
    print(f"  Realized P&L on winners (he held winning side): ${resolved_yes_pnl:+,.2f}")
    print(f"  Realized P&L on losers  (he held losing side):  ${resolved_no_pnl:+,.2f}")
    print(f"  TOTAL realized P&L: ${resolved_yes_pnl + resolved_no_pnl:+,.2f}")

    print(f"\n=== Open markets: {n_open} ===")
    print(f"  Cost basis still in open positions: ${open_cost:,.2f}")
    print(f"  Current mark-to-market value:       ${open_value:,.2f}")
    print(f"  Unrealized P&L: ${open_value - open_cost:+,.2f}")

    total = resolved_yes_pnl + resolved_no_pnl + (open_value - open_cost)
    print(f"\n=== TOTAL P&L (realized + unrealized): ${total:+,.2f} ===")

    print(f"\n=== Per-buy-price bucket (resolved positions only) ===")
    print(f"{'bucket':<12} {'n':>5} {'wins':>5} {'win%':>7} {'cost':>10} {'payout':>10} {'P&L':>10}  {'ROI':>7}")
    for b in ["<0.02", "0.02-0.05", "0.05-0.10", "0.10-0.25", ">=0.25"]:
        s = bucket_stats[b]
        if s["n_resolved"] == 0: continue
        wr = 100 * s["win"] / s["n_resolved"]
        pnl = s["payout"] - s["cost"]
        roi = (100 * pnl / s["cost"]) if s["cost"] > 0 else 0
        print(f"{b:<12} {s['n_resolved']:>5} {s['win']:>5} {wr:>6.1f}% ${s['cost']:>8.2f} ${s['payout']:>8.2f} ${pnl:>+8.2f}  {roi:>+6.1f}%")

    # Breakeven check for <0.02 bucket: median buy was $0.01 → breakeven = 1% hit rate
    s = bucket_stats["<0.02"]
    if s["n_resolved"] > 0:
        wr = s["win"] / s["n_resolved"]
        avg_p = s["cost"] / s["n_resolved"]  # rough; use payout/cost instead
        print(f"\n<0.02 bucket: hit rate = {wr*100:.2f}% on {s['n_resolved']} resolved positions")
        print(f"  Implied avg buy price: ~${s['cost'] / max(1, s['n_resolved']) :.3f} avg per position")
        print(f"  Required hit rate to break even at $0.01 avg buy: 1.0%")
        print(f"  Skill indicator: hit_rate vs avg_price → P&L ${s['payout']-s['cost']:+,.2f}")


if __name__ == "__main__":
    main()
