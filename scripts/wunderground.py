"""
Wunderground (api.weather.com) reader — the resolution source for 44 of our
50 Polymarket weather markets.

Why this exists
---------------
Until 2026-05-17 the bot read METAR from NWS for every city.  Investigation
that day proved that Polymarket explicitly resolves weather markets from
Wunderground's daily-history page (e.g. wunderground.com/history/daily/us/tx/
houston/KHOU), NOT from raw METAR.  The two can disagree by 1+°F because:

  • Wunderground's historical observations are at xx:53 (hourly METAR).
  • NWS time-series feeds report every 5-15 minutes, catching peaks
    Wunderground's xx:53 sampling misses entirely.
  • For Houston 2026-05-17: METAR running_max=86.4°F, Wunderground=85°F.
    Our $15 ≥86°F YES bet was therefore certain to lose.

This module exposes the same data Wunderground itself uses, via the
unauthenticated api.weather.com endpoint that wunderground.com fetches in
the browser.

Usage
-----
    from wunderground import fetch_daily_max_c, fetch_running_max_c

    # Daily max for a past day (used by backfill / resolver / δ calibration)
    t_c = fetch_daily_max_c("Houston", "2026-05-17")

    # Live running-max for today (used by temp_monitor)
    t_c = fetch_running_max_c("Houston")

Limitations
-----------
  • Public site API key.  Rate-limited; cache when looping over many cities.
  • Returns °F (integer); we round-trip to °C for our internal storage.
  • Some ICAOs may require a different :CC suffix than we've guessed.  Each
    miss is logged so we can patch the table.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

import requests


log = logging.getLogger("wunderground")

# Public API key used by wunderground.com itself.  No auth needed.
APIKEY = "e1f10a1e78da46f5b10a1e78da96f525"

# City → (ICAO, country_code) extracted on 2026-05-17 from Polymarket
# market description URLs (https://www.wunderground.com/history/daily/<cc>/<state>/<city>/<ICAO>).
# Cities NOT in this table either resolve from weather.gov (Istanbul,
# Moscow, Tel Aviv, Hong Kong) or had no Polymarket series at extraction
# time (São Paulo, Panama City).  For those, callers should fall back to
# METAR until proven otherwise.
STATIONS: dict[str, tuple[str, str]] = {
    "NYC":           ("KLGA", "US"),
    "Chicago":       ("KORD", "US"),
    "Miami":         ("KMIA", "US"),
    "Los Angeles":   ("KLAX", "US"),
    "Dallas":        ("KDAL", "US"),
    "Atlanta":       ("KATL", "US"),
    "Houston":       ("KHOU", "US"),
    "Austin":        ("KAUS", "US"),
    "Seattle":       ("KSEA", "US"),
    "San Francisco": ("KSFO", "US"),
    "Denver":        ("KBKF", "US"),     # note: Polymarket uses Buckley AFB, not DEN
    "London":        ("EGLC", "GB"),     # London City Airport per Polymarket
    "Paris":         ("LFPB", "FR"),     # Paris-Le Bourget
    "Madrid":        ("LEMD", "ES"),
    "Munich":        ("EDDM", "DE"),
    "Milan":         ("LIMC", "IT"),     # Malpensa
    "Amsterdam":     ("EHAM", "NL"),
    "Warsaw":        ("EPWA", "PL"),
    "Helsinki":      ("EFHK", "FI"),     # Vantaa
    "Ankara":        ("LTAC", "TR"),     # Esenboğa
    "Jeddah":        ("OEJN", "SA"),
    "Seoul":         ("RKSI", "KR"),     # Incheon
    "Tokyo":         ("RJTT", "JP"),     # Haneda
    "Busan":         ("RKPK", "KR"),
    "Taipei":        ("RCSS", "TW"),     # Songshan
    "Beijing":       ("ZBAA", "CN"),
    "Shanghai":      ("ZSPD", "CN"),     # Pudong
    "Guangzhou":     ("ZGGG", "CN"),
    "Shenzhen":      ("ZGSZ", "CN"),
    "Chengdu":       ("ZUUU", "CN"),     # Shuangliu
    "Chongqing":     ("ZUCK", "CN"),
    "Wuhan":         ("ZHHH", "CN"),
    "Singapore":     ("WSSS", "SG"),
    "Kuala Lumpur":  ("WMKK", "MY"),     # KLIA / Sepang
    "Manila":        ("RPLL", "PH"),
    "Jakarta":       ("WIHH", "ID"),     # Halim
    "Lucknow":       ("VILK", "IN"),
    "Karachi":       ("OPKC", "PK"),
    "Wellington":    ("NZWN", "NZ"),
    "Toronto":       ("CYYZ", "CA"),     # Pearson / Mississauga
    "Mexico City":   ("MMMX", "MX"),
    "Buenos Aires":  ("SAEZ", "AR"),     # Ezeiza
    "Cape Town":     ("FACT", "ZA"),
    "Lagos":         ("DNMM", "NG"),
}


def supports(city: str) -> bool:
    """Return True if we have a Wunderground station mapping for this city."""
    return city in STATIONS


def _url(icao: str, country: str, yyyymmdd: str) -> str:
    return (
        "https://api.weather.com/v1/location/"
        f"{icao}:9:{country}/observations/historical.json"
        f"?apiKey={APIKEY}&units=e&startDate={yyyymmdd}"
    )


def _fetch_observations(city: str, target_date: str) -> list[dict]:
    """Pull the raw hourly observation list for (city, YYYY-MM-DD)."""
    if city not in STATIONS:
        log.debug(f"wunderground: no station mapping for {city}")
        return []
    icao, country = STATIONS[city]
    yyyymmdd = target_date.replace("-", "")
    try:
        r = requests.get(_url(icao, country, yyyymmdd), timeout=15)
        if r.status_code != 200:
            log.warning(
                f"wunderground: {city}/{icao}:{country} HTTP {r.status_code} "
                f"for {target_date}"
            )
            return []
        return r.json().get("observations", []) or []
    except Exception as e:
        log.warning(f"wunderground: {city}/{icao} fetch failed: {e}")
        return []


def _max_temp_f(obs: list[dict]) -> float | None:
    """Return the max 'temp' field across an obs list, or None."""
    temps = [o.get("temp") for o in obs if o.get("temp") is not None]
    return float(max(temps)) if temps else None


def _f_to_c(f: float) -> float:
    return round((f - 32.0) * 5.0 / 9.0, 2)


def fetch_daily_max_c(city: str, target_date: str) -> float | None:
    """
    Return Wunderground's daily-high (°C) for the given (city, YYYY-MM-DD).
    None if we lack a station mapping or the API returns no data.
    """
    obs = _fetch_observations(city, target_date)
    f = _max_temp_f(obs)
    return _f_to_c(f) if f is not None else None


# WU's CANONICAL geocode per airport — extracted 2026-05-17 from
# wunderground.com/history/.../{ICAO} SSR transfer state.  Critical: the
# /v3/wx/forecast/daily/5day endpoint returns a GRIDDED value that varies
# with geocode (KHOU at our raw coords gave 86°F; at WU's canonical
# 29.634/-95.246 it gives 85°F — matching what WU displays).  Polymarket
# resolves to the value WU displays, so we must hit the 5day endpoint at
# the EXACT geocode WU uses, NOT the airport's raw lat/lon.  These coords
# are usually 1-3km off from the runway centre; that small displacement is
# the entire source of the bias we initially mis-diagnosed as a v1-vs-5day
# methodology issue.
STATION_LATLON: dict[str, tuple[float, float]] = {
    "NYC":           (40.7610,  -73.8640),
    "Chicago":       (41.9770,  -87.9050),
    "Miami":         (25.8480,  -80.2420),
    "Los Angeles":   (33.9600, -118.4000),
    "Dallas":        (32.8460,  -96.8700),
    "Atlanta":       (33.6390,  -84.4050),
    "Houston":       (29.6340,  -95.2460),
    "Austin":        (30.1620,  -97.6890),
    "Seattle":       (47.4410, -122.3000),
    "San Francisco": (37.6160, -122.3890),
    "Denver":        (39.7050, -104.7640),
    "London":        (51.5100,    0.0280),
    "Paris":         (48.9860,    2.4490),
    "Madrid":        (40.4520,   -3.5840),
    "Munich":        (48.3540,   11.7920),
    "Milan":         (45.6260,    8.6960),
    "Amsterdam":     (52.3100,    4.7650),
    "Warsaw":        (52.1690,   20.9790),
    "Helsinki":      (60.3170,   24.9670),
    "Ankara":        (40.2390,   33.0290),
    "Jeddah":        (21.5820,   39.1650),
    "Seoul":         (37.4943,  126.4905),
    "Tokyo":         (35.5500,  139.7840),
    "Busan":         (35.1814,  128.9544),
    "Taipei":        (25.0580,  121.5510),
    "Beijing":       (40.0470,  116.5930),
    "Shanghai":      (31.1500,  121.8030),
    "Guangzhou":     (23.4360,  113.3210),
    "Shenzhen":      (22.6370,  113.8310),
    "Chengdu":       (30.5730,  103.9580),
    "Chongqing":     (29.7220,  106.6260),
    "Wuhan":         (30.8040,  114.2200),
    "Singapore":     ( 1.3470,  103.9980),
    "Kuala Lumpur":  ( 2.7670,  101.6960),
    "Manila":        (14.5180,  121.0180),
    "Jakarta":       (-6.2640,  106.8870),
    "Lucknow":       (26.7380,   80.8570),
    "Karachi":       (24.8550,   67.0210),
    "Wellington":    (-41.3180, 174.7960),
    "Toronto":       (43.7120,  -79.6550),
    "Mexico City":   (19.4370,  -99.0810),
    "Buenos Aires":  (-34.7880, -58.5230),
    "Cape Town":     (-33.9700,  18.5950),
    "Lagos":         ( 6.4540,    3.3900),
}


def fetch_calendar_day_max_c(city: str) -> float | None:
    """
    Return TODAY's `calendarDayTemperatureMax` from Wunderground's
    /v3/wx/forecast/daily/5day endpoint, converted to °C.  This is THE
    field Polymarket resolves against — verified 2026-05-18 by tracing
    the actual XHR calls the wunderground.com /history page makes.

    For an in-progress day the value blends observed-so-far with
    forecast-rest-of-day; as the day progresses the forecast contribution
    shrinks until at end-of-day the value is the official daily max
    (with SPECI + 1-min ASOS observations that the v1 historical hourly
    endpoint silently filters out).

    Returns None if we lack coordinates or the API fails.
    """
    if city not in STATION_LATLON:
        return None
    lat, lon = STATION_LATLON[city]
    url = (
        "https://api.weather.com/v3/wx/forecast/daily/5day"
        f"?apiKey={APIKEY}&geocode={lat},{lon}"
        "&units=e&language=en-US&format=json"
    )
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            log.warning(f"wunderground 5day: {city} HTTP {r.status_code}")
            return None
        days = r.json().get("calendarDayTemperatureMax") or []
        if not days or days[0] is None:
            return None
        return _f_to_c(float(days[0]))
    except Exception as e:
        log.warning(f"wunderground 5day: {city} failed: {e}")
        return None


def fetch_live_snapshot(
    city: str, target_date: str | None = None,
) -> dict | None:
    """
    One-shot snapshot for temp_monitor: returns the data the per-cycle
    monitor loop needs, all from a single api.weather.com call.

      {
        'temp_c':           <latest observed temp °C>,
        'running_max_c':    <max temp °C across today's obs so far>,
        'n_obs':            <number of obs returned>,
        'latest_obs_gmt':   <unix epoch seconds of the most recent obs>,
        'recent_temps_c':   <list of the last 12 obs in °C, oldest→newest>,
        'sky_condition':    <METAR-style sky code if present, else None>,
      }

    Returns None if we have no station mapping for the city OR the API
    returns no observations (e.g. brand-new station, network blip).
    """
    target = target_date or datetime.now(timezone.utc).date().isoformat()
    obs = _fetch_observations(city, target)
    obs = [o for o in obs if o and o.get("temp") is not None] if obs else []
    # Sort by valid_time_gmt ascending so "latest" is meaningful.
    obs.sort(key=lambda o: o.get("valid_time_gmt") or 0)

    # If hourly obs are missing (data outage, future date, etc.) we can
    # still return a useful snapshot using ONLY calendarDayTemperatureMax.
    # The authoritative daily-max field is the most important value for
    # production locking; temp/sky/history are secondary.
    if not obs:
        # No hourly METAR available — return None so caller falls back to
        # METAR/Open-Meteo/HKO path in temp_monitor.  We deliberately do
        # NOT substitute the gridded calendarDayTemperatureMax here because
        # that value is area-averaged, not station-specific (proven via
        # geocode-shift testing 2026-05-17).
        return None

    temps_f      = [float(o["temp"]) for o in obs]
    latest_obs   = obs[-1]
    latest_t_f   = float(latest_obs["temp"])
    max_t_f      = max(temps_f)

    # Sky condition: prefer the most recent obs's sky text (BKN/OVC/SCT/etc).
    # api.weather.com uses 'clds' (e.g. 'BKN', 'OVC', 'CLR', 'FEW') on most
    # METAR stations.  Some stations send 'sky_cover' instead.  Try both.
    sky = (
        latest_obs.get("clds")
        or latest_obs.get("sky_cover")
        or None
    )
    if isinstance(sky, list) and sky:
        sky = sky[0]

    # Daily max source — REVISED 2026-05-22.
    #
    # Earlier (2026-05-17): we used `calendarDayTemperatureMax` from
    # /v3/wx/forecast/daily/5day at WU's canonical geocode, believing it
    # matched WU's history-page value.
    #
    # Today's diagnostic on 8 100%-NO losses proved this wrong:
    #   • bot's `running_max_c` (using cdtm)       → +0.89°C HIGHER than Polymarket
    #   • cdtm alone                               → +0.56°C HIGHER than Polymarket
    #   • hourly-obs max from this same endpoint   → +0.11°C — matches Polymarket
    #
    # cdtm is gridded and reads higher than the station-level observed max
    # that WU's history page actually shows. We've been using the wrong
    # field. Switch to the hourly-obs max (which we were already computing
    # for telemetry under hourly_max_c).
    #
    # cdtm is still fetched and kept under `calendar_day_max_c` for
    # diagnostic visibility — the divergence between the two will tell us
    # if api.weather.com ever changes their gridding.
    cdtm = fetch_calendar_day_max_c(city)
    hourly_max_c = _f_to_c(max_t_f)
    running_max_c = hourly_max_c    # station-hourly-obs max — matches WU history page + Polymarket resolution

    return {
        "temp_c":              _f_to_c(latest_t_f),
        "running_max_c":       running_max_c,         # station-hourly-obs max (matches WU history page + Polymarket resolution)
        "hourly_max_c":        hourly_max_c,          # same value as running_max_c (kept for compat with existing callers)
        "calendar_day_max_c":  cdtm,                  # the OLD (gridded) value — kept for divergence telemetry only
        "n_obs":               len(obs),
        "latest_obs_gmt":      int(latest_obs.get("valid_time_gmt") or 0),
        "recent_temps_c":      [_f_to_c(t) for t in temps_f[-12:]],
        "sky_condition":       sky if isinstance(sky, str) else None,
    }


def fetch_running_max_c(city: str, today: str | None = None) -> tuple[float | None, int]:
    """
    Return (running_max_c, n_observations_so_far) for today.  Mirrors
    Wunderground's live-page behaviour: max of any observation whose
    valid_time_gmt is within the calendar day in the station's local zone.

    For monitoring purposes we treat the date as the UTC date.  Differs
    by at most one hour from local-date for our airport set; the resolver
    re-checks against the official Wunderground daily-history page.
    """
    today = today or datetime.now(timezone.utc).date().isoformat()
    obs = _fetch_observations(city, today)
    f = _max_temp_f(obs)
    return (_f_to_c(f) if f is not None else None, len(obs))


# ── CLI smoke test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(message)s")
    if len(sys.argv) < 2:
        # Print today's Wunderground max for every supported city
        today = date.today().isoformat()
        print(f"Wunderground daily-max snapshot for {today}:\n")
        for city in STATIONS:
            t_c, n = fetch_running_max_c(city, today)
            t_f = (t_c * 9 / 5 + 32) if t_c is not None else None
            print(f"  {city:18s} {STATIONS[city][0]}: "
                  f"{'%.1f°C / %.0f°F' % (t_c, t_f) if t_c else '—':>20}"
                  f"  ({n} obs)")
    else:
        city = sys.argv[1]
        target = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
        t_c = fetch_daily_max_c(city, target)
        print(f"{city} {target}: {t_c}°C" if t_c is not None else f"{city} {target}: N/A")
