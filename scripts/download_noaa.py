"""Download historical TMAX data from NOAA GHCN for all resolution + comparison stations."""
import os
import time
import requests
import pandas as pd
from datetime import date, timedelta
from config import NOAA_STATIONS, COMPARISON_STATIONS, NO_TMAX_CITIES

BASE_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "historical")
os.makedirs(DATA_DIR, exist_ok=True)


def fetch_station(station_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    params = {
        "dataset": "daily-summaries",
        "stations": station_id,
        "startDate": start_date,
        "endDate": end_date,
        "dataTypes": "TMAX",
        "format": "json",
        "units": "metric",
    }
    try:
        r = requests.get(BASE_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows or not isinstance(rows, list):
            print(f"  WARNING: no data for {station_id}")
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        if "TMAX" not in df.columns:
            print(f"  WARNING: {station_id} returned {len(df)} rows but no TMAX column (has: {list(df.columns)})")
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["DATE"])
        df["tmax_c"] = pd.to_numeric(df["TMAX"], errors="coerce")  # API returns °C directly with units=metric
        return df[["date", "tmax_c"]].dropna()
    except Exception as e:
        print(f"  ERROR fetching {station_id}: {e}")
        return pd.DataFrame()


def main():
    end = date.today().isoformat()
    # 5 years covers Russia (NOAA data stopped 2022) and general international lag
    start = (date.today() - timedelta(days=365 * 5)).isoformat()
    start_year = start[:4]
    end_year = end[:4]

    no_tmax_ids = {NOAA_STATIONS[c] for c in NO_TMAX_CITIES if c in NOAA_STATIONS}

    all_stations: dict[str, str] = {}
    for city, sid in NOAA_STATIONS.items():
        all_stations[sid] = city
    for city, sids in COMPARISON_STATIONS.items():
        for sid in sids:
            all_stations[sid] = city

    skipped = 0
    for station_id, city in all_stations.items():
        if station_id in no_tmax_ids:
            print(f"Skipping {station_id} ({city}) — no TMAX in GHCN, delta=0")
            skipped += 1
            continue
        fname = os.path.join(DATA_DIR, f"{station_id}_{start_year}_{end_year}.csv")
        print(f"Downloading {station_id} ({city}) ...", end=" ", flush=True)
        df = fetch_station(station_id, start, end)
        if not df.empty:
            df.to_csv(fname, index=False)
            print(f"saved {len(df)} rows → {os.path.basename(fname)}")
        else:
            print("no data")
        time.sleep(1)

    print(f"\nDone. {len(all_stations) - skipped} stations downloaded, {skipped} skipped (no TMAX).")


if __name__ == "__main__":
    main()
