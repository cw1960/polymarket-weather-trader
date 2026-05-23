"""
Station verification helper.

For each city in NOAA_STATIONS, tries to fetch 30 days of TMAX data.
Reports which stations return data (VERIFIED) vs. no data (NEEDS FIX).

Run this BEFORE download_noaa.py to catch wrong station IDs early.

Usage:
    python scripts/find_station.py              # check all cities
    python scripts/find_station.py Denver       # check one city
    python scripts/find_station.py --search "Buckley"  # search by name
"""
import sys
import time
import requests
from datetime import date, timedelta
from config import NOAA_STATIONS

BASE_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
CDO_URL  = "https://www.ncdc.noaa.gov/cdo-web/api/v2/stations"

NEEDS_VERIFY = {
    "Denver":      "USW00003017 — Buckley SFB is military, may have limited GHCN coverage",
    "Istanbul":    "TUM00017064 — Bolge Kartal proxy; new LTFM airport (2019) not in GHCN",
    "Karachi":     "PKM00041780 — Jinnah Intl proxy; Masroor Airbase not in GHCN",
    "Hong Kong":   "MCM00045011 — Macau Intl proxy (~55km); HK Observatory not in GHCN",
    "Moscow":      "RSM00027612 — Russia stopped sharing NOAA data post-2022; delta uses 2019-2021",
    "Singapore":   "IDM00096087 — Batam proxy (~30km); Changi has no TMAX in GHCN",
    "Chengdu":     "CHM00056187 — Wenjiang proxy (17km); Shuangliu airport has TAVG only",
    "Taipei":      "TWM00046692 — Taiwan not in NOAA GHCN; delta=0",
    "São Paulo":   "BRM00083004 — No GHCN TMAX near Guarulhos; delta=0",
    "Panama City": "PMM00078762 — Tocumen not in GHCN; no Panama station has TMAX; delta=0",
}


def check_station(station_id: str) -> dict:
    """Try progressively older date windows to handle international data lag."""
    windows = [
        ("2026-01-01", "2026-04-24"),
        ("2025-06-01", "2025-12-31"),
        ("2025-01-01", "2025-06-30"),
        ("2024-01-01", "2024-12-31"),
        ("2023-01-01", "2023-12-31"),
        ("2022-01-01", "2022-12-31"),
        ("2021-01-01", "2021-12-31"),
    ]
    last_non_tmax_keys: list[str] = []
    for start, end in windows:
        try:
            r = requests.get(
                BASE_URL,
                params={
                    "dataset": "daily-summaries",
                    "stations": station_id,
                    "startDate": start,
                    "endDate": end,
                    "dataTypes": "TMAX",
                    "format": "json",
                    "units": "metric",
                },
                timeout=20,
            )
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list):
                continue
            # Filter only rows that actually contain TMAX (some rows may be missing it)
            tmax_rows = [row for row in rows if "TMAX" in row]
            if tmax_rows:
                latest = tmax_rows[-1]["DATE"]
                raw = float(tmax_rows[-1]["TMAX"])
                tmax = raw  # API returns °C directly with units=metric
                return {"ok": True, "rows": len(tmax_rows), "latest": latest, "latest_tmax": tmax}
            # Rows returned but no TMAX in this window — note the available keys and keep trying
            if rows:
                keys: set[str] = set()
                for row in rows:
                    keys.update(row.keys())
                last_non_tmax_keys = sorted(keys - {"DATE", "STATION"})
        except Exception as e:
            return {"ok": False, "rows": 0, "error": str(e)}
    if last_non_tmax_keys is not None and last_non_tmax_keys != []:
        return {"ok": False, "rows": 0, "error": f"no TMAX (has: {','.join(last_non_tmax_keys)})"}
    return {"ok": False, "rows": 0, "error": "no data in any window"}


def search_nearby(lat: float, lon: float, radius_km: int = 30) -> list[dict]:
    """Search NOAA CDO API for TMAX stations near coordinates. Requires no API key for basic use."""
    try:
        r = requests.get(
            CDO_URL,
            params={
                "datatypeid": "TMAX",
                "units": "metric",
                "extent": f"{lat-0.5},{lon-0.5},{lat+0.5},{lon+0.5}",
                "limit": 10,
            },
            timeout=15,
        )
        if r.status_code == 401:
            print("  CDO search requires a free API token from https://www.ncdc.noaa.gov/cdo-web/token")
            return []
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"  Search error: {e}")
        return []


def main():
    args = sys.argv[1:]

    if "--search" in args:
        idx = args.index("--search")
        term = args[idx + 1] if idx + 1 < len(args) else ""
        print(f"Searching for '{term}' in NOAA station names (requires CDO token)...")
        results = search_nearby(0, 0)  # placeholder — CDO search by name not in free tier
        print("Direct name search requires the free NOAA CDO API token.")
        print("Visit: https://www.ncdc.noaa.gov/cdo-web/token")
        return

    target_cities = [a for a in args if not a.startswith("--")]
    cities_to_check = {c: NOAA_STATIONS[c] for c in NOAA_STATIONS if not target_cities or c in target_cities}

    print(f"\nChecking {len(cities_to_check)} stations against NOAA GHCN API...\n")
    print(f"{'City':<20} {'Station ID':<16} {'Status':<10} {'Rows':<6} {'Latest date':<14} {'Latest TMAX'}")
    print("─" * 85)

    verified, failed, flagged = [], [], []

    for city, station_id in sorted(cities_to_check.items()):
        result = check_station(station_id)
        if result["ok"]:
            tmax_str = f"{result['latest_tmax']:.1f}°C"
            print(f"{city:<20} {station_id:<16} {'✓ OK':<10} {result['rows']:<6} {result['latest']:<14} {tmax_str}")
            verified.append(city)
        else:
            print(f"{city:<20} {station_id:<16} {'✗ FAIL':<10} {'—':<6} {'—':<14} {result['error'][:30]}")
            failed.append(city)
        if city in NEEDS_VERIFY:
            flagged.append((city, NEEDS_VERIFY[city]))
        time.sleep(0.5)

    print(f"\n{'─'*85}")
    print(f"✓ Verified: {len(verified)}/{len(cities_to_check)}")
    if failed:
        print(f"\n✗ FAILED — these station IDs returned no data:")
        for c in failed:
            print(f"  {c}: {cities_to_check[c]}")
        print("\n  For each failed city:")
        print("  1. Check the NOAA GHCN station list: https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt")
        print("  2. Search by airport name or ICAO code")
        print("  3. Update the station_id in NOAA_STATIONS (config.py) and resolution_stations (Supabase)")
    if flagged:
        print(f"\n⚠ Pre-flagged for verification:")
        for city, note in flagged:
            print(f"  {city}: {note}")


if __name__ == "__main__":
    main()
