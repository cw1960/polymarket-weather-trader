"""
Quantify the gap between
  (A) max of v1 hourly observations  ← what our old reader returned
  (B) calendarDayTemperatureMax       ← what Polymarket actually resolves to

Runs across all 44 Wunderground-mapped cities for today.  Repeat daily
(via cron) to build up a per-city distribution: mean gap, sign, sample
count.  Persist results to a CSV/JSON for later analysis.

Why this matters
----------------
The two values can differ by 1-3°F.  Always in the direction of B > A
(because B incorporates SPECI / 1-min ASOS data that A's hourly-only
sampling silently drops).  That gap exactly matches the cold-bias
losses we cataloged on 2026-05-13..17.

Usage
-----
    python scripts/wunderground_bias_audit.py
    python scripts/wunderground_bias_audit.py --append /tmp/wu_bias_log.csv

When --append is set, each run writes one row per city (with a UTC
timestamp) into the named CSV.  A separate analysis script can then
compute rolling per-city statistics.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from datetime import date, datetime, timezone

import wunderground


logging.basicConfig(level=logging.WARNING, format="%(message)s")


def audit_today() -> list[dict]:
    """For every supported city: fetch A and B, return per-city deltas.

    Uses the CITY-LOCAL calendar date for the hourly fetch so we don't
    request tomorrow's data for cities west of UTC (which would 400).
    """
    from zoneinfo import ZoneInfo
    try:
        from config import CITY_TIMEZONES
    except Exception:
        CITY_TIMEZONES = {}
    rows = []
    for city in sorted(wunderground.STATIONS):
        tz = ZoneInfo(CITY_TIMEZONES.get(city, "UTC"))
        local_today = datetime.now(tz).date().isoformat()
        # Pull both sources independently (each can succeed/fail on its own).
        cdtm_c       = wunderground.fetch_calendar_day_max_c(city)
        obs          = wunderground._fetch_observations(city, local_today)
        temps_f      = [o["temp"] for o in (obs or []) if o.get("temp") is not None]
        hourly_max_c = (max(temps_f) - 32) * 5/9 if temps_f else None
        gap_c        = (cdtm_c - hourly_max_c) if (cdtm_c is not None and hourly_max_c is not None) else None
        rows.append({
            "ts_utc":         datetime.now(timezone.utc).isoformat(),
            "city":           city,
            "date":           local_today,
            "n_obs":          len(temps_f),
            "hourly_max_c":   round(hourly_max_c, 2) if hourly_max_c is not None else None,
            "calendar_max_c": round(cdtm_c, 2)       if cdtm_c       is not None else None,
            "gap_c":          round(gap_c, 2)        if gap_c        is not None else None,
            "hourly_max_f":   round(hourly_max_c*9/5+32, 1) if hourly_max_c is not None else None,
            "calendar_max_f": round(cdtm_c*9/5+32, 1)       if cdtm_c       is not None else None,
            "gap_f":          round(gap_c*9/5,         1) if gap_c        is not None else None,
        })
    return rows


def report(rows: list[dict]) -> None:
    """Pretty-print results, sorted by gap magnitude."""
    rows = [r for r in rows if r["gap_c"] is not None]
    rows.sort(key=lambda r: -abs(r["gap_c"]))

    print(f"\n=== Wunderground bias audit — {date.today().isoformat()} ===")
    print(f"  hourly_max  = max of api.weather.com /v1 hourly observations")
    print(f"  calendar_max = calendarDayTemperatureMax (what Polymarket resolves to)")
    print(f"  gap = calendar - hourly  (positive = hourly under-reports)\n")
    print(f"  {'CITY':18s} {'hourly':>8s} {'calendar':>10s}  {'gap':>7s}  obs")
    for r in rows:
        flag = ""
        if r["gap_c"] is not None:
            if abs(r["gap_c"]) >= 0.55: flag = "  ⚠ ≥1°F"
            elif abs(r["gap_c"]) >= 0.28: flag = "  ⚠ ≥0.5°F"
        print(f"  {r['city']:18s} "
              f"{(str(r['hourly_max_f'])+'°F'):>8s} "
              f"{(str(r['calendar_max_f'])+'°F'):>10s}  "
              f"{('+' if r['gap_f'] and r['gap_f']>0 else '')+str(r['gap_f'])+'°F':>7s}  "
              f"{r['n_obs']:>3d}{flag}")

    # Aggregate stats
    gaps = [r["gap_c"] for r in rows if r["gap_c"] is not None]
    if gaps:
        n = len(gaps)
        mean = sum(gaps) / n
        positive = sum(1 for g in gaps if g >= 0.05)
        negative = sum(1 for g in gaps if g <= -0.05)
        zero     = n - positive - negative
        max_gap = max(gaps)
        min_gap = min(gaps)
        print(f"\n  Aggregate over {n} cities:")
        print(f"    mean gap:                {mean:+.2f}°C  ({mean*9/5:+.2f}°F)")
        print(f"    cities with gap > +0.05°C: {positive}  (calendar > hourly)")
        print(f"    cities with gap < -0.05°C: {negative}  (calendar < hourly)")
        print(f"    cities with gap ≈ 0:       {zero}")
        print(f"    max:                       {max_gap:+.2f}°C ({max_gap*9/5:+.2f}°F)")
        print(f"    min:                       {min_gap:+.2f}°C ({min_gap*9/5:+.2f}°F)")


def append_csv(rows: list[dict], path: str) -> None:
    """Append today's rows to a longitudinal CSV for trend analysis."""
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        cols = ["ts_utc","city","date","n_obs","hourly_max_c","calendar_max_c","gap_c",
                "hourly_max_f","calendar_max_f","gap_f"]
        w = csv.DictWriter(fh, fieldnames=cols)
        if new: w.writeheader()
        for r in rows: w.writerow(r)
    print(f"\n  Appended {len(rows)} rows to {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", help="path to CSV; if set, append today's results")
    args = ap.parse_args()

    rows = audit_today()
    report(rows)
    if args.append:
        append_csv(rows, args.append)


if __name__ == "__main__":
    main()
