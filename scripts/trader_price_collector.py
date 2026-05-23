"""
trader_price_collector.py — minute-cadence price snapshot collector for
the manual-trading Trader app.

Pulls last-trade prices for every active weather event on Polymarket,
joins with the latest temperature observation per city, and writes one
row per bracket per cycle to bracket_price_history.

Runs from cron every minute:
  * * * * * cd /root/polymarket && venv/bin/python3 scripts/trader_price_collector.py >> logs/trader_collector.log 2>&1

Independent from the existing bot. Doesn't modify any other table.
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path("/root/polymarket/.env"))

from supabase import create_client  # noqa: E402

import sys as _sys
_sys.path.insert(0, str(Path("/root/polymarket/scripts")))
try:
    from config import CITY_TIMEZONES  # type: ignore
except Exception:
    CITY_TIMEZONES = {}

_url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
_key = os.environ["SUPABASE_SERVICE_KEY"]
sb = create_client(_url, _key)

log = logging.getLogger("trader_collector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S UTC")
logging.Formatter.converter = time.gmtime

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Same city → Polymarket slug map used elsewhere in the codebase.
CITY_SLUG = {
    "NYC": "nyc", "Chicago": "chicago", "Miami": "miami",
    "Los Angeles": "los-angeles", "Dallas": "dallas", "Atlanta": "atlanta",
    "Houston": "houston", "Austin": "austin", "Seattle": "seattle",
    "San Francisco": "san-francisco", "Denver": "denver",
    "London": "london", "Paris": "paris", "Madrid": "madrid",
    "Munich": "munich", "Milan": "milan", "Amsterdam": "amsterdam",
    "Warsaw": "warsaw", "Helsinki": "helsinki",
    "Istanbul": "istanbul", "Ankara": "ankara", "Moscow": "moscow",
    "Tel Aviv": "tel-aviv", "Jeddah": "jeddah",
    "Hong Kong": "hong-kong", "Seoul": "seoul", "Tokyo": "tokyo",
    "Busan": "busan", "Taipei": "taipei",
    "Beijing": "beijing", "Shanghai": "shanghai", "Guangzhou": "guangzhou",
    "Shenzhen": "shenzhen", "Chengdu": "chengdu", "Chongqing": "chongqing",
    "Wuhan": "wuhan", "Singapore": "singapore",
    "Kuala Lumpur": "kuala-lumpur", "Manila": "manila", "Jakarta": "jakarta",
    "Lucknow": "lucknow", "Karachi": "karachi", "Wellington": "wellington",
    "Toronto": "toronto", "Mexico City": "mexico-city",
    "São Paulo": "sao-paulo", "Buenos Aires": "buenos-aires",
    "Panama City": "panama-city", "Cape Town": "cape-town", "Lagos": "lagos",
}


_RANGE_RE = re.compile(r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°([fc])", re.IGNORECASE)
_LE_RE    = re.compile(r"(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+below",  re.IGNORECASE)
_GE_RE    = re.compile(r"(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+higher", re.IGNORECASE)
_SINGLE_RE = re.compile(r"be\s+(-?\d+(?:\.\d+)?)\s*°([fc])\s+on", re.IGNORECASE)


def parse_bracket(question: str) -> tuple[str, float, float, str] | None:
    """Return (label, low_native, high_native, unit) or None.
    Bounds use the half-degree window that matches WU's whole-degree rounding."""
    if not question: return None
    m = _RANGE_RE.search(question)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        u = m.group(3).upper()
        # bracket "86-87°F" covers measurements rounding to 86 or 87, so [85.5, 87.5)
        return (f"{int(lo)}-{int(hi)}°{u}", lo - 0.5, hi + 0.5, u)
    m = _LE_RE.search(question)
    if m:
        v = float(m.group(1)); u = m.group(2).upper()
        return (f"≤{int(v)}°{u}", float("-inf"), v + 0.5, u)
    m = _GE_RE.search(question)
    if m:
        v = float(m.group(1)); u = m.group(2).upper()
        return (f"≥{int(v)}°{u}", v - 0.5, float("inf"), u)
    m = _SINGLE_RE.search(question)
    if m:
        v = float(m.group(1)); u = m.group(2).upper()
        return (f"{int(v)}°{u}", v - 0.5, v + 0.5, u)
    return None


def fetch_event(slug: str) -> dict | None:
    try:
        r = requests.get(f"{GAMMA_BASE}/events/slug/{slug}", timeout=12)
        return r.json() if r.ok else None
    except Exception:
        return None


def temp_lookup() -> dict[str, dict]:
    """Map city → {temp_c, running_max_c, observed_at, local_hour} for today."""
    today_iso = date.today().isoformat()
    try:
        r = (sb.table("temp_readings")
             .select("city, temp_c, running_max_c, observed_at, local_hour, reading_date")
             .eq("reading_date", today_iso)
             .execute())
    except Exception as e:
        log.warning(f"temp_readings query failed: {e}")
        return {}
    return {row["city"]: row for row in (r.data or [])}


def main():
    today = date.today()
    cities = list(CITY_SLUG.keys())
    # Fetch events for today, today+1, today+2 — covers everything resolving in
    # the next 24-48h plus today's still-trading markets
    target_dates = [today, today + timedelta(days=1), today + timedelta(days=2)]
    temps = temp_lookup()
    now_utc = datetime.now(timezone.utc)
    rows_to_insert: list[dict] = []
    events_seen = 0
    events_missing = 0

    for city in cities:
        slug_city = CITY_SLUG[city]
        # City-specific temp data (only available for today)
        t = temps.get(city, {})
        temp_c = t.get("temp_c")
        run_max_c = t.get("running_max_c")
        local_hour = t.get("local_hour")

        # Resolution-time estimate: Polymarket weather markets resolve when
        # WU finalizes the daily history for the airport's local date. That
        # happens AFTER the local day ends. Empirically (NYC 5/19, Tokyo 5/19),
        # Polymarket closes the market 1-3 hours after the local day ends.
        # We use END-OF-LOCAL-DAY in UTC + 2h buffer as the resolution_ts
        # estimate. Much more accurate than the prior "12:00 UTC of d+1".
        tz_name = CITY_TIMEZONES.get(city, "UTC")
        try:
            city_tz = ZoneInfo(tz_name)
        except Exception:
            city_tz = ZoneInfo("UTC")

        for d in target_dates:
            date_slug = d.strftime("%B-%-d-%Y").lower()
            event_slug = f"highest-temperature-in-{slug_city}-on-{date_slug}"
            ev = fetch_event(event_slug)
            if ev is None:
                events_missing += 1
                continue
            events_seen += 1
            closed = bool(ev.get("closed"))
            # End-of-local-day in the city's timezone (23:59:59), converted to UTC.
            # Then add a 2h finalization buffer.
            end_of_local = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=city_tz)
            resolution_ts = end_of_local.astimezone(timezone.utc) + timedelta(hours=2)
            ttr_minutes = int((resolution_ts - now_utc).total_seconds() // 60)

            for m in ev.get("markets", []):
                q = m.get("question", "")
                parsed = parse_bracket(q)
                if not parsed: continue
                label, low_n, high_n, unit = parsed
                op = m.get("outcomePrices")
                if isinstance(op, str):
                    try: op = json.loads(op)
                    except Exception: op = []
                yes_p = float(op[0]) if op else None
                no_p  = float(op[1]) if op and len(op) >= 2 else (1 - yes_p if yes_p is not None else None)
                best_bid = m.get("bestBid")
                best_ask = m.get("bestAsk")
                try:
                    best_bid = float(best_bid) if best_bid is not None else None
                    best_ask = float(best_ask) if best_ask is not None else None
                except Exception:
                    best_bid = None; best_ask = None
                spread_pct = None
                if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask > 0:
                    mid = (best_bid + best_ask) / 2
                    spread_pct = (best_ask - best_bid) / mid if mid > 0 else None

                rows_to_insert.append({
                    "city":                       city,
                    "forecast_date":              d.isoformat(),
                    "condition_id":               m.get("conditionId", ""),
                    "market_id":                  m.get("conditionId", ""),
                    "bracket_label":              label,
                    "bracket_unit":               unit,
                    "bracket_low_native":         round(low_n, 2)  if low_n  > -9999 else None,
                    "bracket_high_native":        round(high_n, 2) if high_n <  9999 else None,
                    "yes_price":                  round(yes_p, 4) if yes_p is not None else None,
                    "no_price":                   round(no_p, 4)  if no_p  is not None else None,
                    "best_bid":                   round(best_bid, 4) if best_bid is not None else None,
                    "best_ask":                   round(best_ask, 4) if best_ask is not None else None,
                    "bid_size_usd":               None,    # populated by separate orderbook poller on Page 2
                    "ask_size_usd":               None,
                    "spread_pct":                 round(spread_pct, 4) if spread_pct is not None else None,
                    "observed_temp_c":            float(temp_c) if temp_c is not None else None,
                    "observed_running_max_c":     float(run_max_c) if run_max_c is not None else None,
                    "local_hour":                 int(local_hour) if local_hour is not None else None,
                    "time_to_resolution_minutes": ttr_minutes,
                    "market_closed":              closed,
                })

    log.info(f"Collected {events_seen} events ({events_missing} missing) → {len(rows_to_insert)} bracket rows")
    if not rows_to_insert:
        log.info("Nothing to insert."); return

    # Insert in chunks of 500 to keep PostgREST happy
    CHUNK = 500
    inserted = 0
    for i in range(0, len(rows_to_insert), CHUNK):
        try:
            sb.table("bracket_price_history").insert(rows_to_insert[i:i+CHUNK]).execute()
            inserted += len(rows_to_insert[i:i+CHUNK])
        except Exception as e:
            log.warning(f"Insert chunk {i} failed: {e}")
    log.info(f"Inserted {inserted}/{len(rows_to_insert)} rows")


if __name__ == "__main__":
    # Cron fires once per minute. We used to loop 4× per cycle to get ~15s
    # snapshot cadence, but that wrote 4×1584 = ~6.3k rows/min = 9M rows/day,
    # depleting Supabase's disk-IO budget. The Trade Station UI gets its
    # live ticks from gamma-api DIRECTLY at 2s (useLivePolymarketEvent),
    # which doesn't touch Supabase. So this collector only needs to provide
    # the historical-chart spine — 1×/min is plenty.
    main()
