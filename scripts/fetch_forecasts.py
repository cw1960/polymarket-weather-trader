"""
Fetch multi-model ensemble forecasts and store in ensemble_forecasts.

Sources (all free via Open-Meteo):
  GFS ensemble     — 31 members  (gfs_seamless on ensemble API)
  ECMWF IFS025     — 51 members  (ecmwf_ifs025_ensemble on ensemble API)
  6 deterministic  — GFS, ECMWF, ICON, MeteoFrance, UKMO, GEM
                     → consensus_spread_c = confidence signal for sizing

Combined 82-member array replaces the old Gaussian mean/std approach.
Bucket probabilities are computed by direct member counting in ladder.py.

DB migration required before first run:
  scripts/migrate_ensemble_v2.sql
"""
import math
import time
import requests
from datetime import datetime, timezone, date, timedelta
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, CITY_UNITS
# Per-city forecast bias corrections — replaces the misused delta_matrix.
# See scripts/forecast_bias.py for full history of the delta_matrix bug.
from forecast_bias import get_correction as get_forecast_bias

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

ENSEMBLE_URL      = "https://ensemble-api.open-meteo.com/v1/ensemble"
DETERMINISTIC_URL = "https://api.open-meteo.com/v1/forecast"

STD_FLOOR_C = 1.5   # raised from 1.0 — mean abs error was 1.25C, 1.0 was overconfident
STD_FLOOR_F = 2.7   # equivalent: 1.5C * 9/5 = 2.7F (was 1.8F)

CONSENSUS_MODELS = [
    "gfs_seamless",
    "ecmwf_ifs025",
    "icon_seamless",
    "meteofrance_seamless",
    "ukmo_seamless",
    "gem_seamless",
]

# Lowered 2026-05-21 from 6h → 0.5h so the new hourly forecast-refresh cron
# (added at :30 of each hour) can refetch when data is older than 30 minutes.
# Rate-limit budget: 50 cities × 8 API calls × 24 hourly runs = 9,600 calls/day,
# just under Open-Meteo's 10K daily free-tier limit.
FORECAST_FRESHNESS_HOURS = 0.5
REQUEST_DELAY = 0.2


# ── helpers ───────────────────────────────────────────────────────────────────

def _members_from_daily(data: dict) -> dict[str, list[float | None]]:
    """Extract {date: [all_member_values]} from an Open-Meteo daily ensemble response."""
    times = data.get("daily", {}).get("time", [])
    all_keys = [k for k in data.get("daily", {}) if "temperature_2m_max" in k]
    result: dict[str, list] = {t: [] for t in times}
    for key in all_keys:
        for t, v in zip(times, data["daily"][key]):
            result[t].append(v)
    return result


def _clean(vals: list) -> list[float]:
    return [float(v) for v in vals if v is not None]


# ── API fetchers ──────────────────────────────────────────────────────────────

def fetch_gfs_ensemble(lat: float, lon: float, days: int = 3) -> dict[str, list[float]]:
    """GFS 31-member ensemble daily highs. Returns {date_str: [temp_c, ...]}."""
    try:
        r = requests.get(ENSEMBLE_URL, params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max",
            "models": "gfs_seamless",
            "forecast_days": days, "timezone": "UTC",
        }, timeout=30)
        r.raise_for_status()
        raw = _members_from_daily(r.json())
        return {d: _clean(v) for d, v in raw.items()}
    except Exception as e:
        print(f"    GFS ensemble error: {e}")
        return {}


def fetch_ecmwf_ensemble(lat: float, lon: float, days: int = 3) -> dict[str, list[float]]:
    """ECMWF IFS025 51-member ensemble daily highs. Returns {date_str: [temp_c, ...]}."""
    try:
        r = requests.get(ENSEMBLE_URL, params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max",
            "models": "ecmwf_ifs025_ensemble",
            "forecast_days": days, "timezone": "UTC",
        }, timeout=30)
        r.raise_for_status()
        raw = _members_from_daily(r.json())
        return {d: _clean(v) for d, v in raw.items()}
    except Exception as e:
        print(f"    ECMWF ensemble error: {e}")
        return {}


def fetch_consensus(lat: float, lon: float, days: int = 3) -> dict[str, dict[str, float]]:
    """6 deterministic models. Returns {date_str: {model_name: temp_c}}."""
    result: dict[str, dict[str, float]] = {}
    for model in CONSENSUS_MODELS:
        try:
            r = requests.get(DETERMINISTIC_URL, params={
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max",
                "models": model, "forecast_days": days, "timezone": "UTC",
            }, timeout=20)
            if not r.ok:
                continue
            d = r.json()
            for t, v in zip(d["daily"]["time"], d["daily"]["temperature_2m_max"]):
                if v is not None:
                    result.setdefault(t, {})[model] = float(v)
            time.sleep(REQUEST_DELAY)
        except Exception:
            pass
    return result


# ── station / delta helpers ───────────────────────────────────────────────────

def fetch_station_coords(city: str) -> tuple[float, float] | None:
    res = sb.table("resolution_stations").select("lat,lon").eq("city", city).single().execute()
    return (res.data["lat"], res.data["lon"]) if res.data else None


def get_delta(city: str, month: int) -> tuple[float, float]:
    res = (sb.table("delta_matrix")
           .select("delta_mean,delta_std")
           .eq("city", city).eq("month", month).execute())
    rows = res.data or []
    if not rows:
        return 0.0, 0.0
    return (float(sum(r["delta_mean"] for r in rows) / len(rows)),
            float(sum(r["delta_std"]  for r in rows) / len(rows)))


def _forecast_is_fresh(city: str, forecast_date: str,
                       max_age_hours: float = FORECAST_FRESHNESS_HOURS) -> bool:
    try:
        res = (sb.table("ensemble_forecasts")
               .select("created_at")
               .eq("city", city).eq("forecast_date", forecast_date)
               .order("created_at", desc=True).limit(1).execute())
        if not res.data:
            return False
        created = datetime.fromisoformat(res.data[0]["created_at"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - created).total_seconds() / 3600 < max_age_hours
    except Exception:
        return False


# ── main per-city runner ──────────────────────────────────────────────────────

def run_for_city(city: str, force: bool = False):
    coords = fetch_station_coords(city)
    if not coords:
        print(f"  {city}: no station coords, skipping")
        return
    lat, lon = coords

    targets_needed = []
    for days_ahead in [1, 2]:
        target = (date.today() + timedelta(days=days_ahead)).isoformat()
        if force or not _forecast_is_fresh(city, target):
            targets_needed.append(target)
    if not targets_needed:
        print(f"  {city}: forecasts fresh, skipping")
        return

    gfs_data   = fetch_gfs_ensemble(lat, lon)
    ecmwf_data = fetch_ecmwf_ensemble(lat, lon)
    consensus  = fetch_consensus(lat, lon)

    # Forecast bias correction.
    #
    # OLD (buggy until 2026-05-18):
    #   delta_mean, delta_std = get_delta(city, month)
    # That called delta_matrix which stored resolution_station -
    # comparison_station temperature offsets, NOT forecast bias.  See
    # scripts/forecast_bias.py for the full diagnosis.
    #
    # NEW: get_forecast_bias() returns the per-city median of
    # (winning_bracket_mid - forecast_mean) from historical resolved
    # markets.  Positive value means forecasts under-predict, so we
    # add to make the forecast warmer.
    delta_mean = get_forecast_bias(city)
    delta_std = 0.0  # std calibration is handled by the existing std_floor

    model_run_hour = (datetime.now(timezone.utc).hour // 6) * 6
    model_run = datetime.now(timezone.utc).replace(
        hour=model_run_hour, minute=0, second=0, microsecond=0
    ).isoformat()

    unit = CITY_UNITS.get(city, "C")
    floor_c = STD_FLOOR_F * 5 / 9 if unit == "F" else STD_FLOOR_C

    for target in targets_needed:
        gfs_members   = gfs_data.get(target, [])
        ecmwf_members = ecmwf_data.get(target, [])

        if not gfs_members:
            print(f"  {city} {target}: no GFS members")
            continue

        all_members = gfs_members + ecmwf_members
        n    = len(all_members)
        mean = sum(all_members) / n
        variance = sum((h - mean) ** 2 for h in all_members) / max(n - 1, 1)
        std  = math.sqrt(variance)

        corrected_mean = mean + delta_mean
        corrected_std  = max(math.sqrt(std ** 2 + delta_std ** 2), floor_c)

        # Consensus spread
        day_means     = consensus.get(target, {})
        valid_means   = [v for v in day_means.values() if v is not None]
        spread        = (max(valid_means) - min(valid_means)) if len(valid_means) >= 2 else None

        # Delta-adjust the full member pool so probabilities are calibration-corrected.
        # Apply delta_mean as a shift AND spread members by delta_std so the member
        # pool reflects the same uncertainty as corrected_std (not just raw ensemble spread).
        # Without this, member counting uses a tighter distribution than corrected_std implies.
        raw_mean = mean  # unadjusted ensemble mean before delta correction
        def _adjust_member(m: float) -> float:
            """Shift by delta_mean and scale spread by corrected_std / raw_std ratio."""
            if std > 0 and corrected_std > std:
                # Expand spread proportionally around the corrected mean
                return round(corrected_mean + (m - raw_mean) * (corrected_std / std), 3)
            return round(m + delta_mean, 3)

        adj_gfs   = [_adjust_member(m) for m in gfs_members]
        adj_ecmwf = [_adjust_member(m) for m in ecmwf_members]

        row = {
            "city":               city,
            "forecast_date":      target,
            "model_run":          model_run,
            "model":              "gfs+ecmwf_ensemble",
            "mean_high":          round(corrected_mean, 2),
            "std_high":           round(corrected_std,  2),
            "min_high":           round(min(all_members), 2),
            "max_high":           round(max(all_members), 2),
            "member_count":       n,
            "raw_members":        adj_gfs,
            "ecmwf_members":      adj_ecmwf,
            "consensus_spread_c": round(spread, 2) if spread is not None else None,
            "model_means":        {k: round(v, 2) for k, v in day_means.items()} or None,
        }

        try:
            sb.table("ensemble_forecasts").upsert(
                row, on_conflict="city,forecast_date,model_run"
            ).execute()
        except Exception:
            # New columns don't exist yet — fall back gracefully
            row_base = {k: row[k] for k in [
                "city", "forecast_date", "model_run", "model",
                "mean_high", "std_high", "min_high", "max_high",
                "member_count", "raw_members",
            ]}
            sb.table("ensemble_forecasts").upsert(
                row_base, on_conflict="city,forecast_date,model_run"
            ).execute()
            if target == targets_needed[0]:
                print(f"  WARNING: new columns missing — run scripts/migrate_ensemble_v2.sql")

        spread_str = f"  spread={spread:.1f}°C" if spread is not None else ""
        print(
            f"  {city} {target}: GFS={len(gfs_members)} ECMWF={len(ecmwf_members)} "
            f"total={n} | mean={corrected_mean:.1f}°C ± {corrected_std:.1f}°C{spread_str}"
        )


# ── entry point ───────────────────────────────────────────────────────────────

def main(cities=None, force: bool = False):
    from config import CITIES
    targets = cities or CITIES
    print(f"Fetching GFS+ECMWF forecasts for {len(targets)} cities (force={force})...")
    for city in targets:
        run_for_city(city, force=force)
    print("Done.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("city", nargs="?", help="Single city (default: all)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main([args.city] if args.city else None, force=args.force)
