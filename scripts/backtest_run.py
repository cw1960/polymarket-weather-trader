"""
Run the backtest simulation.

Pass 1 — GFS Calibration:
  For each city/date pair where we have a GFS forecast AND a NOAA actual:
  - Construct the ladder (using the forecast mean + empirical std)
  - Check which bracket the actual temperature fell into
  - Bucket by σ distance and measure hit rate vs model probability
  - A well-calibrated model should hit 10% of the time when it says 10%

Pass 2 — P&L Simulation:
  Same as Pass 1 but now price each rung:
  - Use actual Polymarket prices from backtest_markets where available
  - Fall back to the price model for any bracket without a real price
  - Compute P&L per ladder and aggregate statistics

Usage:
  python scripts/backtest_run.py --pass1
  python scripts/backtest_run.py --pass2
  python scripts/backtest_run.py --pass1 --pass2 --tag my_run
  python scripts/backtest_run.py --pass2 --city NYC --no-save
"""
import sys
import math
import argparse
from datetime import date, datetime, timezone
from collections import defaultdict

sys.path.insert(0, "scripts") if "scripts" not in sys.path[0] else None
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, CITIES, CITY_UNITS
from ladder import build_ladder, LADDER_DEFAULTS

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Price model (fallback when no Polymarket price available) ─────────────────
# Derived from observed price structure across current live markets.
# Maps σ distance → estimated market yes_price.
# This is conservative (slightly generous to the market) to avoid overfitting.

PRICE_MODEL = [
    (0.0,  0.20),   # 0–0.5σ:   ~20¢  (near-the-money)
    (0.5,  0.10),   # 0.5–1σ:   ~10¢
    (1.0,  0.04),   # 1–1.5σ:    ~4¢
    (1.5,  0.015),  # 1.5–2σ:  ~1.5¢
    (2.0,  0.006),  # 2–2.5σ:  ~0.6¢
    (2.5,  0.003),  # 2.5–3σ:  ~0.3¢
    (3.0,  0.002),  # 3–3.5σ:  ~0.2¢
]

def price_model(distance_sigma: float) -> float:
    for threshold, price in reversed(PRICE_MODEL):
        if distance_sigma >= threshold:
            return price
    return 0.20


# ── Data loading ──────────────────────────────────────────────────────────────

def load_data(cities: list[str], start: str, end: str) -> dict:
    """
    Returns a dict keyed by (city, date_str) with:
      forecast: {raw_forecast_c, empirical_std_c}
      actual:   {actual_c}
      markets:  [{label, low, high, unit, yes_price, resolved_yes}]  (may be empty)
    """
    print("Loading backtest data from Supabase...")

    def fetch_all(query):
        """Paginate through Supabase results (default limit is 1000 rows)."""
        rows, offset = [], 0
        while True:
            res = query.range(offset, offset + 999).execute()
            rows.extend(res.data)
            if len(res.data) < 1000:
                break
            offset += 1000
        return rows

    f_rows = fetch_all(
        sb.table("backtest_forecasts")
          .select("city,forecast_date,raw_forecast_c,empirical_std_c")
          .in_("city", cities)
          .gte("forecast_date", start)
          .lte("forecast_date", end)
    )
    a_rows = fetch_all(
        sb.table("backtest_actuals")
          .select("city,date,actual_c")
          .in_("city", cities)
          .gte("date", start)
          .lte("date", end)
    )
    m_rows = fetch_all(
        sb.table("backtest_markets")
          .select("city,market_date,label,low,high,unit,yes_price,resolved_yes")
          .in_("city", cities)
          .gte("market_date", start)
          .lte("market_date", end)
    )
    print(f"  Raw rows: {len(f_rows)} forecasts, {len(a_rows)} actuals, {len(m_rows)} markets")

    data: dict = {}

    for row in f_rows:
        key = (row["city"], str(row["forecast_date"])[:10])
        data.setdefault(key, {"forecast": None, "actual": None, "markets": []})
        data[key]["forecast"] = {
            "raw_forecast_c":  row["raw_forecast_c"],
            "empirical_std_c": row["empirical_std_c"],
        }

    for row in a_rows:
        key = (row["city"], str(row["date"])[:10])
        data.setdefault(key, {"forecast": None, "actual": None, "markets": []})
        data[key]["actual"] = {"actual_c": row["actual_c"]}

    for row in m_rows:
        key = (row["city"], str(row["market_date"])[:10])
        data.setdefault(key, {"forecast": None, "actual": None, "markets": []})
        data[key]["markets"].append(row)

    print(f"  {len(data)} city/date pairs loaded")
    pairs_with_both = sum(
        1 for v in data.values()
        if v["forecast"] and v["actual"]
    )
    pairs_with_markets = sum(
        1 for v in data.values()
        if v["markets"]
    )
    print(f"  {pairs_with_both} pairs have forecast + actual (usable for Pass 1)")
    print(f"  {pairs_with_markets} pairs have Polymarket market data (usable for Pass 2)")
    return data


# ── Determine which bracket the actual temperature falls into ─────────────────

def find_winning_bracket(actual_c: float, buckets: list[dict], unit: str) -> str | None:
    """Return the label of the bracket containing actual_c (after unit conversion)."""
    actual = actual_c * 9 / 5 + 32 if unit == "F" else actual_c
    for b in buckets:
        if b["low"] <= actual < b["high"]:
            return b["label"]
    # Handle exact upper boundary
    if buckets:
        last = buckets[-1]
        if actual >= last["low"]:
            return last["label"]
    return None


def synthetic_buckets_from_actual(actual_c: float, mean_c: float,
                                   std_c: float, unit: str) -> list[dict]:
    """
    When no Polymarket market data is available, generate synthetic 2°F / 1°C
    buckets spanning ±4σ around the forecast mean. Used for Pass 1 calibration.
    """
    mean = mean_c * 9 / 5 + 32 if unit == "F" else mean_c
    std  = std_c  * 9 / 5      if unit == "F" else std_c
    step = 2.0 if unit == "F" else 1.0

    # Range: mean ± 4σ, rounded to step boundaries
    lo = math.floor((mean - 4 * std) / step) * step
    hi = math.ceil( (mean + 4 * std) / step) * step

    buckets = []
    t = lo
    while t < hi:
        label = f"{int(t)}-{int(t + step - 1)}°{unit}"
        buckets.append({
            "label":  label,
            "low":    t,
            "high":   t + step,
            "unit":   unit,
        })
        t += step

    # Cap tails into open-ended buckets
    if buckets:
        buckets[0]["low"]   = -9999
        buckets[0]["label"] = f"≤{int(buckets[0]['high'] - 1)}°{unit}"
        buckets[-1]["high"] = 9999
        buckets[-1]["label"] = f"≥{int(buckets[-1]['low'])}°{unit}"

    return buckets


# ── Pass 1: GFS calibration ───────────────────────────────────────────────────

def run_pass1(data: dict, config: dict | None = None) -> dict:
    """
    For each city/date with forecast + actual, construct a ladder and check
    whether each rung hit or missed. Aggregate by σ-distance band.

    Returns calibration stats dict.
    """
    cfg = {**LADDER_DEFAULTS, **(config or {})}

    # sigma_band → {model_prob_sum, hit_count, total_count}
    bands = defaultdict(lambda: {"model_prob_sum": 0.0, "hits": 0, "total": 0})
    # For reliability diagram: model_prob bucket → {hits, total}
    reliability = defaultdict(lambda: {"hits": 0, "total": 0})

    skipped = 0
    processed = 0

    for (city, d), entry in sorted(data.items()):
        if not entry["forecast"] or not entry["actual"]:
            skipped += 1
            continue

        mean_c = entry["forecast"]["raw_forecast_c"]
        std_c  = entry["forecast"]["empirical_std_c"]
        if std_c is None or std_c <= 0:
            std_c = 2.0  # fallback: 2°C if empirical std not yet computed

        actual_c = entry["actual"]["actual_c"]
        unit = CITY_UNITS.get(city, "C")

        # Use Polymarket buckets if available, else synthetic
        if entry["markets"]:
            buckets = [
                {**m, "yes_price": m["yes_price"] or price_model(0.5)}
                for m in entry["markets"]
            ]
        else:
            buckets = synthetic_buckets_from_actual(actual_c, mean_c, std_c, unit)
            for b in buckets:
                b["yes_price"] = price_model(0.5)  # placeholder; not used in Pass 1

        # Build ladder using the existing logic
        rungs = build_ladder(mean_c, std_c, buckets, city, config=cfg)
        if not rungs:
            skipped += 1
            continue

        winning_label = find_winning_bracket(actual_c, buckets, unit)
        processed += 1

        for rung in rungs:
            hit = (rung["label"] == winning_label)
            sigma = rung["distance_sigma"]
            prob  = rung["model_prob"]

            # σ band (0–0.5, 0.5–1, ..., 3–3.5)
            band_key = f"{math.floor(sigma * 2) / 2:.1f}–{math.floor(sigma * 2) / 2 + 0.5:.1f}σ"
            bands[band_key]["model_prob_sum"] += prob
            bands[band_key]["hits"]  += int(hit)
            bands[band_key]["total"] += 1

            # Reliability: round model_prob to nearest 5%
            prob_bucket = round(prob * 20) / 20  # nearest 0.05
            reliability[f"{prob_bucket:.0%}"]["hits"]  += int(hit)
            reliability[f"{prob_bucket:.0%}"]["total"] += 1

    print(f"\nPass 1 — GFS Calibration")
    print(f"  Processed: {processed} city/date pairs | Skipped: {skipped}")
    print()
    print(f"  {'σ band':<12} {'Rungs':>6} {'Avg model%':>11} {'Hit rate':>9} {'Calibration':>12}")
    print(f"  {'-'*12} {'-'*6} {'-'*11} {'-'*9} {'-'*12}")

    for band in sorted(bands.keys()):
        b = bands[band]
        if b["total"] == 0:
            continue
        avg_model = b["model_prob_sum"] / b["total"]
        hit_rate  = b["hits"] / b["total"]
        ratio     = hit_rate / avg_model if avg_model > 0 else 0
        flag = "  ✓" if 0.7 <= ratio <= 1.4 else ("  ↑ OVER" if ratio > 1.4 else "  ↓ UNDER")
        print(f"  {band:<12} {b['total']:>6} {avg_model*100:>10.1f}% {hit_rate*100:>8.1f}%"
              f"  {ratio:>5.2f}x{flag}")

    print()
    print("  Reliability diagram (model probability vs actual hit rate):")
    print(f"  {'Model says':>12} {'Actual hit':>11} {'N':>6} {'Diff':>8}")
    print(f"  {'-'*12} {'-'*11} {'-'*6} {'-'*8}")
    for bucket in sorted(reliability.keys(),
                         key=lambda x: float(x.strip("%")) / 100):
        rb = reliability[bucket]
        if rb["total"] < 5:
            continue
        model_p  = float(bucket.strip("%")) / 100
        actual_p = rb["hits"] / rb["total"]
        diff     = actual_p - model_p
        flag = "  ↑" if diff > 0.05 else ("  ↓" if diff < -0.05 else "")
        print(f"  {bucket:>12} {actual_p*100:>10.1f}% {rb['total']:>6} {diff*100:>+7.1f}%{flag}")

    return {"bands": dict(bands), "reliability": dict(reliability),
            "processed": processed, "skipped": skipped}


# ── Pass 2: P&L simulation ────────────────────────────────────────────────────

def run_pass2(data: dict, run_tag: str, save: bool = True,
              config: dict | None = None) -> dict:
    """
    Simulate the full ladder strategy using historical data.
    Prices come from Polymarket where available, price model as fallback.
    Returns P&L summary and per-ladder detail.
    """
    cfg = {**LADDER_DEFAULTS, **(config or {})}

    ladder_results = []
    save_buffer    = []
    skipped = 0

    for (city, d), entry in sorted(data.items()):
        if not entry["forecast"] or not entry["actual"]:
            skipped += 1
            continue
        if not entry["markets"]:
            # Need actual market structure for Pass 2 bucketing
            skipped += 1
            continue

        mean_c   = entry["forecast"]["raw_forecast_c"]
        std_c    = entry["forecast"]["empirical_std_c"] or 2.0
        actual_c = entry["actual"]["actual_c"]
        unit     = CITY_UNITS.get(city, "C")

        buckets = []
        for m in entry["markets"]:
            b = dict(m)
            b["yes_price"] = m["yes_price"] or price_model(
                abs(((m["low"] + m["high"]) / 2) -
                    (mean_c * 9 / 5 + 32 if unit == "F" else mean_c))
                / max(std_c * (9 / 5 if unit == "F" else 1), 0.01)
            )
            b["no_price"]     = round(1 - b["yes_price"], 4)
            b["condition_id"] = m.get("condition_id", "")
            b["market_id"]    = ""
            b["question"]     = ""
            buckets.append(b)

        winning_label = next(
            (m["label"] for m in entry["markets"] if m["resolved_yes"]), None
        )
        if winning_label is None:
            skipped += 1
            continue

        rungs = build_ladder(mean_c, std_c, buckets, city, config=cfg)
        if not rungs:
            continue

        total_cost = sum(r["size_usd"] for r in rungs)
        winning_rung = next((r for r in rungs if r["label"] == winning_label), None)

        # Determine price source for the winning rung specifically.
        # A ladder is only "real-priced" if the winning bracket had an actual
        # Polymarket price — otherwise the price model would fabricate a payout.
        winning_market = next(
            (m for m in entry["markets"] if m["label"] == winning_label), None
        ) if winning_label else None
        winning_has_real_price = bool(winning_market and winning_market.get("yes_price"))
        price_source = "polymarket" if winning_has_real_price else "model"

        if winning_rung:
            payout = winning_rung["size_usd"] / max(winning_rung["yes_price"], 0.001)
            pnl    = payout - total_cost
        else:
            payout = 0.0
            pnl    = -total_cost

        ladder_results.append({
            "city":                  city,
            "market_date":           d,
            "num_rungs":             len(rungs),
            "num_core":              sum(1 for r in rungs if r["rung_type"] == "core"),
            "num_wings":             sum(1 for r in rungs if r["rung_type"] == "wing"),
            "total_cost":            round(total_cost, 2),
            "payout":                round(payout, 2),
            "pnl":                   round(pnl, 2),
            "won":                   winning_rung is not None,
            "winning_label":         winning_label,
            "winning_sigma":         winning_rung["distance_sigma"] if winning_rung else None,
            "price_source":          price_source,
            "winning_has_real_price": winning_has_real_price,
            "rungs":                 rungs,
        })

        if save:
            for r in rungs:
                save_buffer.append({
                    "run_tag":        run_tag,
                    "city":           city,
                    "market_date":    d,
                    "label":          r["label"],
                    "rung_type":      r["rung_type"],
                    "distance_sigma": r["distance_sigma"],
                    "model_prob":     r["model_prob"],
                    "yes_price":      r["yes_price"],
                    "size_usd":       r["size_usd"],
                    "resolved_yes":   r["label"] == winning_label,
                    "pnl":            round(
                        r["size_usd"] / max(r["yes_price"], 0.001) - total_cost
                        if r["label"] == winning_label else -r["size_usd"], 2
                    ),
                    "price_source":   price_source,
                })

    # ── Flush save buffer in batches ──────────────────────────────────────────
    if save and save_buffer:
        BATCH = 500
        for i in range(0, len(save_buffer), BATCH):
            sb.table("backtest_results").insert(save_buffer[i:i + BATCH]).execute()
        print(f"  Saved {len(save_buffer)} rung rows to backtest_results.")

    # ── Summary stats ──────────────────────────────────────────────────────────

    if not ladder_results:
        print("\nPass 2 — no ladder results (need Polymarket market data).")
        print("  Run backtest_fetch.py without --skip-markets first.")
        return {}

    # Split into real-price vs model-price ladders
    real  = [l for l in ladder_results if l["winning_has_real_price"] or not l["won"]]
    model = [l for l in ladder_results if not l["winning_has_real_price"] and l["won"]]

    n_ladders  = len(ladder_results)
    n_won      = sum(1 for l in ladder_results if l["won"])

    def _stats(rows: list[dict], label: str):
        if not rows:
            print(f"\n  {label}: no data")
            return
        nw   = sum(1 for l in rows if l["won"])
        cost = sum(l["total_cost"] for l in rows)
        pnl  = sum(l["pnl"] for l in rows)
        roi  = pnl / cost if cost > 0 else 0

        winning   = [l for l in rows if l["won"]]
        multiples = sorted([l["payout"] / l["total_cost"] for l in winning])

        streak = max_streak = 0
        for l in rows:
            streak = 0 if l["won"] else streak + 1
            max_streak = max(max_streak, streak)

        monthly: dict[str, float] = defaultdict(float)
        for l in rows:
            monthly[l["market_date"][:7]] += l["pnl"]

        print(f"\n  ── {label} ({len(rows)} ladders) ──")
        print(f"  Win rate:          {nw}/{len(rows)} = {nw/len(rows)*100:.1f}%")
        print(f"  Total cost:        ${cost:,.2f}")
        print(f"  Total P&L:         ${pnl:+,.2f}")
        print(f"  ROI:               {roi*100:+.1f}%")
        print(f"  Max losing streak: {max_streak} ladders")
        if multiples:
            print(f"  Payout multiples (winning ladders):")
            print(f"    Min {min(multiples):.1f}x  Median {multiples[len(multiples)//2]:.1f}x"
                  f"  p75 {multiples[int(len(multiples)*0.75)]:.1f}x"
                  f"  Max {max(multiples):.1f}x")
        print(f"  Monthly P&L:")
        for month in sorted(monthly.keys()):
            bar = "█" * min(int(abs(monthly[month]) / 5), 60)
            print(f"    {month}  ${monthly[month]:+8.2f}  {bar}")

    print(f"\nPass 2 — P&L Simulation  [{run_tag}]")
    print(f"  Ladders simulated:   {n_ladders}  (won {n_won})")
    print(f"  Skipped:             {skipped}")
    print(f"  Real-price winners:  {len([l for l in ladder_results if l['winning_has_real_price'] and l['won']])}"
          f"  |  Model-price winners: {len(model)}")
    print()
    print("  NOTE: 'Real-price' = winning bracket had an actual Polymarket price.")
    print("        'Model-price' = price fabricated from σ model — NOT reliable P&L.")

    _stats([l for l in ladder_results if l["winning_has_real_price"] or not l["won"]],
           "Real-price ladders (reliable)")
    _stats(model, "Model-price winners (unreliable — shown for reference only)")

    return {
        "n_ladders":      n_ladders,
        "n_won":          n_won,
        "win_rate":       n_won / n_ladders,
        "ladder_results": ladder_results,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run ladder strategy backtest")
    parser.add_argument("--pass1",   action="store_true", help="Run calibration analysis")
    parser.add_argument("--pass2",   action="store_true", help="Run P&L simulation")
    parser.add_argument("--start",   default="2024-06-01")
    parser.add_argument("--end",     default=str(date.today()))
    parser.add_argument("--city",    default=None, help="Single city")
    parser.add_argument("--tag",     default=None, help="Run tag for Pass 2 results")
    parser.add_argument("--no-save", action="store_true", help="Don't write to Supabase")
    args = parser.parse_args()

    if not args.pass1 and not args.pass2:
        parser.print_help()
        print("\nError: specify --pass1, --pass2, or both")
        sys.exit(1)

    cities = [args.city] if args.city else CITIES
    run_tag = args.tag or f"backtest_{args.start}_{args.end}"

    data = load_data(cities, args.start, args.end)

    if args.pass1:
        run_pass1(data)

    if args.pass2:
        run_pass2(data, run_tag=run_tag, save=not args.no_save)


if __name__ == "__main__":
    main()
