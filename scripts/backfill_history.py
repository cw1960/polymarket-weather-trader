"""
backfill_history.py — pulls historical price + temp data for every
(city, date) Polymarket weather market that resolved in the last N days.

What it writes (per (city, date)):
  • historical_bracket_prices         — CLOB price-history per bracket
  • historical_event_resolutions      — winning bracket + day-max temp
  • historical_temp_observations      — WU hourly obs for the day

Idempotent: re-runs overwrite (PRIMARY KEYs handle dedup; we use upsert).

Usage:
  python3 scripts/backfill_history.py                       # default 90 days
  python3 scripts/backfill_history.py --days 30
  python3 scripts/backfill_history.py --city NYC --days 14  # one city only
  python3 scripts/backfill_history.py --date 2026-05-21     # one date, all cities

Run on the VPS where dotenv has SUPABASE_SERVICE_KEY:
  cd /root/polymarket && venv/bin/python3 scripts/backfill_history.py
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path("/root/polymarket/.env"))

from supabase import create_client  # noqa: E402

sys.path.insert(0, str(Path("/root/polymarket/scripts")))
try:
    from wunderground import STATIONS as WU_STATIONS  # type: ignore
except Exception:
    WU_STATIONS = {}

_url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
_key = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(_url, _key)

log = logging.getLogger("backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S UTC")
logging.Formatter.converter = time.gmtime

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"
WU_APIKEY  = "e1f10a1e78da46f5b10a1e78da96f525"

# Same map as trader_price_collector.py
CITY_SLUG = {
    "NYC": "nyc", "Chicago": "chicago", "Miami": "miami", "Los Angeles": "los-angeles",
    "Dallas": "dallas", "Atlanta": "atlanta", "Houston": "houston", "Austin": "austin",
    "Seattle": "seattle", "San Francisco": "san-francisco", "Denver": "denver",
    "London": "london", "Paris": "paris", "Madrid": "madrid", "Munich": "munich",
    "Milan": "milan", "Amsterdam": "amsterdam", "Warsaw": "warsaw", "Helsinki": "helsinki",
    "Istanbul": "istanbul", "Ankara": "ankara", "Moscow": "moscow",
    "Tel Aviv": "tel-aviv", "Jeddah": "jeddah",
    "Hong Kong": "hong-kong", "Seoul": "seoul", "Tokyo": "tokyo", "Busan": "busan",
    "Taipei": "taipei", "Beijing": "beijing", "Shanghai": "shanghai", "Guangzhou": "guangzhou",
    "Shenzhen": "shenzhen", "Chengdu": "chengdu", "Chongqing": "chongqing", "Wuhan": "wuhan",
    "Singapore": "singapore", "Kuala Lumpur": "kuala-lumpur", "Manila": "manila",
    "Jakarta": "jakarta", "Lucknow": "lucknow", "Karachi": "karachi",
    "Wellington": "wellington", "Toronto": "toronto", "Mexico City": "mexico-city",
    "São Paulo": "sao-paulo", "Buenos Aires": "buenos-aires", "Panama City": "panama-city",
    "Cape Town": "cape-town", "Lagos": "lagos",
}

_RANGE_RE  = re.compile(r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°([fc])", re.IGNORECASE)
_LE_RE     = re.compile(r"(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+below",  re.IGNORECASE)
_GE_RE     = re.compile(r"(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+higher", re.IGNORECASE)
_SINGLE_RE = re.compile(r"be\s+(-?\d+(?:\.\d+)?)\s*°([fc])\s+on", re.IGNORECASE)


def parse_bracket(question: str):
    if not question: return None
    m = _RANGE_RE.search(question)
    if m: return (f"{int(float(m.group(1)))}-{int(float(m.group(2)))}°{m.group(3).upper()}",
                  float(m.group(1)) - 0.5, float(m.group(2)) + 0.5, m.group(3).upper())
    m = _LE_RE.search(question)
    if m: return (f"≤{int(float(m.group(1)))}°{m.group(2).upper()}",
                  float("-inf"), float(m.group(1)) + 0.5, m.group(2).upper())
    m = _GE_RE.search(question)
    if m: return (f"≥{int(float(m.group(1)))}°{m.group(2).upper()}",
                  float(m.group(1)) - 0.5, float("inf"), m.group(2).upper())
    m = _SINGLE_RE.search(question)
    if m: return (f"{int(float(m.group(1)))}°{m.group(2).upper()}",
                  float(m.group(1)) - 0.5, float(m.group(1)) + 0.5, m.group(2).upper())
    return None


def fetch_event(slug: str):
    try:
        r = requests.get(f"{GAMMA_BASE}/events/slug/{slug}", timeout=15)
        if r.status_code == 404: return None
        return r.json() if r.ok else None
    except Exception as e:
        log.warning(f"gamma fetch {slug}: {e}")
        return None


def fetch_clob_history(token_id: str):
    """Return list of {t, p} (unix seconds, 0..1 price) for the YES side."""
    url = f"{CLOB_BASE}/prices-history?market={token_id}&interval=max&fidelity=10"
    try:
        r = requests.get(url, timeout=20)
        if not r.ok: return []
        return (r.json() or {}).get("history", []) or []
    except Exception as e:
        log.warning(f"clob {token_id[:12]}…: {e}")
        return []


def fetch_wu_hourly(city: str, d: date):
    """Pull WU hourly observations for one (city, date). Returns list of dicts."""
    if city not in WU_STATIONS: return []
    icao, country = WU_STATIONS[city]
    yyyymmdd = d.strftime("%Y%m%d")
    url = (f"https://api.weather.com/v1/location/{icao}:9:{country}/"
           f"observations/historical.json?apiKey={WU_APIKEY}&units=e&startDate={yyyymmdd}")
    try:
        r = requests.get(url, timeout=15)
        if not r.ok: return []
        return (r.json() or {}).get("observations", []) or []
    except Exception as e:
        log.warning(f"wu {city} {d}: {e}")
        return []


def upsert_chunked(table: str, rows: list, chunk=500, on_conflict: str | None = None):
    """Upsert rows in chunks with retry on transient HTTP/2 errors.
    Supabase-py uses HTTP/2 by default which can fail under concurrent
    uploads (ConnectionTerminated / COMPRESSION_ERROR). We retry each
    chunk up to 4 times with exponential backoff."""
    if not rows: return 0
    inserted = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i:i+chunk]
        for attempt in range(4):
            try:
                q = sb.table(table)
                if on_conflict:
                    q.upsert(batch, on_conflict=on_conflict).execute()
                else:
                    q.upsert(batch).execute()
                inserted += len(batch)
                break
            except Exception as e:
                if attempt == 3:
                    log.warning(f"upsert {table} chunk {i} GAVE UP after 4 tries: {e}")
                else:
                    time.sleep(0.5 * (2 ** attempt))   # 0.5s, 1s, 2s
    return inserted


def backfill_event(city: str, d: date) -> tuple[int, int, int]:
    """Return (price_rows, obs_rows, resolved?) for this (city, date)."""
    date_slug = d.strftime("%B-%-d-%Y").lower()
    event_slug = f"highest-temperature-in-{CITY_SLUG[city]}-on-{date_slug}"
    ev = fetch_event(event_slug)
    if ev is None:
        return (0, 0, 0)

    # --- Price history for every bracket (parallel CLOB fetches) ---
    # Build per-bracket job specs first, then dispatch CLOB requests in
    # parallel. ~11 brackets per event → drops per-event wall time from
    # ~7s (sequential) to ~1.5s (parallel). Polymarket's CLOB handles
    # ~50 concurrent reads without complaint.
    jobs = []          # list of (label, lo, hi, unit, cid, yes_token)
    winning_bracket = None
    winning_cid = None
    for m in ev.get("markets", []):
        q = m.get("question", "")
        parsed = parse_bracket(q)
        if not parsed: continue
        label, lo, hi, unit = parsed
        cid = m.get("conditionId", "")
        token_ids = m.get("clobTokenIds")
        if isinstance(token_ids, str):
            try: token_ids = json.loads(token_ids)
            except Exception: token_ids = []
        yes_token = token_ids[0] if token_ids else None
        if not yes_token or not cid: continue

        # YES outcome resolved? Polymarket returns outcomePrices as ['1','0'] if YES won
        op = m.get("outcomePrices")
        if isinstance(op, str):
            try: op = json.loads(op)
            except Exception: op = []
        if op and float(op[0]) >= 0.99 and bool(m.get("closed")):
            winning_bracket = label
            winning_cid = cid

        jobs.append((label, lo, hi, unit, cid, str(yes_token)))

    price_rows: list[dict] = []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(11, len(jobs))) as pool:
            futures = {pool.submit(fetch_clob_history, j[5]): j for j in jobs}
            for fut, job in futures.items():
                label, lo, hi, unit, cid, yes_token = job
                history = fut.result()
                for h in history:
                    ts = h.get("t"); p = h.get("p")
                    if ts is None or p is None: continue
                    price_rows.append({
                        "city": city,
                        "forecast_date": d.isoformat(),
                        "bracket_label": label,
                        "bracket_unit": unit,
                        "bracket_low_native": round(lo, 2) if lo > -9999 else None,
                        "bracket_high_native": round(hi, 2) if hi <  9999 else None,
                        "condition_id": cid,
                        "yes_token_id": yes_token,
                        "recorded_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        "yes_price": round(float(p), 4),
                    })

    n_prices = upsert_chunked("historical_bracket_prices", price_rows,
                              on_conflict="condition_id,recorded_at")

    # --- WU hourly observations for that day ---
    obs_rows = []
    day_max_f = None
    day_max_local_hour = None
    for ob in fetch_wu_hourly(city, d):
        temp_f = ob.get("temp")
        ts = ob.get("valid_time_gmt")
        if temp_f is None or ts is None: continue
        try:
            temp_f = float(temp_f); ts = int(ts)
        except Exception:
            continue
        if day_max_f is None or temp_f > day_max_f:
            day_max_f = temp_f
            # local hour of day-max (Wunderground returns valid_time_local for some endpoints)
            day_max_local_hour = ob.get("valid_time_local_hour")
        obs_rows.append({
            "city": city,
            "observed_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "temp_f": round(temp_f, 2),
            "temp_c": round((temp_f - 32) * 5 / 9, 3),
            "dew_pt_f": ob.get("dewPt"),
            "wind_mph": ob.get("wspd"),
            "pressure_mbar": ob.get("pressure"),
            "source": "wunderground",
            "station_icao": WU_STATIONS.get(city, ("?", "?"))[0],
        })
    n_obs = upsert_chunked("historical_temp_observations", obs_rows,
                            on_conflict="city,observed_at,source")

    # --- Resolution row ---
    resolved = 0
    if winning_bracket is not None or day_max_f is not None:
        sb.table("historical_event_resolutions").upsert({
            "city": city,
            "forecast_date": d.isoformat(),
            "winning_bracket_label": winning_bracket,
            "winning_condition_id": winning_cid,
            "day_max_temp_f": round(day_max_f, 2) if day_max_f is not None else None,
            "day_max_temp_c": round((day_max_f - 32) * 5 / 9, 3) if day_max_f is not None else None,
            "day_max_local_hour": day_max_local_hour,
            "source": "gamma+wunderground",
        }, on_conflict="city,forecast_date").execute()
        resolved = 1

    return (n_prices, n_obs, resolved)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="Days back from today")
    parser.add_argument("--city", type=str, default=None, help="Single city only")
    parser.add_argument("--date", type=str, default=None, help="Single date (YYYY-MM-DD)")
    args = parser.parse_args()

    today = date.today()
    if args.date:
        dates = [date.fromisoformat(args.date)]
    else:
        # Skip today (still active) — backfill is for resolved markets only.
        dates = [today - timedelta(days=i) for i in range(1, args.days + 1)]
    cities = [args.city] if args.city else list(CITY_SLUG.keys())
    log.info(f"Backfilling {len(cities)} cities × {len(dates)} days = {len(cities)*len(dates)} (city,date) pairs")

    totals = {"prices": 0, "obs": 0, "resolved": 0, "skipped": 0}
    start = time.time()

    # Within each day, process cities in parallel. Each event already does
    # its 11 CLOB fetches concurrently inside backfill_event(), so 2 events
    # × 11 = 22 max in-flight CLOB requests. We keep this LOWER than the
    # CLOB-side parallelism so Supabase's HTTP/2 layer doesn't bottleneck
    # (we observed ConnectionTerminated / COMPRESSION_ERROR at 4× events).
    EVENT_PARALLELISM = 2
    for di, d in enumerate(dates):
        with ThreadPoolExecutor(max_workers=EVENT_PARALLELISM) as pool:
            futures = {pool.submit(backfill_event, c, d): c for c in cities}
            for fut, c in futures.items():
                try:
                    n_p, n_o, n_r = fut.result()
                except Exception as e:
                    log.warning(f"backfill {c} {d} crashed: {e}")
                    n_p, n_o, n_r = 0, 0, 0
                totals["prices"] += n_p
                totals["obs"]    += n_o
                totals["resolved"] += n_r
                if n_p == 0 and n_o == 0 and n_r == 0:
                    totals["skipped"] += 1
        elapsed = time.time() - start
        log.info(f"[{di+1}/{len(dates)}] {d} done — prices={totals['prices']} obs={totals['obs']} resolved={totals['resolved']} skipped={totals['skipped']} elapsed={elapsed:.0f}s")

    log.info(f"DONE — prices={totals['prices']} obs={totals['obs']} resolved={totals['resolved']} skipped={totals['skipped']}")


if __name__ == "__main__":
    main()
