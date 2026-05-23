"""Pull fridius2's full Polymarket activity and analyze his weather-market strategy."""
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests

WALLET = "0x81035115a389a085e36255e5cb9b9ab8ee3723a1"
DATA_API = "https://data-api.polymarket.com"
OUT_FILE = "trader5_analysis.json"


def fetch_all_activity():
    all_rows = []
    offset = 0
    limit = 500
    while True:
        r = requests.get(
            f"{DATA_API}/activity",
            params={"user": WALLET, "limit": limit, "offset": offset},
            timeout=30,
        )
        if r.status_code == 400:
            print(f"  API rejected offset={offset} (likely pagination cap) — stopping")
            break
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        all_rows.extend(batch)
        print(f"  fetched {len(batch)} rows (total {len(all_rows)})")
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return all_rows


def is_weather(row):
    slug = (row.get("slug") or "") + " " + (row.get("eventSlug") or "")
    return "highest-temperature" in slug or "temperature" in slug.lower()


def analyze(rows):
    trades = [r for r in rows if r.get("type") == "TRADE"]
    weather = [r for r in trades if is_weather(r)]

    print(f"\nTotal activity rows: {len(rows)}")
    print(f"Total TRADE rows: {len(trades)}")
    print(f"Weather-market trades: {len(weather)}")

    if not trades:
        return

    # Time range
    ts = [r["timestamp"] for r in trades]
    print(f"Trade time range: {datetime.fromtimestamp(min(ts), tz=timezone.utc)} "
          f"to {datetime.fromtimestamp(max(ts), tz=timezone.utc)}")

    # Volume & side breakdown — weather only
    sides = Counter(r["side"] for r in weather)
    outcomes = Counter(r["outcome"] for r in weather)
    print(f"\n--- WEATHER MARKETS ---")
    print(f"Side breakdown: {dict(sides)}")
    print(f"Outcome breakdown: {dict(outcomes)}")
    total_usdc = sum(r["usdcSize"] for r in weather)
    buy_usdc = sum(r["usdcSize"] for r in weather if r["side"] == "BUY")
    sell_usdc = sum(r["usdcSize"] for r in weather if r["side"] == "SELL")
    print(f"Total USDC volume: ${total_usdc:,.2f}")
    print(f"  BUY  volume: ${buy_usdc:,.2f}")
    print(f"  SELL volume: ${sell_usdc:,.2f}")
    print(f"Net cash flow (sells - buys): ${sell_usdc - buy_usdc:+,.2f}")

    # Price distribution on BUYS — this tells us the strategy shape
    buys = [r for r in weather if r["side"] == "BUY"]
    sells = [r for r in weather if r["side"] == "SELL"]
    if buys:
        prices = sorted(r["price"] for r in buys)
        print(f"\nBUY price distribution (n={len(buys)}):")
        print(f"  min={min(prices):.4f}  median={prices[len(prices)//2]:.4f}  max={max(prices):.4f}")
        buckets = Counter()
        for p in prices:
            if p < 0.02: buckets["<0.02"] += 1
            elif p < 0.05: buckets["0.02-0.05"] += 1
            elif p < 0.10: buckets["0.05-0.10"] += 1
            elif p < 0.25: buckets["0.10-0.25"] += 1
            elif p < 0.50: buckets["0.25-0.50"] += 1
            elif p < 0.75: buckets["0.50-0.75"] += 1
            else: buckets[">=0.75"] += 1
        print(f"  buckets: {dict(buckets)}")
        avg_buy_size = sum(r["usdcSize"] for r in buys) / len(buys)
        print(f"  avg BUY size: ${avg_buy_size:.2f}")
    if sells:
        prices = sorted(r["price"] for r in sells)
        print(f"\nSELL price distribution (n={len(sells)}):")
        print(f"  min={min(prices):.4f}  median={prices[len(prices)//2]:.4f}  max={max(prices):.4f}")

    # Cities he trades
    cities = Counter()
    for r in weather:
        slug = r.get("eventSlug", "")
        # e.g. highest-temperature-in-london-on-may-15-2026
        parts = slug.split("-")
        if len(parts) > 3 and parts[0] == "highest" and parts[1] == "temperature":
            cities[parts[3] if parts[2] == "in" else parts[2]] += 1
    print(f"\nCities traded: {dict(cities.most_common(15))}")

    # Per-conditionId round-trips: did he close or hold to resolution?
    per_cond = defaultdict(list)
    for r in weather:
        per_cond[r["conditionId"]].append(r)
    closed = 0
    held = 0
    for cid, rows_ in per_cond.items():
        sizes = sum(r["size"] * (1 if r["side"] == "BUY" else -1) for r in rows_)
        if abs(sizes) < 0.01:
            closed += 1
        else:
            held += 1
    print(f"\nMarkets entered: {len(per_cond)}")
    print(f"  fully closed (round-tripped): {closed}")
    print(f"  still holding / held to resolution: {held}")

    # Hold times for closed positions
    hold_times = []
    for cid, rows_ in per_cond.items():
        sizes = sum(r["size"] * (1 if r["side"] == "BUY" else -1) for r in rows_)
        if abs(sizes) < 0.01:
            buy_ts = [r["timestamp"] for r in rows_ if r["side"] == "BUY"]
            sell_ts = [r["timestamp"] for r in rows_ if r["side"] == "SELL"]
            if buy_ts and sell_ts:
                hold_times.append(max(sell_ts) - min(buy_ts))
    if hold_times:
        hold_times.sort()
        med = hold_times[len(hold_times)//2]
        print(f"\nHold times (closed positions, n={len(hold_times)}):")
        print(f"  median: {med/3600:.1f}h  ({med}s)")
        print(f"  min: {min(hold_times)/60:.1f}min  max: {max(hold_times)/3600:.1f}h")

    # Recent week of weather trades — preview
    cutoff = time.time() - 7 * 86400
    recent = [r for r in weather if r["timestamp"] > cutoff]
    print(f"\nWeather trades in last 7 days: {len(recent)}")
    recent_buys = [r for r in recent if r["side"] == "BUY"]
    if recent_buys:
        rp = sorted(r["price"] for r in recent_buys)
        print(f"  recent BUY price median: {rp[len(rp)//2]:.4f}")
        print(f"  recent BUY USDC volume: ${sum(r['usdcSize'] for r in recent_buys):,.2f}")


def main():
    print(f"Fetching activity for {WALLET}...")
    rows = fetch_all_activity()
    with open(OUT_FILE, "w") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved {len(rows)} rows to {OUT_FILE}")
    analyze(rows)


if __name__ == "__main__":
    main()
