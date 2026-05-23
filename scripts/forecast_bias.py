"""
Forecast bias corrections — applied to ensemble forecast means before
bracket-probability computation.

==============================================================================
HISTORY (READ BEFORE TOUCHING)
==============================================================================
Until 2026-05-18 the bot applied corrections from the `delta_matrix` table
to forecast means via fetch_forecasts.py:

    corrected_mean = mean + delta_mean  # WRONG

`delta_matrix.delta_mean` was actually computed as
    resolution_station_temp - comparison_station_temp
in compute_delta_matrix.py.  That's a STATION-TO-STATION temperature offset
on historical observation days — NOT a forecast bias.  Adding it to a
forecast mean made cold-biased forecasts colder for many US cities
(Atlanta -0.91, Chicago -0.88, NYC -1.47, LA -2.08 in May 2026).

The bug inverted Phase 1's calibration: the higher the model_probability,
the LOWER the actual win rate.  Brier skill score was -0.25 (worse than
guessing the base rate).  Phase 1 win rate ran at 34% — essentially random.

==============================================================================
THIS MODULE
==============================================================================
Holds the CORRECT signal: per-city median of
    (actual_winning_bracket_mid_c - stored_forecast_mean_c)
computed from historical resolved markets where Polymarket's winning bracket
was recorded.  Applied in fetch_forecasts.py as:

    corrected_mean = mean + get_correction(city)

Positive correction = make forecast warmer (forecasts under-predict).

Safety rules built into the values below:
  - require n_samples >= 5; otherwise correction = 0.0
  - cap absolute correction at ±2.0°C
  - manually overrode Denver (n=5 outlier, direction inconsistent with neighbors)
    and Tel Aviv (weather.gov resolution source — separate pipeline issue) to 0.0

Backtest (2026-05-18) showed:
  - Top-1 bracket pick per (city, date) would-be win rate: 49.6% (vs 34% live)
  - Brier skill score: -0.10 (vs -0.25 with delta_matrix)
  - Calibration: now under-confident at high p (predict 25%, actual 49%)
    rather than INVERTED (predict 25%, actual 18%).

The corrections are NOT validated against post-deploy live data yet.  Per
CLAUDE.md Rule 3 they remain "candidate fixes" until at least 7 resolved
markets in the new regime show calibration is preserved.

==============================================================================
RE-COMPUTATION
==============================================================================
Run scripts/recompute_forecast_bias.py to regenerate these constants from
fresh historical data.  Re-run weekly as new resolved markets accumulate
so corrections track seasonality.
"""
from __future__ import annotations

# Per-city forecast bias corrections in °C.
# Generated 2026-05-18 from historical (winning_bracket_mid - forecast_mean).
# Use scripts/recompute_forecast_bias.py to refresh.
FORECAST_BIAS_C: dict[str, float] = {
    "Amsterdam":     -0.10,
    "Ankara":        +1.57,
    "Atlanta":        0.00,  # n=1, insufficient
    "Austin":         0.00,  # n=3, insufficient (raw -3.65 is suspicious)
    "Beijing":        0.00,  # n=4, insufficient
    "Buenos Aires":  +0.81,
    "Busan":          0.00,  # n=3, insufficient
    "Cape Town":     +1.25,
    "Chengdu":       +2.00,  # capped from +2.40
    "Chicago":        0.00,  # n=3, insufficient
    "Chongqing":      0.00,  # n=4, insufficient
    "Dallas":         0.00,  # n=2, insufficient
    "Denver":         0.00,  # hardcoded — n=5 with -4.04, direction inconsistent
    "Guangzhou":      0.00,  # n=3, insufficient
    "Helsinki":      +1.87,
    "Hong Kong":     -0.04,
    "Houston":        0.00,  # n=1, insufficient
    "Istanbul":       0.00,  # n=4, insufficient + weather.gov source
    "Jakarta":       +1.54,
    "Jeddah":         0.00,  # n=0, no data
    "Karachi":        0.00,  # n=0, no data
    "Kuala Lumpur":  +2.00,  # capped from +2.06
    "Lagos":         +1.31,
    "London":        -0.20,
    "Los Angeles":    0.00,  # n=2, insufficient
    "Lucknow":        0.00,  # n=0, no data
    "Madrid":        +0.66,
    "Manila":         0.00,  # n=0, no data
    "Mexico City":    0.00,  # n=1, insufficient
    "Miami":         -0.24,
    "Milan":         -0.15,
    "Moscow":        +0.93,
    "Munich":         0.00,  # n=4, insufficient
    "NYC":           +2.00,  # capped from +2.45
    "Panama City":    0.00,  # n=0, no data
    "Paris":         +0.55,
    "San Francisco":  0.00,  # n=0, no data
    "Seattle":        0.00,  # n=4, insufficient (just below threshold)
    "Seoul":          0.00,  # n=4, insufficient
    "Shanghai":      +2.00,  # capped from +2.36
    "Shenzhen":       0.00,  # n=3, insufficient
    "Singapore":     +1.39,
    "São Paulo":     -0.04,
    "Taipei":         0.00,  # n=4, insufficient
    "Tel Aviv":       0.00,  # hardcoded — weather.gov source, needs separate audit
    "Tokyo":         +0.23,
    "Toronto":       -0.85,
    "Warsaw":        +1.23,
    "Wellington":    +1.03,
    "Wuhan":         +1.54,
}


def get_correction(city: str) -> float:
    """Return the forecast-mean bias correction for a city, in °C.

    Positive value means add to forecast mean (forecast was cold-biased).
    Cities without enough historical data (or with manually-overridden
    values) return 0.0 — no correction applied.
    """
    return FORECAST_BIAS_C.get(city, 0.0)


def get_all_corrections() -> dict[str, float]:
    """Snapshot of all corrections — for diagnostics / logging."""
    return dict(FORECAST_BIAS_C)


# ── Walk-forward variant (for the 100-day backtest only) ─────────────────────
#
# Per the 2026-05-20 senior-dev review, the hardcoded FORECAST_BIAS_C dict
# above is computed in-sample from all resolved markets — that means the
# value used to predict (city, T) is partly trained on the actual outcome
# of (city, T). That is leakage, and is one explanation for why the
# in-sample backtest looked optimistic.
#
# The function below recomputes the correction using ONLY samples where
# forecast_date < as_of_date. It does not touch the DB — the caller passes
# the training data in. The 100-day backtest assembles training_data once
# from a single historical pull, then re-uses it per backtest day.
#
# This function is INTENTIONALLY NOT wired into the live pipeline yet.
# The live bot still calls get_correction() and reads the static dict.
# Once the walk-forward backtest validates, we'll bake recompute into a
# weekly cron and have get_correction() read from a fresh table.

from dataclasses import dataclass
from datetime import date as _date


@dataclass(frozen=True)
class ResolvedForecast:
    """One historical (city, date) row used to train the bias correction.
    `raw_forecast_mean_c` is the model output BEFORE any bias is applied.
    `winning_bracket_mid_c` is the mid-temperature of the bracket that
    actually won at resolution."""
    forecast_date:         _date
    raw_forecast_mean_c:   float
    winning_bracket_mid_c: float


def compute_walk_forward_correction(
    city: str,
    as_of_date: _date,
    city_history: list[ResolvedForecast],
    min_samples: int = 5,
    cap_abs:     float = 2.0,
) -> tuple[float, int]:
    """
    Return (correction_c, n_samples) using only `city_history` rows where
    forecast_date < as_of_date.

    correction = median(winning_bracket_mid - raw_forecast_mean)
    capped at ±cap_abs.

    Returns (0.0, 0) when n_samples < min_samples — same safety rule as the
    in-sample dict.
    """
    relevant = [r for r in city_history if r.forecast_date < as_of_date]
    n = len(relevant)
    if n < min_samples:
        return (0.0, n)
    deltas = sorted(r.winning_bracket_mid_c - r.raw_forecast_mean_c for r in relevant)
    if n % 2 == 1:
        median = deltas[n // 2]
    else:
        median = (deltas[n // 2 - 1] + deltas[n // 2]) / 2.0
    return (max(-cap_abs, min(cap_abs, median)), n)
