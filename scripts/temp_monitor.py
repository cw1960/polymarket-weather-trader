"""
Phase 2 — Real-time Temperature Monitor
========================================
Runs every 5 minutes via cron.

For each city that has an open ladder for today's forecast date:
  1. Fetch current temperature from best available source (METAR → Open-Meteo).
  2. Update running daily maximum in temp_readings table.
  3. Compute bracket-lock confidence based on time of day + stability.
  4. When confidence >= threshold, call phase2_engine to place the confirmation trade.

Data source priority:
  Tier 1: METAR via aviationweather.gov (actual station observations)
  Tier 2: Open-Meteo current weather (model-based, universal fallback)
  Special: HKO API for Hong Kong (10-min updates)

Running the monitor manually:
  python scripts/temp_monitor.py
  python scripts/temp_monitor.py --city Tokyo        # single city debug
  python scripts/temp_monitor.py --dry-run           # no DB writes
"""
import math
import time
import logging
import argparse
import requests
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

from supabase import create_client
from config import (
    SUPABASE_URL, SUPABASE_KEY, CITY_UNITS, CITY_ICAO, CITY_TIMEZONES,
    PHASE2_MIN_CONFIDENCE, PHASE2_MIN_LOCAL_HOUR, PHASE2_STABLE_READINGS,
    OPENMETEO_FALLBACK_CITIES,
)
# Wunderground is the resolution source for 44 of our 50 Polymarket weather
# markets (proven 2026-05-17).  When a city has a Wunderground station mapping
# we read the daily max from the same backend (api.weather.com) that
# wunderground.com itself uses, so our running_max matches the value the
# market will resolve against.  See scripts/wunderground.py for details.
import wunderground

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

METAR_URL       = "https://aviationweather.gov/api/data/metar"
OPENMETEO_URL   = "https://api.open-meteo.com/v1/forecast"
HKO_URL         = "https://data.hko.gov.hk/api/v1/rhrread"
REQUEST_TIMEOUT = 10
REQUEST_DELAY   = 0.15   # seconds between API calls

# ── Station delta cache ───────────────────────────────────────────────────────
# Maps city → delta_c (float).  Positive = resolution station reads higher than
# our METAR/Open-Meteo source, so we add the delta before bracket matching.
# Populated lazily from resolution_stations; falls back to DEFAULT_DELTA_C.
_delta_cache: dict[str, float] = {}

# Default delta used only when no informative prior exists at all
# (typically a completely new install with no resolved trades).
DEFAULT_DELTA_C = 1.0

# Real-money calibration threshold. Lowered from 3 → 2 once the hierarchical
# Bayesian prior is in place — a single observation combined with a regional
# prior is informative enough to size up to $15.
CALIBRATION_MIN_SAMPLES = 2

# ── Hierarchical priors: climate/region groups ────────────────────────────────
# Each city belongs to a climate group. The group prior is the mean delta of
# its calibrated members (n >= 3). Cities use a weighted blend of their own
# observations and the group prior:
#     eff_delta = (n_city * raw_delta + K * group_prior) / (n_city + K)
# At n=0:    eff_delta = group_prior  (fully informed by group)
# At n=K:    eff_delta = midway between own data and prior
# At n>>K:   eff_delta ~ raw_delta   (own data dominates)
CITY_GROUPS: dict[str, list[str]] = {
    "europe_temperate":  ["Amsterdam", "Ankara", "Helsinki", "Istanbul", "London",
                           "Madrid", "Milan", "Munich", "Paris", "Warsaw"],
    "asia_subtropical":  ["Hong Kong", "Taipei", "Tokyo", "Seoul", "Busan"],
    "asia_continental":  ["Beijing", "Shanghai", "Chongqing", "Chengdu", "Wuhan"],
    "se_asia_tropical":  ["Singapore", "Kuala Lumpur", "Manila", "Jakarta",
                           "Guangzhou", "Shenzhen"],
    "mideast_africa":    ["Jeddah", "Tel Aviv", "Lagos", "Cape Town"],
    "us_humid":          ["NYC", "Chicago", "Miami", "Atlanta", "Houston", "Austin", "Dallas"],
    "us_dry":            ["Los Angeles", "San Francisco", "Seattle", "Denver"],
    "americas_other":    ["Toronto", "Mexico City", "São Paulo", "Buenos Aires", "Panama City"],
    "russia_cold":       ["Moscow"],
    "south_asia":        ["Lucknow", "Karachi"],
    "oceania":           ["Wellington"],
}
_city_to_group: dict[str, str] = {
    city: group for group, cities in CITY_GROUPS.items() for city in cities
}

# Module-level cache: group_id → prior_mean_delta, populated each cycle.
_group_priors: dict[str, float] = {}
_global_prior_mean: float = 0.0    # fallback when group has no calibrated members

# Prior strength: how many "effective samples" the prior is worth.
# Higher value → more shrinkage toward the prior, slower adaptation to local data.
PRIOR_STRENGTH = 5.0

# Bayesian shrinkage: small-sample deltas are noisy. Pull them toward zero
# until enough samples accumulate to trust the measured value.
#   effective_delta = (n / (n + K)) * raw_delta
# K is dynamic — adjusted by per-city delta variance:
#   K_adj = clamp(K_BASE * (σ_city / σ_global), 1, 10)
# Stable cities (low σ) → K → 1 (high trust in raw delta).
# Noisy cities (high σ) → K → 10 (heavy shrinkage toward zero).
# Cities with n < CALIBRATION_MIN_SAMPLES use K_BASE (warm-up protocol).
BAYESIAN_SHRINKAGE_K_BASE = 5

# Asymmetric boundary buffer: when adjusted_temp is within this distance ABOVE
# the lower bracket boundary (i.e. we just barely crossed up into a new bracket),
# bump the bet down to the previous bracket. This treats overshoots as worse
# than undershoots given our payout structure (cheap brackets pay 10x+).
#
# Conditional: only applied when σ_city >= STABILITY_THRESHOLD_C or when σ
# is unknown (n < 3). For very stable cities, the buffer over-corrects.
BOUNDARY_BUFFER_C = 0.3
STABILITY_THRESHOLD_C = 0.3   # σ_city below this → trust raw delta, skip buffer

# Module-level caches populated at the start of each run_monitor() cycle.
_sigma_cache: dict[str, float] = {}    # city → σ_city (°C). Missing = uncalibrated.
_sigma_global: float = 0.5             # median of all city sigmas (default until computed)


def _apply_bayesian_shrinkage(raw_delta: float, samples: int, k: float) -> float:
    """
    Shrink a raw delta toward zero based on sample count.
    Returns effective_delta = (n / (n + K)) * raw_delta.
    """
    if samples <= 0:
        return 0.0
    return (samples / (samples + k)) * raw_delta


def _compute_k_adjusted(city: str) -> float:
    """
    Compute the variance-adjusted K for this city. Cities with low delta
    variance (stable) get small K (high trust). Cities with high variance
    (noisy) get large K (heavy shrinkage toward zero).
    """
    sigma = _sigma_cache.get(city)
    if sigma is None or _sigma_global <= 0:
        return float(BAYESIAN_SHRINKAGE_K_BASE)
    ratio = sigma / _sigma_global
    return max(1.0, min(10.0, BAYESIAN_SHRINKAGE_K_BASE * ratio))


def _load_delta_variances() -> None:
    """
    Populate _sigma_cache and _sigma_global from resolved Phase 2 trades.
    Called once at the start of each temp_monitor cycle.

    σ_city = stdev of (resolution_temp - mean_high_at_lock) for each city
             that has at least CALIBRATION_MIN_SAMPLES resolved Phase 2 trades.
    σ_global = median of all city sigmas.
    """
    global _sigma_global
    import statistics
    import re
    from collections import defaultdict

    try:
        res = (sb.table("trade_signals")
               .select("city, mean_high, winning_bracket")
               .eq("signal_phase", "phase2")
               .not_.is_("pnl_usd", "null")
               .not_.is_("mean_high", "null")
               .limit(500)
               .execute())
        deltas: dict[str, list[float]] = defaultdict(list)
        for r in res.data or []:
            win_text = r.get("winning_bracket", "") or ""
            nums = re.findall(r"-?\d+", win_text)
            if not nums:
                continue
            actual_native = float(nums[0])
            mean_high = float(r["mean_high"])
            if mean_high == 0:
                continue
            unit = CITY_UNITS.get(r["city"], "C")
            actual_c = (actual_native - 32) * 5 / 9 if unit == "F" else actual_native
            deltas[r["city"]].append(actual_c - mean_high)

        sigmas = {c: statistics.stdev(d) for c, d in deltas.items()
                  if len(d) >= CALIBRATION_MIN_SAMPLES}
        _sigma_cache.clear()
        _sigma_cache.update(sigmas)
        if sigmas:
            _sigma_global = statistics.median(sigmas.values())
            log.info(
                f"Delta variance: σ_global = {_sigma_global:.3f}°C across "
                f"{len(sigmas)} cities (range {min(sigmas.values()):.3f} → {max(sigmas.values()):.3f})"
            )
    except Exception as e:
        log.warning(f"Delta variance load failed (using defaults): {e}")


def _compute_group_priors() -> None:
    """
    Populate _group_priors and _global_prior_mean.

    For each climate group, the prior is the mean delta_c of its members
    that have at least CALIBRATION_MIN_SAMPLES observations. Groups with
    no calibrated members fall through to the global prior, which is the
    mean of all calibrated deltas across all groups.

    Called once per temp_monitor cycle.
    """
    global _global_prior_mean
    try:
        res = sb.table("resolution_stations").select("city, delta_c, delta_samples").execute()
        deltas_by_city = {
            r["city"]: (float(r.get("delta_c") or 0.0), int(r.get("delta_samples") or 0))
            for r in res.data
        }
        _group_priors.clear()
        all_calibrated_deltas: list[float] = []
        for group, cities in CITY_GROUPS.items():
            members = [deltas_by_city[c][0] for c in cities
                       if c in deltas_by_city and deltas_by_city[c][1] >= 3]
            if members:
                _group_priors[group] = sum(members) / len(members)
                all_calibrated_deltas.extend(members)
        if all_calibrated_deltas:
            _global_prior_mean = sum(all_calibrated_deltas) / len(all_calibrated_deltas)
        else:
            _global_prior_mean = 0.0
        log.info(
            f"Hierarchical priors: global={_global_prior_mean:+.2f}°C, "
            f"groups={ {g: round(v,2) for g,v in _group_priors.items()} }"
        )
    except Exception as e:
        log.warning(f"Group prior computation failed: {e}")


def _get_city_delta(city: str) -> float:
    """
    Hierarchical Bayesian delta estimator with variance-adjusted shrinkage.

    Strategy:
      eff_delta = (n_city × raw_delta + K_eff × group_prior) / (n_city + K_eff)

    Where K_eff = K_adj (variance-adjusted from the city's σ history) — high
    variance cities are shrunk harder toward the prior, low variance cities
    trust their own data more.

    Tiers:
      n = 0:       eff = group_prior (fully informed by region)
      n = K_eff:   eff = midway between local and prior
      n >> K_eff:  eff ~ raw_delta (city-specific)

    Falls back to DEFAULT_DELTA_C only if there is literally no calibration
    information anywhere (first-time install).
    """
    if city in _delta_cache:
        return _delta_cache[city]

    # Look up the appropriate prior
    group = _city_to_group.get(city)
    if group and group in _group_priors:
        prior_mean = _group_priors[group]
    elif _global_prior_mean != 0.0 or len(_group_priors) > 0:
        prior_mean = _global_prior_mean
    else:
        # No priors at all — first-time install
        prior_mean = DEFAULT_DELTA_C

    # Fetch this city's own observations
    raw_delta: float = 0.0
    raw_samples: int = 0
    try:
        res = (sb.table("resolution_stations")
               .select("delta_c, delta_samples")
               .eq("city", city)
               .limit(1)
               .execute())
        if res.data:
            rd = res.data[0].get("delta_c")
            raw_delta = float(rd) if rd is not None else 0.0
            raw_samples = int(res.data[0].get("delta_samples") or 0)
    except Exception:
        pass

    if raw_samples <= 0:
        eff_delta = prior_mean
    else:
        k = _compute_k_adjusted(city)
        eff_delta = (raw_samples * raw_delta + k * prior_mean) / (raw_samples + k)

    _delta_cache[city] = eff_delta
    sigma = _sigma_cache.get(city)
    log.debug(
        f"  {city}: n={raw_samples} raw={raw_delta:+.2f} "
        f"prior={prior_mean:+.2f} ({group or 'global'}) "
        f"σ={sigma if sigma is not None else 'N/A'} → eff={eff_delta:+.2f}°C"
    )
    return eff_delta


def _should_apply_buffer(city: str) -> bool:
    """
    Conditional boundary buffer policy:
      - Cities with σ_city >= STABILITY_THRESHOLD_C (noisy): apply buffer
      - Cities with σ_city < STABILITY_THRESHOLD_C (stable): skip buffer
      - Cities with no σ data (uncalibrated, n<3): apply buffer (warm-up)

    This prevents over-correction on stable cities (Ankara, Warsaw) where
    shrinkage already pulls us close to bracket centers, while preserving
    overshoot protection on noisy cities (Hong Kong, Madrid).
    """
    sigma = _sigma_cache.get(city)
    if sigma is None:
        return True   # warm-up: no variance data yet, use buffer for safety
    # FP epsilon so 0.2999... still passes >= 0.30
    return sigma >= STABILITY_THRESHOLD_C - 1e-6


# ── Temperature fetchers ──────────────────────────────────────────────────────

def fetch_temp_metar(icao: str) -> float | None:
    """Fetch current temperature (°C) from latest METAR observation."""
    try:
        r = requests.get(METAR_URL, params={
            "ids": icao, "format": "json", "taf": "false",
        }, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            return None
        data = r.json()
        if not data:
            return None
        obs = data[0]
        temp = obs.get("temp")
        return float(temp) if temp is not None else None
    except Exception as e:
        log.debug(f"  METAR {icao} error: {e}")
        return None


def fetch_metar_full(icao: str) -> dict | None:
    """
    Fetch full METAR observation including temperature and sky conditions.
    Returns {temp_c, sky_condition} or None if unavailable.

    Sky condition is the highest sky cover code from the clouds array:
      OVC > BKN > SCT > FEW > SKC
    OVC/BKN means the sun is obscured — strong signal that temperature has peaked.
    """
    try:
        r = requests.get(METAR_URL, params={
            "ids": icao, "format": "json", "taf": "false",
        }, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            return None
        data = r.json()
        if not data:
            return None
        obs = data[0]
        temp = obs.get("temp")
        if temp is None:
            return None
        # Pick highest cloud cover from the clouds array
        SKY_RANK = {"SKC": 0, "CLR": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4}
        sky = None
        sky_rank = -1
        for c in obs.get("clouds", []) or []:
            cov = c.get("cover", "").upper()
            if cov in SKY_RANK and SKY_RANK[cov] > sky_rank:
                sky = cov
                sky_rank = SKY_RANK[cov]
        return {"temp_c": float(temp), "sky_condition": sky}
    except Exception as e:
        log.debug(f"  METAR full {icao} error: {e}")
        return None


def fetch_temp_openmeteo(lat: float, lon: float) -> float | None:
    """Fetch current temperature (°C) from Open-Meteo (model-based, 15-min cycle)."""
    try:
        r = requests.get(OPENMETEO_URL, params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m",
            "forecast_days": 1,
        }, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            return None
        return float(r.json()["current"]["temperature_2m"])
    except Exception as e:
        log.debug(f"  Open-Meteo ({lat},{lon}) error: {e}")
        return None


def fetch_temp_hko() -> float | None:
    """
    Fetch current temperature at HK Observatory King's Park (10-min updates).
    HKO API may be geo-restricted; returns None on failure (falls back to Open-Meteo).
    """
    try:
        r = requests.get(HKO_URL, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            return None
        data = r.json()
        # HKO rhrread returns temperature array; station 'HKO' = King's Park
        for entry in data.get("temperature", {}).get("data", []):
            if entry.get("station") == "HKO":
                return float(entry["value"])
        return None
    except Exception:
        return None


def get_city_temp(city: str, lat: float, lon: float) -> tuple[float | None, str, str | None]:
    """
    Fetch current temperature for a city using best available source.
    Returns (temp_c, source_name, sky_condition).
    sky_condition is one of {SKC, CLR, FEW, SCT, BKN, OVC} or None if unavailable.
    """
    # Special case: Hong Kong Observatory King's Park
    if city == "Hong Kong":
        t = fetch_temp_hko()
        if t is not None:
            return t, "hko", None
        # Fall through to Open-Meteo

    # Tier 1: METAR (with full obs including clouds)
    icao = CITY_ICAO.get(city)
    if icao:
        full = fetch_metar_full(icao)
        if full is not None:
            return full["temp_c"], "metar", full.get("sky_condition")
        log.debug(f"  {city}: METAR {icao} unavailable, falling back to Open-Meteo")

    # Tier 2: Open-Meteo (universal fallback) — no cloud data
    t = fetch_temp_openmeteo(lat, lon)
    if t is not None:
        return t, "openmeteo", None

    return None, "none", None


# ── Bracket-lock confidence ───────────────────────────────────────────────────

def _bracket_confidence(
    local_hour: int,
    stable_readings: int,
    sky_condition: str | None = None,
    trend_flat: bool = False,
) -> float:
    """
    Estimate confidence that running_max represents the final daily high.

    Factors:
      time_confidence:      how late in the day it is (daily max rarely after 6 PM)
      stability_confidence: how many consecutive 5-min readings at the same running_max
      sky_boost:            BKN/OVC sky obscures the sun → temp unlikely to rise further
      trend_flat:           last N raw readings show flat or declining trend

    Returns float [0.0, 1.0].
    """
    # Time of day confidence (local city time)
    if local_hour < PHASE2_MIN_LOCAL_HOUR:
        time_conf = 0.0
    elif local_hour < 15:
        time_conf = 0.45
    elif local_hour < 16:
        time_conf = 0.65
    elif local_hour < 17:
        time_conf = 0.80
    elif local_hour < 18:
        time_conf = 0.90
    else:
        time_conf = 0.97

    # Stability: PHASE2_STABLE_READINGS consecutive readings at running_max = full confidence
    stability_conf = min(stable_readings / PHASE2_STABLE_READINGS, 1.0)

    # Sky cover boost — overcast/broken cloud strongly suggests temp has peaked.
    # +0.05 if BKN, +0.08 if OVC (capped overall via min).
    sky_boost = 0.0
    if sky_condition == "BKN":
        sky_boost = 0.05
    elif sky_condition == "OVC":
        sky_boost = 0.08

    # Trend penalty/boost: if the last few raw readings show a rising trend,
    # cap confidence below the lock threshold to prevent premature locks.
    if not trend_flat and stable_readings >= PHASE2_STABLE_READINGS:
        # Stable per running_max but raw readings still creeping up — hold off
        return round(min(time_conf * 0.55 + stability_conf * 0.45 + sky_boost, 0.79), 3)

    base = time_conf * 0.55 + stability_conf * 0.45
    return round(min(base + sky_boost, 1.0), 3)


def _is_trend_flat(recent_temps: list[float], current_temp: float) -> bool:
    """
    Return True if the last several raw readings show a flat or declining trend.

    Slope test: temperature change from the oldest reading in the window to
    the current reading must be <= 0.0°C. Tightened on 2026-05-17 from
    <=0.1°C after the cluster of premature-lock losses: the old 0.1°C slop
    let a "creeping rise" pattern (22.1 → 22.2 → 22.3) read as flat while
    temp was still genuinely climbing. Combined with the PHASE2_STABLE_READINGS
    bump 12 → 24 (60 → 120 min plateau) this hardens the lock criterion
    materially.

    Requires at least 6 readings (30 minutes) of history. Returns True if not
    enough history (insufficient data → don't penalize).
    """
    if not recent_temps or len(recent_temps) < 6:
        return True   # not enough data, give benefit of the doubt
    # Compare oldest of the recent window to the current reading
    oldest = recent_temps[0]
    if current_temp - oldest > 0.0:
        return False   # any net rise across the window = not flat
    return True


# ── DB read/write ─────────────────────────────────────────────────────────────

def get_today_reading(city: str, today: str) -> dict | None:
    """Fetch today's running_max record from DB (None if first reading of day)."""
    try:
        res = (sb.table("temp_readings")
               .select("*")
               .eq("city", city)
               .eq("reading_date", today)
               .limit(1)
               .execute())
        return res.data[0] if res.data else None
    except Exception:
        return None


def upsert_reading(
    city: str, today: str, temp_c: float, running_max_c: float,
    stable_readings: int, local_hour: int, confidence: float,
    source: str, bracket_locked: bool, locked_bracket: str | None,
    phase2_triggered: bool,
    recent_temps: list[float] | None = None,
    sky_condition: str | None = None,
    dry_run: bool = False,
) -> None:
    """Upsert one temp_readings row (unique per city + reading_date)."""
    if dry_run:
        return
    row = {
        "city":             city,
        "reading_date":     today,
        "observed_at":      datetime.now(timezone.utc).isoformat(),
        "temp_c":           round(temp_c, 2),
        "running_max_c":    round(running_max_c, 2),
        "source":           source,
        "stable_readings":  stable_readings,
        "local_hour":       local_hour,
        "confidence":       confidence,
        "bracket_locked":   bracket_locked,
        "locked_bracket":   locked_bracket,
        "phase2_triggered": phase2_triggered,
    }
    # Optional new columns (recent_temps_json, sky_condition).
    # Migration: scripts/migrate_recent_temps.sql must be applied to the DB.
    # If columns don't exist yet, retry without them so the system keeps running.
    if recent_temps is not None:
        import json as _json
        row["recent_temps_json"] = _json.dumps(recent_temps)
    if sky_condition is not None:
        row["sky_condition"] = sky_condition

    try:
        sb.table("temp_readings").upsert(row, on_conflict="city,reading_date").execute()
    except Exception as e:
        # If the new columns don't exist yet, drop them and retry
        if "recent_temps_json" in str(e) or "sky_condition" in str(e):
            row.pop("recent_temps_json", None)
            row.pop("sky_condition", None)
            sb.table("temp_readings").upsert(row, on_conflict="city,reading_date").execute()
            log.warning(
                f"  {city}: temp_readings missing new columns — run migrate_recent_temps.sql "
                f"to enable rate-of-change and cloud cover features"
            )
        else:
            raise


# ── Per-city monitor logic ────────────────────────────────────────────────────

def _find_locked_bracket(running_max_c: float, city: str, buckets: list[dict]) -> str | None:
    """
    Return the bracket label that running_max_c falls into, or None.
    Buckets come from fetch_markets_for_city and have 'low','high','label','unit'.

    Two-stage selection:

    1. Station-delta correction (with Bayesian shrinkage): we add the
       calibrated delta_c to the METAR running_max so we target the bracket
       Wunderground will actually resolve in.

    2. Asymmetric boundary buffer: if the adjusted_temp lands within
       BOUNDARY_BUFFER_C above the lower edge of its bracket (i.e. we just
       barely crossed up into the bracket), bump the bet DOWN to the
       previous bracket. This prevents overshoot losses where small noise
       in the delta correction pushes us into the wrong bracket.

       Asymmetric because under our payout structure (cheap brackets pay
       10x+), a $45 overshoot loss costs more than a missed undershoot win.
    """
    unit = CITY_UNITS.get(city, "C")

    # Apply station bias before bucket comparison (delta stored in °C)
    delta_c = _get_city_delta(city)
    adjusted_c = running_max_c + delta_c

    # Convert to the market unit for comparison
    val = adjusted_c * 9 / 5 + 32 if unit == "F" else adjusted_c
    buffer = BOUNDARY_BUFFER_C * (9 / 5) if unit == "F" else BOUNDARY_BUFFER_C

    log.info(
        f"  {city}: bracket lookup with delta correction "
        f"{running_max_c:.1f}°C + {delta_c:+.2f}°C = {adjusted_c:.1f}°C → {val:.1f}°{unit}"
    )

    INF = 9000.0
    # Sort buckets by low edge so we can find the previous bracket
    sorted_buckets = sorted(buckets, key=lambda b: b.get("low", -INF))

    apply_buffer = _should_apply_buffer(city)
    sigma = _sigma_cache.get(city)
    sigma_str = f"σ={sigma:.2f}°C" if sigma is not None else "σ=N/A"

    for i, b in enumerate(sorted_buckets):
        low  = b.get("low",  -INF)
        high = b.get("high",  INF)
        if low <= val < high:
            # Conditional boundary buffer: only applied to noisy cities (σ>=0.3)
            # or uncalibrated cities (warm-up). Stable cities trust raw delta.
            distance_above_low = val - low
            if (apply_buffer and 0 <= distance_above_low <= buffer + 1e-6
                    and i > 0):
                prev = sorted_buckets[i - 1]
                prev_label = prev.get("label") or prev.get("outcome") or f"{val:.0f}{unit}"
                log.info(
                    f"  {city}: boundary buffer triggered ({sigma_str}) — "
                    f"adjusted {val:.1f}°{unit} only {distance_above_low:.1f}° "
                    f"above bracket low {low:.1f}°{unit}; "
                    f"bumping down to '{prev_label}' (overshoot protection)"
                )
                return prev_label
            elif (not apply_buffer and 0 <= distance_above_low <= buffer + 1e-6
                  and i > 0):
                # Stable city — would have buffered, but trusting raw delta
                log.debug(
                    f"  {city}: stable city ({sigma_str}) — buffer skipped "
                    f"(adjusted {val:.1f}°{unit} {distance_above_low:.1f}° above boundary)"
                )
            return b.get("label") or b.get("outcome") or f"{val:.0f}{unit}"
    return None


def monitor_city(
    city: str, lat: float, lon: float, forecast_date: str,
    buckets: list[dict], dry_run: bool = False,
) -> dict:
    """
    Single city monitor cycle.

    `forecast_date` is the city's LOCAL date that the ladder targets.  We
    use it as the per-day key for temp_readings so daily aggregates roll
    over at the city's local midnight, not UTC midnight.  Earlier this
    function used `date.today().isoformat()` (UTC date) — that caused
    US-west-coast cities to start a fresh "day" 7 hours before their
    actual local day ended, polluting the next day's running_max with
    evening readings from the previous local day.
    """
    today = forecast_date

    # Local time for this city
    tz   = ZoneInfo(CITY_TIMEZONES.get(city, "UTC"))
    now_local = datetime.now(tz)
    local_hour = now_local.hour

    # Fetch current temperature + sky condition
    temp_c, source, sky_condition = get_city_temp(city, lat, lon)
    if temp_c is None:
        log.warning(f"  {city}: no temperature available")
        return {"city": city, "status": "no_data"}

    time.sleep(REQUEST_DELAY)

    # Load or initialise today's record
    existing = get_today_reading(city, today)
    if existing:
        prev_max       = float(existing["running_max_c"])
        prev_stable    = int(existing["stable_readings"])
        already_locked = bool(existing["bracket_locked"])
        already_trigg  = bool(existing["phase2_triggered"])
        # Load recent_temps history (may be missing if migration not yet applied)
        import json as _json
        try:
            raw_recent = existing.get("recent_temps_json")
            prev_recent = _json.loads(raw_recent) if raw_recent else []
        except Exception:
            prev_recent = []
    else:
        prev_max       = temp_c
        prev_stable    = 0
        already_locked = False
        already_trigg  = False
        prev_recent    = []

    # ── Wunderground override (the 44 cities that resolve from there) ────
    # For supported cities, ignore the METAR-derived running_max entirely
    # and use Wunderground's own daily-max value as the source of truth.
    # This eliminates the inter-hour-spike bias that previously cost real
    # money on cities like Houston (METAR running_max=86.4°F but
    # Wunderground=85°F → ≥86°F bet was a guaranteed loss).
    wu_snap = None
    if wunderground.supports(city):
        try:
            wu_snap = wunderground.fetch_live_snapshot(city, target_date=today)
        except Exception as _wu_err:
            log.warning(f"  {city}: Wunderground snapshot failed: {_wu_err}; using METAR")
            wu_snap = None

    if wu_snap is not None:
        # Authoritative: replace temp_c, running_max with Wunderground's view.
        # We keep the METAR sky_condition because Wunderground's `clds` field
        # is unreliable for some non-US stations; sky_condition only feeds the
        # _bracket_confidence boost and isn't part of the lock-temp math.
        temp_c       = wu_snap["temp_c"]
        running_max  = wu_snap["running_max_c"]
        source       = "wunderground"
        # Stable counter: increment iff running_max didn't move vs the prior cycle
        # AND we're past the minimum local hour.  Same semantics as METAR.
        TOLERANCE    = 0.1
        if running_max > prev_max + TOLERANCE:
            stable_count = 0  # Wunderground bumped a new high → reset
        else:
            stable_count = prev_stable + 1 if local_hour >= PHASE2_MIN_LOCAL_HOUR else 0
        # Use Wunderground's own hourly history for trend detection — it's
        # what the market sees.  Falls back to the rolling 12 if snapshot
        # arrived empty (rare).
        recent_temps = wu_snap["recent_temps_c"] or (prev_recent + [round(temp_c, 2)])[-12:]
        trend_flat = _is_trend_flat(recent_temps, temp_c)
    else:
        # ── METAR / Open-Meteo / HKO path (Istanbul, Moscow, Tel Aviv,
        # Hong Kong, São Paulo, Panama City — the cities without
        # Wunderground station mappings) ─────────────────────────────
        TOLERANCE = 0.1   # °C — readings within this band count as "same"
        if temp_c > prev_max + TOLERANCE:
            running_max   = temp_c
            stable_count  = 0   # new high → reset stability
        else:
            running_max   = prev_max
            # Count as stable only after the minimum hour threshold
            stable_count  = prev_stable + 1 if local_hour >= PHASE2_MIN_LOCAL_HOUR else 0

        # Track last 12 raw readings for rate-of-change detection
        recent_temps = (prev_recent + [round(temp_c, 2)])[-12:]
        trend_flat = _is_trend_flat(recent_temps, temp_c)

    confidence = _bracket_confidence(
        local_hour, stable_count,
        sky_condition=sky_condition,
        trend_flat=trend_flat,
    )
    bracket_locked = (
        confidence >= PHASE2_MIN_CONFIDENCE
        and stable_count >= PHASE2_STABLE_READINGS  # hard gate: must have ≥N consecutive readings
        and trend_flat                              # hard gate: temperature trend must be flat/declining
        and local_hour >= PHASE2_MIN_LOCAL_HOUR
        and not already_trigg   # don't re-lock once Phase 2 fired
    )

    locked_bracket = (
        _find_locked_bracket(running_max, city, buckets)
        if bracket_locked or already_locked
        else None
    )

    sky_str = f" sky={sky_condition}" if sky_condition else ""
    trend_str = "" if trend_flat else " ↑rising"
    log.info(
        f"  {city}: {temp_c:.1f}°C (src={source}{sky_str}) | "
        f"running_max={running_max:.1f}°C | "
        f"stable={stable_count}{trend_str} | local={local_hour:02d}h | "
        f"conf={confidence:.2f} | {'🔒 LOCKED → ' + str(locked_bracket) if bracket_locked else 'watching'}"
    )

    upsert_reading(
        city=city, today=today, temp_c=temp_c, running_max_c=running_max,
        stable_readings=stable_count, local_hour=local_hour,
        confidence=confidence, source=source,
        bracket_locked=bracket_locked or already_locked,
        locked_bracket=locked_bracket or existing.get("locked_bracket") if existing else locked_bracket,
        phase2_triggered=already_trigg,
        recent_temps=recent_temps,
        sky_condition=sky_condition,
        dry_run=dry_run,
    )

    # Post-lock exit simulation (shadow mode — no real trades)
    if not dry_run and already_trigg:
        try:
            from exit_sim import check_for_exit_events
            check_for_exit_events(city, today, running_max)
        except Exception as e:
            log.debug(f"  [Exit Sim] {city} error: {e}")

    return {
        "city":          city,
        "temp_c":        temp_c,
        "running_max_c": running_max,
        "confidence":    confidence,
        "bracket_locked": bracket_locked and not already_trigg,
        "locked_bracket": locked_bracket,
        "status":        "ok",
    }


# ── Main runner ───────────────────────────────────────────────────────────────

def run_monitor(cities: list[str] | None = None, dry_run: bool = False):
    """
    Monitor temperature for all cities with open ladders for today.
    Calls phase2_engine for any newly locked brackets.
    """
    from fetch_markets import fetch_markets_for_city

    log.info(f"=== TEMP MONITOR === {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC | dry_run={dry_run}")

    # Reset per-cycle caches, compute hierarchical priors and delta variances
    _delta_cache.clear()
    _compute_group_priors()
    _load_delta_variances()

    # Check fill status of any pending live orders before processing cities
    if not dry_run:
        try:
            from executor import check_and_update_orders
            check_and_update_orders()
        except Exception as _e:
            log.warning(f"  Fill-check error: {_e}")

    # Get ALL currently-open ladders.  We then match each ladder against
    # the CITY'S LOCAL DATE (not UTC date).  A ladder for forecast_date=D
    # is active only when its city's local date == D.  This is the fix for
    # the timezone bug that caused west-coast cities to roll over their
    # daily running_max 7 hours before their local day actually ended.
    open_ladders = (sb.table("ladders")
                    .select("city,forecast_date")
                    .eq("status", "open")
                    .execute())

    # Build [(city, forecast_date)] for ladders whose city's local date
    # currently matches the ladder's forecast_date.
    active_pairs: list[tuple[str, str]] = []
    skipped_off_day: list[str] = []
    for r in (open_ladders.data or []):
        c  = r["city"]
        fd = r["forecast_date"]
        try:
            tz = ZoneInfo(CITY_TIMEZONES.get(c, "UTC"))
            city_local_date = datetime.now(tz).date().isoformat()
        except Exception:
            city_local_date = date.today().isoformat()
        if city_local_date == fd:
            active_pairs.append((c, fd))
        else:
            skipped_off_day.append(f"{c}({fd}≠{city_local_date})")

    if cities:
        active_pairs = [p for p in active_pairs if p[0] in cities]

    if not active_pairs:
        msg = "  No open ladders match a city's local date — nothing to monitor."
        if skipped_off_day:
            msg += f" (off-day ladders pending: {', '.join(skipped_off_day[:10])})"
        log.info(msg)
        return

    active_cities = sorted({c for c, _ in active_pairs})
    log.info(f"  Monitoring {len(active_pairs)} city/date pairs: "
             f"{', '.join(f'{c}({fd})' for c, fd in sorted(active_pairs))}")

    # Fetch station coords
    coords_res = (sb.table("resolution_stations")
                  .select("city,lat,lon")
                  .in_("city", active_cities)
                  .execute())
    coords = {r["city"]: (float(r["lat"]), float(r["lon"])) for r in coords_res.data}

    newly_locked = []

    for city, ladder_forecast_date in active_pairs:
        if city not in coords:
            log.warning(f"  {city}: no station coords, skipping")
            continue

        lat, lon = coords[city]

        # Load buckets: prefer cached buckets_json stored in the ladder row,
        # then fall back to the live Polymarket API.  Match the ladder by
        # this city's specific forecast_date, not a global "today".
        import json as _json
        buckets = []
        try:
            ladder_res = (sb.table("ladders")
                          .select("buckets_json")
                          .eq("city", city)
                          .eq("forecast_date", ladder_forecast_date)
                          .eq("status", "open")
                          .limit(1)
                          .execute())
            raw = ladder_res.data[0].get("buckets_json") if ladder_res.data else None
            if raw:
                buckets = _json.loads(raw)
        except Exception as e:
            log.warning(f"  {city}: bucket DB load error — {e}")

        if not buckets:
            # Fallback: fetch from live Polymarket API
            try:
                markets = fetch_markets_for_city(city) or []
                buckets = [
                    b
                    for mkt in markets
                    if mkt.get("date") == ladder_forecast_date
                    for b in mkt.get("buckets", [])
                ]
            except Exception as e:
                log.warning(f"  {city}: bucket API fallback error — {e}")

        result = monitor_city(
            city=city, lat=lat, lon=lon,
            forecast_date=ladder_forecast_date, buckets=buckets,
            dry_run=dry_run,
        )
        # Remember which date this result was for so the Phase 2 trigger
        # uses the same date the monitor cycle worked on.
        result["forecast_date"] = ladder_forecast_date

        if result.get("bracket_locked") and result.get("locked_bracket"):
            newly_locked.append(result)

    # Trigger Phase 2 for all newly locked cities
    if newly_locked:
        log.info(f"\n  🔒 {len(newly_locked)} bracket(s) locked — triggering Phase 2 engine")
        try:
            import phase2_engine
            for city_result in newly_locked:
                phase2_engine.execute_phase2(
                    city=city_result["city"],
                    forecast_date=city_result["forecast_date"],
                    locked_bracket=city_result["locked_bracket"],
                    running_max_c=city_result["running_max_c"],
                    confidence=city_result["confidence"],
                    dry_run=dry_run,
                )
        except Exception as e:
            log.error(f"  Phase 2 engine error: {e}")

    # ── Stale YES position handling (added 2026-05-21) ────────────────────
    # When running_max climbs past a locked YES bracket, the bracket is dead
    # and the YES token is heading to $0. We sell at the current best bid to
    # recover what's left.
    try:
        from stale_yes_detector import find_stale_yes_positions, native_temp_str
        from executor import sell_position
        stale = find_stale_yes_positions()
        if stale:
            log.info(f"\n  ⚠️  {len(stale)} stale YES position(s) detected — selling to recover")
            for s in stale:
                log.info(f"    {s.city} '{s.locked_bracket}'  "
                         f"rmax={native_temp_str(s.running_max_c, s.city)} > "
                         f"bracket_high={native_temp_str(s.bracket_high_c, s.city)}")
                if not dry_run:
                    result = sell_position(s.signal_id)
                    log.info(f"      sell result: {result}")
    except Exception as e:
        log.warning(f"  Stale-YES detector error: {e}")

    # ── Per-city NO sweep (independent of Phase 2 YES locks) ─────────────
    # Added 2026-05-21. The senior-dev review and the agreed week-1 strategy
    # both require NO sweep to evaluate every city every cycle (with all the
    # function's internal gates — local hour, already_swept_today, guardrails,
    # paper-trade-mode-when-blocked — doing the filtering inside). Previously
    # NO sweep only ran as a side effect of YES locks, which produced near-
    # zero bracket_evaluations data and missed the week-1 NO-only strategy
    # entirely.
    #
    # Each call to _execute_no_sweep is internally idempotent (uses
    # already_swept_today and writes only on first eligible firing), so
    # iterating every cycle is safe.
    try:
        import phase2_engine as _p2
        sweep_attempted = 0
        sweep_returned  = 0
        for city, ladder_forecast_date in active_pairs:
            try:
                results = _p2._execute_no_sweep(
                    city=city,
                    forecast_date=ladder_forecast_date,
                    running_max_c=0.0,    # unused by new member-count path; kept for signature compat
                    delta_c=0.0,
                    dry_run=dry_run,
                )
                sweep_attempted += 1
                if results:
                    sweep_returned += len(results)
            except Exception as _swe:
                log.warning(f"  NO sweep error for {city}: {_swe}")
        if sweep_attempted:
            log.info(f"  NO sweep: {sweep_attempted} cities evaluated, {sweep_returned} bracket-trades produced")
    except Exception as e:
        log.error(f"  NO sweep loop error: {e}")

    log.info("Done.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-time temperature bracket monitor")
    parser.add_argument("--city", nargs="*", help="Specific city or cities to monitor")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes or Phase 2 trades")
    args = parser.parse_args()
    run_monitor(cities=args.city, dry_run=args.dry_run)
