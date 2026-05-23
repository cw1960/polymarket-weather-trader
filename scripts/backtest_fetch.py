"""
Fetch historical data needed for backtesting.

Three data sources:
  1. Open-Meteo historical forecast API  → GFS day-ahead predicted high temps
  2. NOAA GHCN daily API                 → actual observed high temps
  3. Polymarket Gamma API (closed events) → bracket boundaries + which bracket won
                                           + pre-resolution price if available

Usage:
  python scripts/backtest_fetch.py --start 2024-06-01 --end 2026-04-20
  python scripts/backtest_fetch.py --start 2024-06-01 --end 2026-04-20 --city NYC
  python scripts/backtest_fetch.py --start 2024-06-01 --end 2026-04-20 --skip-markets
"""
import sys
import math
import time
import json
import argparse
import requests
from datetime import date, timedelta
from supabase import create_client

sys.path.insert(0, "scripts") if "scripts" not in sys.path[0] else None
from config import SUPABASE_URL, SUPABASE_KEY, CITIES, CITY_UNITS, NOAA_STATIONS
from fetch_markets import city_to_slug, parse_bucket, _parse_yes_price

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

HIST_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
NOAA_API_URL      = "https://www.ncei.noaa.gov/access/services/data/v1"
GAMMA_URL         = "https://gamma-api.polymarket.com"

BATCH_DAYS = 90   # fetch GFS / NOAA in 90-day chunks to stay within API limits
REQUEST_DELAY = 0.3  # seconds between API calls


# ── 1. GFS historical day-ahead forecast ──────────────────────────────────────

def fetch_gfs_range(lat: float, lon: float, start: date, end: date) -> dict[date, float]:
    """
    Returns {date: predicted_high_c} for dates in [start, end].

    Uses the deterministic GFS forecast (temperature_2m_max daily).
    The historical-forecast-api returns what the model predicted for each date
    using the model run initialised the day before — i.e. the day-ahead forecast.
    """
    params = {
        "latitude":    lat,
        "longitude":   lon,
        "daily":       "temperature_2m_max",
        "models":      "gfs_seamless",
        "start_date":  start.isoformat(),
        "end_date":    end.isoformat(),
        "timezone":    "UTC",
    }
    try:
        r = requests.get(HIST_FORECAST_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        times  = data["daily"]["time"]
        values = data["daily"]["temperature_2m_max"]
        return {
            date.fromisoformat(t): float(v)
            for t, v in zip(times, values)
            if v is not None
        }
    except Exception as e:
        print(f"    GFS fetch error: {e}")
        return {}


def fetch_and_store_forecasts(city: str, lat: float, lon: float,
                               start: date, end: date) -> int:
    """Fetch GFS forecasts in batches and upsert into backtest_forecasts."""
    stored = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=BATCH_DAYS - 1), end)
        preds = fetch_gfs_range(lat, lon, cursor, chunk_end)
        if preds:
            rows = [
                {"city": city, "forecast_date": d.isoformat(), "raw_forecast_c": round(v, 2)}
                for d, v in preds.items()
            ]
            sb.table("backtest_forecasts").upsert(
                rows, on_conflict="city,forecast_date"
            ).execute()
            stored += len(rows)
        cursor = chunk_end + timedelta(days=1)
        time.sleep(REQUEST_DELAY)
    return stored


# ── 2. NOAA actual high temperatures ─────────────────────────────────────────

def fetch_noaa_range(station_id: str, start: date, end: date) -> dict[date, float]:
    """
    Returns {date: actual_high_c} from NOAA GHCN daily.
    TMAX is stored as tenths of °C in GHCN (divide by 10).
    """
    params = {
        "dataset":          "daily-summaries",
        "stations":         station_id,
        "startDate":        start.isoformat(),
        "endDate":          end.isoformat(),
        "dataTypes":        "TMAX",
        "format":           "json",
        "includeAttributes": "false",
        "units":            "metric",   # returns °C directly (no divide by 10 needed)
    }
    try:
        r = requests.get(NOAA_API_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json()
        return {
            date.fromisoformat(row["DATE"][:10]): float(row["TMAX"])
            for row in rows
            if "TMAX" in row
        }
    except Exception as e:
        print(f"    NOAA fetch error ({station_id}): {e}")
        return {}


def fetch_and_store_actuals(city: str, station_id: str,
                             start: date, end: date) -> int:
    stored = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=BATCH_DAYS - 1), end)
        actuals = fetch_noaa_range(station_id, cursor, chunk_end)
        if actuals:
            rows = [
                {"city": city, "date": d.isoformat(),
                 "actual_c": round(v, 2), "station_id": station_id}
                for d, v in actuals.items()
            ]
            sb.table("backtest_actuals").upsert(
                rows, on_conflict="city,date"
            ).execute()
            stored += len(rows)
        cursor = chunk_end + timedelta(days=1)
        time.sleep(REQUEST_DELAY)
    return stored


# ── 3. Polymarket resolved markets ───────────────────────────────────────────

def _get_series_events(city: str) -> list[dict]:
    """Return all events (open + closed) for a city's daily weather series."""
    slug = city_to_slug(city)  # city_to_slug already appends "-daily-weather"
    try:
        r = requests.get(f"{GAMMA_URL}/series", params={"slug": slug}, timeout=15)
        r.raise_for_status()
        series_list = r.json()
        if not series_list:
            return []
        series = series_list[0] if isinstance(series_list, list) else series_list
        return series.get("events", [])
    except Exception as e:
        print(f"    Series fetch error ({city}): {e}")
        return []


def _event_to_market_date(event: dict) -> date | None:
    """Extract the market's resolution date from the event title or endDate."""
    end = event.get("endDate", "")
    if end:
        try:
            return date.fromisoformat(end[:10])
        except ValueError:
            pass
    return None


def _parse_resolved_markets(event: dict, city: str) -> list[dict]:
    """
    Extract bucket rows from a resolved Polymarket event.

    For each bracket we record:
      - label, low, high, unit
      - yes_price: last traded price if available (pre-resolution proxy), else None
      - resolved_yes: True for the bracket that won
    """
    unit = CITY_UNITS.get(city, "C")
    rows = []

    # Use embedded markets if the series response already includes them;
    # only fall back to a per-event API call when they're absent.
    markets = event.get("markets")
    ev = event
    if not markets:
        try:
            r = requests.get(f"{GAMMA_URL}/events/{event['id']}", timeout=15)
            r.raise_for_status()
            ev = r.json()
            markets = ev.get("markets", [])
        except Exception as e:
            print(f"      Event detail fetch error: {e}")
            return []
    for mkt in markets:
        question = mkt.get("question", "")
        bucket = parse_bucket(question)
        if not bucket:
            continue

        # Determine which outcome is YES
        outcomes_raw = mkt.get("outcomes", "[]")
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        prices_raw = mkt.get("outcomePrices", None)

        # After resolution, outcomePrices is ["1","0"] or ["0","1"]
        # We use this to determine which resolved YES
        resolved_yes = False
        yes_price    = None

        if prices_raw is not None:
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                prices = [float(p) for p in prices]
                try:
                    yes_idx = next(i for i, o in enumerate(outcomes) if str(o).lower() == "yes")
                except StopIteration:
                    yes_idx = 0
                yes_final_price = prices[yes_idx] if yes_idx < len(prices) else prices[0]
                # If yes_final_price ≈ 1.0, this bracket won
                resolved_yes = yes_final_price > 0.9
                # If not yet resolved or prices aren't settlement values, store as market price
                if not (yes_final_price > 0.9 or yes_final_price < 0.1):
                    yes_price = round(yes_final_price, 4)
            except Exception:
                pass

        # Try lastTradePrice as pre-resolution price proxy
        if yes_price is None:
            ltp = mkt.get("lastTradePrice")
            if ltp is not None:
                try:
                    p = float(ltp)
                    # Only use if it looks like a real pre-resolution price (not 0 or 1)
                    if 0.001 < p < 0.999:
                        yes_price = round(p, 4)
                except (ValueError, TypeError):
                    pass

        rows.append({
            "city":         city,
            "label":        bucket["label"],
            "low":          bucket["low"],
            "high":         bucket["high"],
            "unit":         unit,
            "yes_price":    yes_price,
            "resolved_yes": resolved_yes,
            "event_slug":   ev.get("slug", ""),
            "condition_id": mkt.get("conditionId", ""),
        })

    return rows


def fetch_and_store_markets(city: str, start: date, end: date) -> int:
    """
    Fetch all resolved Polymarket temperature markets for a city in [start, end]
    and store bracket outcomes in backtest_markets.
    """
    events = _get_series_events(city)
    closed  = [e for e in events if e.get("closed", False)
               and _event_to_market_date(e) is not None
               and start <= _event_to_market_date(e) <= end]
    print(f"      {len(closed)} closed events to fetch…", flush=True)
    stored = 0

    for i, event in enumerate(closed, 1):
        mkt_date = _event_to_market_date(event)
        if i % 50 == 0 or i == len(closed):
            print(f"      … {i}/{len(closed)} events processed, {stored} rows so far", flush=True)

        brackets = _parse_resolved_markets(event, city)
        if not brackets:
            continue

        winners = [b for b in brackets if b["resolved_yes"]]
        if len(winners) != 1:
            # Skip if we can't cleanly identify the winner
            # (may happen for markets still pending resolution)
            continue

        rows = [
            {
                "city":         b["city"],
                "market_date":  mkt_date.isoformat(),
                "label":        b["label"],
                "low":          b["low"],
                "high":         b["high"],
                "unit":         b["unit"],
                "yes_price":    b["yes_price"],
                "resolved_yes": b["resolved_yes"],
                "event_slug":   b["event_slug"],
                "condition_id": b["condition_id"],
            }
            for b in brackets
        ]
        sb.table("backtest_markets").upsert(
            rows, on_conflict="city,market_date,label"
        ).execute()
        stored += len(rows)
        time.sleep(REQUEST_DELAY)

    return stored


# ── 4. Empirical std computation ──────────────────────────────────────────────

def compute_and_store_empirical_std(city: str) -> int:
    """
    For each forecast row, compute the rolling 90-day RMSE of (forecast - actual)
    and write it back as empirical_std_c.
    This gives a data-driven estimate of forecast uncertainty per city.
    """
    f_res = (sb.table("backtest_forecasts")
             .select("forecast_date,raw_forecast_c")
             .eq("city", city)
             .order("forecast_date")
             .execute())
    a_res = (sb.table("backtest_actuals")
             .select("date,actual_c")
             .eq("city", city)
             .order("date")
             .execute())

    if not f_res.data or not a_res.data:
        return 0

    forecasts = {row["forecast_date"]: row["raw_forecast_c"] for row in f_res.data}
    actuals   = {row["date"]: row["actual_c"]           for row in a_res.data}

    # Build list of (date, error) where error = forecast - actual
    pairs = sorted(
        (d, forecasts[d] - actuals[d])
        for d in forecasts
        if d in actuals
    )

    if not pairs:
        return 0

    # Rolling 90-day RMSE centred at each date
    WINDOW = 90
    updates = []
    for i, (d, _) in enumerate(pairs):
        window = [e for _, e in pairs[max(0, i - WINDOW // 2): i + WINDOW // 2 + 1]]
        rmse = math.sqrt(sum(e ** 2 for e in window) / len(window))
        updates.append({
            "city":             city,
            "forecast_date":    d,
            "raw_forecast_c":   forecasts[d],
            "empirical_std_c":  round(rmse, 3),
        })

    # Upsert in one batch
    sb.table("backtest_forecasts").upsert(
        updates, on_conflict="city,forecast_date"
    ).execute()

    return len(updates)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch historical backtest data")
    parser.add_argument("--start",        default="2024-06-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",          default=str(date.today() - timedelta(days=2)),
                        help="End date YYYY-MM-DD (default: 2 days ago)")
    parser.add_argument("--city",         default=None, help="Single city (default: all)")
    parser.add_argument("--skip-markets", action="store_true",
                        help="Skip Polymarket fetch (faster, Pass 1 only)")
    parser.add_argument("--skip-std",     action="store_true",
                        help="Skip empirical std computation")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    cities = [args.city] if args.city else CITIES

    print(f"Backtest data fetch: {start} → {end} | {len(cities)} cities")
    print(f"Phases: GFS forecasts + NOAA actuals"
          + ("" if args.skip_markets else " + Polymarket markets")
          + ("" if args.skip_std     else " + empirical std"))
    print()

    # Load station coords once
    coords_res = sb.table("resolution_stations").select("city,lat,lon,station_id").execute()
    coords = {r["city"]: (r["lat"], r["lon"], r["station_id"]) for r in coords_res.data}

    for city in cities:
        info = coords.get(city)
        if not info:
            print(f"  {city}: no resolution station, skipping")
            continue
        lat, lon, station_id = info
        noaa_id = NOAA_STATIONS.get(city, station_id)

        print(f"  {city}")

        # Phase 1: GFS forecasts
        n = fetch_and_store_forecasts(city, lat, lon, start, end)
        print(f"    GFS: {n} forecast rows stored")

        # Phase 2: NOAA actuals
        n = fetch_and_store_actuals(city, noaa_id, start, end)
        print(f"    NOAA: {n} actual rows stored")

        # Phase 3: Polymarket resolved markets
        if not args.skip_markets:
            n = fetch_and_store_markets(city, start, end)
            print(f"    Polymarket: {n} bracket rows stored")

        # Phase 4: Empirical std
        if not args.skip_std:
            n = compute_and_store_empirical_std(city)
            print(f"    Empirical std: {n} rows updated")


if __name__ == "__main__":
    main()
