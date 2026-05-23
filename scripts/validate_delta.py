"""Backtest the delta model over the last 6 months to confirm it improves accuracy."""
import os
import glob
import pandas as pd
import numpy as np
from config import NOAA_STATIONS, COMPARISON_STATIONS

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "historical")
DELTA_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "delta_matrix.csv")


def load_station(station_id: str) -> pd.DataFrame:
    files = glob.glob(os.path.join(DATA_DIR, f"{station_id}_*.csv"))
    if not files:
        return pd.DataFrame()
    df = pd.read_csv(files[0], parse_dates=["date"])
    return df.rename(columns={"tmax_c": station_id}).set_index("date")


def validate_city(city: str, delta_df: pd.DataFrame) -> dict:
    res_id = NOAA_STATIONS[city]
    comp_ids = COMPARISON_STATIONS.get(city, [])
    res_df = load_station(res_id)
    if res_df.empty or not comp_ids:
        return {}

    comp_id = comp_ids[0]
    comp_df = load_station(comp_id)
    if comp_df.empty:
        return {}

    merged = res_df.join(comp_df, how="inner").dropna()
    cutoff = merged.index.max() - pd.Timedelta(days=180)
    test = merged[merged.index >= cutoff].copy()

    raw_errors, corrected_errors = [], []
    for dt, row in test.iterrows():
        month = dt.month
        delta_row = delta_df[
            (delta_df["city"] == city) &
            (delta_df["comparison_station"] == comp_id) &
            (delta_df["month"] == month)
        ]
        if delta_row.empty:
            continue
        delta_mean = delta_row["delta_mean"].values[0]
        raw_pred = row[comp_id]
        corrected_pred = raw_pred + delta_mean
        actual = row[res_id]
        raw_errors.append(abs(raw_pred - actual))
        corrected_errors.append(abs(corrected_pred - actual))

    if not raw_errors:
        return {}

    raw_mae = np.mean(raw_errors)
    corr_mae = np.mean(corrected_errors)
    improvement = (raw_mae - corr_mae) / raw_mae * 100
    return {
        "city": city,
        "raw_mae": round(raw_mae, 2),
        "corrected_mae": round(corr_mae, 2),
        "improvement_pct": round(improvement, 1),
    }


def main():
    if not os.path.exists(DELTA_CSV):
        print("delta_matrix.csv not found. Run compute_delta_matrix.py first.")
        return

    delta_df = pd.read_csv(DELTA_CSV)
    print("\n=== DELTA MODEL VALIDATION ===\n")
    all_ok = True
    for city in NOAA_STATIONS:
        result = validate_city(city, delta_df)
        if not result:
            print(f"{city}: insufficient data")
            continue
        ok = result["improvement_pct"] >= 10
        status = "✓" if ok else "⚠ WARNING"
        print(f"{city} ({result['city']} vs comparison):")
        print(f"  Raw MAE:       {result['raw_mae']}°C")
        print(f"  Corrected MAE: {result['corrected_mae']}°C")
        print(f"  Improvement:   {result['improvement_pct']}% {status}")
        if not ok:
            all_ok = False
            print(f"  → Delta model may not be adding value for {city}. Check station IDs.")

    print()
    if all_ok:
        print("All cities show ≥10% improvement. Delta model is valid. Proceed to Phase 3.")
    else:
        print("Some cities below 10% threshold. Do not proceed until resolved.")


if __name__ == "__main__":
    main()
