"""
backtest_walk_forward_intraday.py — same walk-forward backtest as
backtest_walk_forward_100d.py, but with INTRADAY CONDITIONING applied to
the NO sweep probabilities.

The question this answers: across 90 days of historical markets, does
intraday-conditioning the morning ensemble distribution on observed
running_max produce calibrated probabilities, AND would the gate have
fired meaningfully fewer of the 4/4-losing-style trades?

Method per (city, date):
  1. Pull historical Polymarket event → bracket structure + winner
  2. Pull historical 6-model deterministic forecast (synthetic ensemble) → mean
     and std for the day
  3. Pull historical hourly observed temperatures from Open-Meteo archive
  4. Simulate the bot firing NO sweep at SIMULATED_TRADE_HOUR_LOCAL local
     time. Compute simulated_running_max = max(observed temps up to that
     hour).
  5. Filter forecast members to those >= simulated_running_max
     (= the intraday fix)
  6. For each bracket: compute prob_yes from filtered set
  7. Apply gate (edge ≥ 0.08, prob_no ≥ 0.55)
  8. Calibration: bin prob_no, count actual NO wins

Compare results to backtest_walk_forward_100d.py (which used UNFILTERED
morning members) to quantify the impact of the fix.
"""
from __future__ import annotations
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, str(Path(__file__).parent))

from forecast_bias import compute_walk_forward_correction, ResolvedForecast  # noqa: E402
from config import CITY_UNITS, CITY_TIMEZONES  # noqa: E402
from wunderground import STATION_LATLON  # noqa: E402
from backtest_walk_forward_100d import (  # noqa: E402
    CITY_SLUG, fetch_polymarket_event, forecast_stats_c,
    extract_brackets_and_winner, bracket_prob_yes,
    parse_question, CACHE_DIR,
    WINDOW_DAYS, END_DATE, SKIP_DATES, MIN_TRAIN_N, CAP_BIAS_C,
    NEW_EDGE, NEW_MIN_PROB, _c_to_f, _f_to_c,
)

ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"
SIMULATED_TRADE_HOUR_LOCAL = 16   # bot would fire NO sweep around mid-afternoon local


def fetch_hourly_archive(lat: float, lon: float, d: date) -> list[Optional[float]] | None:
    """Return 24-element list of hourly °C temperatures for the date, or None."""
    cache_path = CACHE_DIR / f"hourly_{lat:.4f}_{lon:.4f}_{d.isoformat()}.json"
    if cache_path.exists():
        try:
            j = json.loads(cache_path.read_text())
            return j.get("hourly", {}).get("temperature_2m") if j.get("_found") else None
        except Exception:
            pass
    try:
        r = requests.get(ARCHIVE_BASE, params={
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m",
            "start_date": d.isoformat(), "end_date": d.isoformat(),
            "timezone": "UTC",
        }, timeout=25)
    except Exception:
        time.sleep(0.5)
        return None
    if not r.ok:
        cache_path.write_text(json.dumps({"_found": False}))
        return None
    j = r.json()
    j["_found"] = True
    cache_path.write_text(json.dumps(j))
    time.sleep(0.05)
    return j.get("hourly", {}).get("temperature_2m")


def simulated_running_max_c(hourly_c: list[Optional[float]], city: str, target_date: date) -> Optional[float]:
    """Compute what the bot would have observed as running_max at
    SIMULATED_TRADE_HOUR_LOCAL on this date, given the city's timezone.

    hourly_c is 24 entries indexed by UTC hour (00-23 UTC of target_date).
    We want the max of all observations from UTC 00:00 through the UTC
    hour corresponding to SIMULATED_TRADE_HOUR_LOCAL.
    """
    if not hourly_c:
        return None
    tz = CITY_TIMEZONES.get(city, "UTC")
    # Quick-and-dirty UTC offset table (handles DST imperfectly but adequate
    # for this analysis). For simplicity we approximate from the timezone name.
    # Better: use zoneinfo. But the archive returns UTC-indexed; we just need
    # to know which UTC hour = SIMULATED_TRADE_HOUR_LOCAL.
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        local_dt = _dt(target_date.year, target_date.month, target_date.day,
                       SIMULATED_TRADE_HOUR_LOCAL, 0, 0, tzinfo=ZoneInfo(tz))
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    except Exception:
        return None
    # If the local-time UTC equivalent is on a DIFFERENT date than target_date,
    # we can't compute running_max within this single day's array. Skip.
    if utc_dt.date() != target_date:
        return None
    utc_hour_inclusive = utc_dt.hour
    observed = [t for t in hourly_c[:utc_hour_inclusive + 1] if t is not None]
    if not observed:
        return None
    return max(observed)


def main() -> int:
    cities = list(CITY_SLUG.keys())
    start_date = END_DATE - timedelta(days=WINDOW_DAYS)
    print(f"Walk-forward backtest with INTRADAY CONDITIONING")
    print(f"  Window: {start_date} → {END_DATE}  ({WINDOW_DAYS} days)")
    print(f"  Simulated trade hour: {SIMULATED_TRADE_HOUR_LOCAL}:00 local")
    print()

    # Phase 1: build city history (same as before) + universe
    city_history: dict[str, list[ResolvedForecast]] = defaultdict(list)
    universe: list = []  # (city, d, brackets, winner_label, raw_mean_c, std_c, hourly_c)
    cells_total = cells_with_data = cells_with_winner = cells_with_hourly = 0

    print("Phase 1: fetching universe + hourly archives (this populates cache)")
    d = start_date
    while d <= END_DATE:
        if d in SKIP_DATES:
            d += timedelta(days=1); continue
        for city in cities:
            cells_total += 1
            stats = forecast_stats_c(city, d)
            if not stats: continue
            mean_c, std_c, _ = stats
            event = fetch_polymarket_event(city, d)
            if not event: continue
            brackets, winner_label = extract_brackets_and_winner(event, city)
            if not brackets: continue
            cells_with_data += 1
            coords = STATION_LATLON.get(city)
            if not coords: continue
            hourly_c = fetch_hourly_archive(coords[0], coords[1], d)
            if not hourly_c: continue
            cells_with_hourly += 1
            if winner_label:
                cells_with_winner += 1
                wb = next((b for b in brackets if b["label"] == winner_label), None)
                if wb:
                    mid_c = 0.5 * (wb["low_c"] + wb["high_c"])
                    if mid_c < -50: mid_c = wb["high_c"]
                    if mid_c >  60: mid_c = wb["low_c"]
                    city_history[city].append(ResolvedForecast(d, mean_c, mid_c))
            universe.append((city, d, brackets, winner_label, mean_c, std_c, hourly_c))
        if (d - start_date).days % 10 == 0:
            print(f"  ...{d}  cells_total={cells_total}  forecast+market+hourly={cells_with_hourly}  with_winner={cells_with_winner}")
        d += timedelta(days=1)

    print()
    print(f"Universe: {cells_with_hourly} cells with forecast+market+hourly data")
    print(f"  with winner (usable for calibration): {cells_with_winner}")
    print(f"  cities w/ training history: {len(city_history)}")

    # Phase 2: walk-forward + intraday-conditioned probabilities
    print()
    print("Phase 2: walk-forward simulation with intraday conditioning")
    fired = 0
    fired_won = 0
    fired_lost = 0
    fired_with_intraday_skip = 0   # cells where intraday filter blocked otherwise-firing trades
    no_intraday_obs = 0

    # Calibration bins for the NO-side gate-passing brackets
    bins: dict[float, list[int]] = defaultdict(lambda: [0, 0])  # [n, wins]

    # For diagnostic: count brackets where running_max already PAST the bracket
    # (so the morning-only backtest would have fired NO at hugely-stale prices)
    dead_bracket_fires_avoided = 0

    for (city, d, brackets, winner_label, raw_mean_c, std_c, hourly_c) in universe:
        if not winner_label:
            continue

        # Walk-forward bias
        bias_c, _n_train = compute_walk_forward_correction(
            city, d, city_history[city],
            min_samples=MIN_TRAIN_N, cap_abs=CAP_BIAS_C,
        )
        corrected_mean_c = raw_mean_c + bias_c

        # Synthesize ensemble members from mean+std (since deterministic
        # archive gives us only 6 models). Use a normal-distributed sample
        # of 82 around (mean, std).
        members = [corrected_mean_c + std_c * z
                   for z in [-2.5,-2.2,-2.0,-1.8,-1.6,-1.4,-1.2,-1.0,-0.9,-0.8,
                             -0.7,-0.6,-0.5,-0.4,-0.3,-0.2,-0.1, 0.0, 0.0, 0.1,
                              0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2,
                              1.4, 1.6, 1.8, 2.0, 2.2, 2.5]] * 2  # ~72 synthetic

        # Compute simulated running_max at trade time
        sim_rmax = simulated_running_max_c(hourly_c, city, d)
        if sim_rmax is None:
            no_intraday_obs += 1
            continue

        # Intraday filter
        filtered = [m for m in members if m >= sim_rmax]
        if len(filtered) < 8:
            # Forecast failure — skip the city (matches production behavior)
            continue
        n_filtered = len(filtered)

        # Evaluate each bracket
        candidates = []
        for b in brackets:
            low_c, high_c = b["low_c"], b["high_c"]
            yp = b.get("yes_price")
            # In historical Polymarket data, yes_price reflects POST-RESOLUTION
            # state; we can't use it for simulating P&L. But for the gate
            # calibration question, we don't need market price — we just
            # need prob_yes and the actual outcome.
            count_in = sum(1 for m in filtered if low_c <= m <= high_c)
            prob_yes = count_in / n_filtered
            prob_no  = 1.0 - prob_yes
            won_yes  = (b["label"] == winner_label)

            # Intraday "would have been blocked"?
            # In the morning-only backtest, prob_yes for this bracket would
            # have been higher if the temp was already past it. By filtering,
            # we set prob_yes ≈ 0 for "dead" brackets — which gives prob_no ≈ 1.
            # That's a near-certain NO trade. The trade still WINS (NO does
            # win since YES bracket is dead), so it's good — but the entry
            # price on the live market would be very close to 100¢ already.
            # We don't have historical market prices to compute the EV.
            #
            # Instead we record the model's win rate: when model says prob_no >= 0.55,
            # how often does NO actually win? That's the calibration test.
            if prob_no >= NEW_MIN_PROB:
                # No edge filter here because we have no market price.
                # Use it as a pure calibration check.
                bin_key = round(prob_no * 10) / 10
                bins[bin_key][0] += 1
                if not won_yes:
                    bins[bin_key][1] += 1
                candidates.append((prob_no, b["label"], won_yes))

        # Pretend we fire on top-3 by prob_no
        candidates.sort(reverse=True)
        for prob_no, label, won_yes in candidates[:3]:
            fired += 1
            won_no = not won_yes
            if won_no: fired_won += 1
            else:      fired_lost += 1

    print()
    print(f"Fired (top-3 by prob_no): {fired}")
    if fired:
        print(f"  wins:    {fired_won} / {fired} = {fired_won/fired:.1%}")
        print(f"  losses:  {fired_lost}")
    print(f"  cells skipped (no intraday obs / DST cross): {no_intraday_obs}")
    print()
    print("Calibration table (NO-side probs computed AFTER intraday filter)")
    print(f"  {'bucket':>6} | {'n':>6} | {'NO win rate':>12}")
    for k in sorted(bins):
        n, w = bins[k]
        if n >= 30:
            print(f"  {k:>6.1f} | {n:>6d} | {w/n:>11.1%}")
    print()
    print("Compare to backtest_walk_forward_100d.py (morning-only):")
    print("    0.7 → 59.6%   0.8 → 79.6%   0.9 → 93.6%   1.0 → 98.8%")
    print("  Healthy intraday-conditioned result should be:")
    print("    same or higher rates per bucket (we filter out 'losing' members up front)")
    print("    AND meaningfully more 1.0-bucket entries (dead brackets identified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
