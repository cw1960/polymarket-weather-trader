"""
stale_yes_detector.py — find Phase 2 YES positions that are physically dead.

A Phase 2 YES position is "stale" when the running_max temperature has
climbed PAST the locked bracket's upper bound. That means the bracket can
no longer be the final daily high → the YES token is heading to $0 unless
we sell what's left of it on the orderbook.

Used by:
  • temp_monitor.py (every 5 min cron) to detect stale positions and hand
    them to executor.sell_position() for recovery
  • Mission Control dashboard (read-only listing)

Output: list of dicts, each with:
  {signal_id, condition_id, city, forecast_date, locked_bracket, side,
   fill_price, filled_size_usd, bracket_high_c, running_max_c,
   over_bracket_by_c}

Does NOT modify the DB. The sell path (executor.sell_position) is the
write side.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from supabase import create_client

_log = logging.getLogger(__name__)

_url = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY", "")
_sb  = create_client(_url, _key) if (_url and _key) else None


# ── Bracket label → (low_c, high_c) parsing ──────────────────────────────────
# Bracket labels in trade_signals.outcome look like:
#   "66-67°F"  (range)
#   "≤75°F"    (tail: high cap)
#   "≥85°F"    (tail: low floor; no upper bound → never stale)
#   "24°C"     (single 1°C bracket)

_RE_RANGE_F  = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_RE_RANGE_C  = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")
_RE_LE_F     = re.compile(r"^\s*[≤<]=?\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_RE_LE_C     = re.compile(r"^\s*[≤<]=?\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")
_RE_GE_F     = re.compile(r"^\s*[≥>]=?\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_RE_GE_C     = re.compile(r"^\s*[≥>]=?\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")
_RE_SINGLE_F = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_RE_SINGLE_C = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")


def _f_to_c(t: float) -> float:
    return (t - 32.0) * 5.0 / 9.0


def parse_bracket_bounds_c(label: str) -> Optional[tuple[float, float]]:
    """Return (low_c, high_c) for a bracket label, or None if unparseable.
    high_c = +inf means the bracket has no upper bound (≥X°). Such brackets
    are NEVER stale."""
    if not label:
        return None
    s = label.strip()
    m = _RE_RANGE_F.match(s)
    if m: return (_f_to_c(float(m.group(1))), _f_to_c(float(m.group(2))))
    m = _RE_RANGE_C.match(s)
    if m: return (float(m.group(1)), float(m.group(2)))
    m = _RE_LE_F.match(s)
    if m: return (float("-inf"), _f_to_c(float(m.group(1))))
    m = _RE_LE_C.match(s)
    if m: return (float("-inf"), float(m.group(1)))
    m = _RE_GE_F.match(s)
    if m: return (_f_to_c(float(m.group(1))), float("inf"))
    m = _RE_GE_C.match(s)
    if m: return (float(m.group(1)), float("inf"))
    m = _RE_SINGLE_F.match(s)
    if m:
        t = float(m.group(1))
        return (_f_to_c(t - 0.5), _f_to_c(t + 0.5))
    m = _RE_SINGLE_C.match(s)
    if m:
        t = float(m.group(1))
        return (t - 0.5, t + 0.5)
    return None


# ── Detector ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StalePosition:
    signal_id:        str
    condition_id:     str
    city:             str
    forecast_date:    str
    locked_bracket:   str
    fill_price:       float        # what we paid (entry price)
    filled_size_usd:  float        # actual capital deployed
    bracket_high_c:   float
    running_max_c:    float
    over_bracket_by_c: float       # how far past the bracket's top we are


def find_stale_yes_positions(forecast_date: Optional[str] = None) -> list[StalePosition]:
    """Return all stale real-money YES positions for the given forecast_date
    (defaults to today UTC). A position is stale when:

      • signal_phase = 'phase2'
      • side = 'YES'
      • order_status = 'filled' (we own the tokens)
      • winning_bracket is NULL (market hasn't resolved yet)
      • running_max_c > bracket.high (temperature has climbed past it)
    """
    if _sb is None:
        return []
    today = forecast_date or datetime.now(timezone.utc).date().isoformat()

    try:
        ts = (_sb.table("trade_signals")
              .select("id, condition_id, city, forecast_date, outcome, fill_price, filled_size_usd")
              .eq("forecast_date", today)
              .eq("signal_phase", "phase2")
              .eq("side", "YES")
              .eq("order_status", "filled")
              .is_("winning_bracket", "null")
              .gt("filled_size_usd", 1)
              .execute())
    except Exception as e:
        _log.warning(f"stale_yes_detector: trade_signals query failed: {e}")
        return []

    candidates = ts.data or []
    if not candidates:
        return []

    # Fetch running_max per city in one query
    cities = list({c["city"] for c in candidates})
    try:
        tr = (_sb.table("temp_readings")
              .select("city, running_max_c")
              .in_("city", cities)
              .eq("reading_date", today)
              .execute())
    except Exception as e:
        _log.warning(f"stale_yes_detector: temp_readings query failed: {e}")
        return []
    running_max_by_city: dict[str, float] = {}
    for row in (tr.data or []):
        v = row.get("running_max_c")
        if v is not None:
            running_max_by_city[row["city"]] = float(v)

    stale: list[StalePosition] = []
    for c in candidates:
        bracket = c.get("outcome") or ""
        bounds = parse_bracket_bounds_c(bracket)
        if not bounds:
            _log.debug(f"stale_yes_detector: unparseable bracket '{bracket}' on signal {c['id']}")
            continue
        _, high_c = bounds
        if high_c == float("inf"):
            continue   # ≥X tail bracket — never stale
        rmax = running_max_by_city.get(c["city"])
        if rmax is None:
            continue   # no observation yet, can't tell
        if rmax > high_c:
            stale.append(StalePosition(
                signal_id=str(c["id"]),
                condition_id=str(c.get("condition_id") or ""),
                city=c["city"],
                forecast_date=c["forecast_date"],
                locked_bracket=bracket,
                fill_price=float(c.get("fill_price") or 0),
                filled_size_usd=float(c.get("filled_size_usd") or 0),
                bracket_high_c=high_c,
                running_max_c=rmax,
                over_bracket_by_c=rmax - high_c,
            ))
    return stale


def native_temp_str(c_value: float, city: str) -> str:
    """Honest display: WU publishes whole °F (US) or whole °C (rest).
    Internal °C is fractional only as an artifact of unit conversion."""
    try:
        from config import CITY_UNITS
    except Exception:
        CITY_UNITS = {}
    u = CITY_UNITS.get(city, "C")
    if u == "F":
        return f"{round(c_value * 9 / 5 + 32):d}°F"
    return f"{round(c_value):d}°C"


if __name__ == "__main__":
    # CLI for inspection — show today's stale positions
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stale = find_stale_yes_positions()
    if not stale:
        print("No stale Phase 2 YES positions found for today.")
    else:
        print(f"Found {len(stale)} stale Phase 2 YES position(s):")
        for s in stale:
            print(
                f"  {s.city.ljust(15)} '{s.locked_bracket}'  "
                f"running_max={native_temp_str(s.running_max_c, s.city)}  "
                f"bracket_high={native_temp_str(s.bracket_high_c, s.city)}  "
                f"fill={s.fill_price:.3f}  size=${s.filled_size_usd:.2f}  "
                f"signal_id={s.signal_id}"
            )
