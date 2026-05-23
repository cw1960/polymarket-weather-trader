"""Fetch live temperature market prices from Polymarket Gamma API.

Market hierarchy:
  /series?slug={city}-daily-weather  →  series with nested events
  /events/{event_id}                 →  event with markets[]
  market.question                    →  parse temperature bucket boundaries
"""
import re
import time
import logging
import unicodedata
import requests
from datetime import datetime, timezone, timedelta

GAMMA_BASE = "https://gamma-api.polymarket.com"
log = logging.getLogger(__name__)

INF = 9999.0

# ── Bucket parsing ────────────────────────────────────────────────────────────
_BELOW_RE = re.compile(r"be (-?\d+)°([CF]) or below", re.IGNORECASE)
_RANGE_RE  = re.compile(r"be between (-?\d+)[\s\-–—]+(-?\d+)°([CF])", re.IGNORECASE)
_ABOVE_RE  = re.compile(r"be (-?\d+)°([CF]) or (?:above|higher)", re.IGNORECASE)
_EXACT_RE  = re.compile(r"be (-?\d+)°([CF])(?:\s|$|\?|\.)", re.IGNORECASE)


def parse_bucket(question: str) -> dict | None:
    """Parse a temperature market question into {label, low, high, unit}."""
    m = _BELOW_RE.search(question)
    if m:
        t, unit = int(m.group(1)), m.group(2).upper()
        return {"label": f"≤{t}°{unit}", "low": -INF, "high": t + 0.5, "unit": unit}

    m = _RANGE_RE.search(question)
    if m:
        lo, hi, unit = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        return {"label": f"{lo}-{hi}°{unit}", "low": lo - 0.5, "high": hi + 0.5, "unit": unit}

    m = _ABOVE_RE.search(question)
    if m:
        t, unit = int(m.group(1)), m.group(2).upper()
        return {"label": f"≥{t}°{unit}", "low": t - 0.5, "high": INF, "unit": unit}

    m = _EXACT_RE.search(question)
    if m:
        t, unit = int(m.group(1)), m.group(2).upper()
        return {"label": f"{t}°{unit}", "low": t - 0.5, "high": t + 0.5, "unit": unit}

    return None


# ── Slug derivation ───────────────────────────────────────────────────────────

def city_to_slug(city: str) -> str:
    """'São Paulo' → 'sao-paulo-daily-weather'"""
    nfkd = unicodedata.normalize("NFKD", city)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9\-]+", "-", ascii_str.lower().replace(" ", "-")).strip("-")
    return f"{base}-daily-weather"


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, retries: int = 2) -> dict | list | None:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1.0)
            else:
                log.debug(f"GET {url} failed: {e}")
                return None


# ── Series / event fetching ───────────────────────────────────────────────────

def fetch_series(slug: str) -> dict | None:
    data = _get(f"{GAMMA_BASE}/series", params={"slug": slug})
    if not data:
        return None
    if isinstance(data, list):
        for s in data:
            if s.get("slug") == slug:
                return s
        return data[0] if data else None
    return data


def _open_events(series: dict, hours: int = 48) -> list[dict]:
    """Return active, non-closed events from a series within the planning window.

    The `closed` and `resolved` flags are the authoritative signal — endDate is
    Polymarket's internal scheduling timestamp, NOT the trading close time.
    Daily-high temperature markets stay open for trading until the full day's
    data is finalised, which can be well after endDate noon-UTC.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    # Look back up to 48 h so same-day markets aren't missed after their endDate
    lookback = now - timedelta(hours=48)
    open_evs = []
    for ev in series.get("events", []):
        if ev.get("closed") or ev.get("resolved"):
            continue
        end_str = ev.get("endDate") or ev.get("endTime") or ""
        if not end_str:
            if ev.get("active"):
                open_evs.append(ev)
            continue
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            # Include if within our window: not too old AND not too far in future
            if lookback <= end_dt <= cutoff:
                open_evs.append(ev)
        except Exception:
            pass
    return open_evs


def _fetch_open_events_fallback(series_id: int | str) -> list[dict]:
    """Fallback: query /events directly if events not nested in the series object."""
    data = _get(
        f"{GAMMA_BASE}/events",
        params={"seriesId": series_id, "active": "true", "closed": "false", "limit": 5},
    )
    return data if isinstance(data, list) else []


def _event_date(ev: dict) -> str:
    """Extract YYYY-MM-DD resolution date from an event dict."""
    for key in ("endDate", "startDate", "endTime", "startTime"):
        val = ev.get(key, "")
        if val:
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
    title = ev.get("title") or ev.get("question") or ""
    m = re.search(r"(\w+ \d+(?:,?\s*\d{4})?)", title)
    if m:
        raw = m.group(1).strip()
        for fmt in ("%B %d %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        try:
            return datetime.strptime(raw + " 2026", "%B %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Price extraction ──────────────────────────────────────────────────────────

def _parse_yes_price(mkt: dict) -> float:
    """Extract YES price from a market dict. Handles JSON-string fields and None."""
    import json as _json

    # outcomePrices may be a Python list, a JSON string, or None
    raw_prices = mkt.get("outcomePrices")
    if isinstance(raw_prices, str):
        try:
            raw_prices = _json.loads(raw_prices)
        except Exception:
            raw_prices = None

    raw_outcomes = mkt.get("outcomes", ["Yes", "No"])
    if isinstance(raw_outcomes, str):
        try:
            raw_outcomes = _json.loads(raw_outcomes)
        except Exception:
            raw_outcomes = ["Yes", "No"]

    if raw_prices:
        try:
            yes_idx = next(
                i for i, o in enumerate(raw_outcomes) if str(o).lower() == "yes"
            )
            return float(raw_prices[yes_idx])
        except (StopIteration, IndexError, ValueError):
            try:
                return float(raw_prices[0])
            except (IndexError, ValueError):
                pass

    # Fallback: CLOB midpoint or last trade
    best_bid = mkt.get("bestBid") or 0.0
    best_ask = mkt.get("bestAsk") or 1.0
    last = mkt.get("lastTradePrice")
    if last:
        return float(last)
    if best_bid > 0 or best_ask < 1.0:
        return round((float(best_bid) + float(best_ask)) / 2, 4)
    return 0.5


# ── Main public API ───────────────────────────────────────────────────────────

def fetch_markets_for_city(city: str) -> list[dict]:
    """
    Return a list of market dicts for the city, one per open event (tomorrow/day after).

    Each dict:
      city, date, event_id, series_slug,
      buckets: [{label, low, high, unit, yes_price, no_price, condition_id, market_id}],
      outcomes: [{label, yes_price, no_price, condition_id, market_id}]  ← legacy compat
    """
    slug = city_to_slug(city)
    series = fetch_series(slug)
    if not series:
        return []

    open_evs = _open_events(series)
    if not open_evs:
        series_id = series.get("id")
        if series_id:
            open_evs = _fetch_open_events_fallback(series_id)
    if not open_evs:
        return []

    results = []
    for ev in open_evs:
        event_id = ev.get("id")
        if not event_id:
            continue

        markets_raw = ev.get("markets")
        if not markets_raw:
            ev_full = _get(f"{GAMMA_BASE}/events/{event_id}")
            markets_raw = (ev_full or {}).get("markets", [])
        if not markets_raw:
            continue

        buckets = []
        for mkt in markets_raw:
            question = mkt.get("question", "")
            bucket = parse_bucket(question)
            if not bucket:
                continue

            yes_price = _parse_yes_price(mkt)

            bucket["yes_price"] = round(yes_price, 4)
            bucket["no_price"] = round(1.0 - yes_price, 4)
            bucket["condition_id"] = mkt.get("conditionId", "")
            bucket["market_id"] = str(mkt.get("id", ""))
            bucket["question"] = question
            buckets.append(bucket)

        if not buckets:
            continue

        buckets.sort(key=lambda b: b["low"])
        date_str = _event_date(ev)
        event_slug = ev.get("slug", "")

        results.append({
            "city": city,
            "date": date_str,
            "event_id": str(event_id),
            "event_slug": event_slug,
            "series_slug": slug,
            "buckets": buckets,
            # Legacy field used by signal_engine.py
            "outcomes": [
                {
                    "label": b["label"],
                    "yes_price": b["yes_price"],
                    "no_price": b["no_price"],
                    "condition_id": b["condition_id"],
                    "market_id": b["market_id"],
                }
                for b in buckets
            ],
        })
        time.sleep(0.3)

    return results


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    test_cities = ["NYC", "Chicago", "London", "Hong Kong", "Seoul", "Singapore", "Lagos"]
    for city in test_cities:
        markets = fetch_markets_for_city(city)
        if not markets:
            print(f"{city}: no markets found (slug: {city_to_slug(city)})")
            continue
        for mkt in markets:
            print(f"\n{city} {mkt['date']} — {len(mkt['buckets'])} buckets (event {mkt['event_id']}):")
            for b in mkt["buckets"]:
                bar = "█" * max(1, int(b["yes_price"] * 30))
                print(f"  {b['label']:<12} YES={b['yes_price']:.3f}  {bar}")


if __name__ == "__main__":
    main()
