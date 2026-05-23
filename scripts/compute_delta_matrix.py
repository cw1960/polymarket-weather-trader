"""Compute monthly temperature deltas: resolution_station − comparison_station."""
import os
import glob
import pandas as pd
import numpy as np
from supabase import create_client
from config import NOAA_STATIONS, COMPARISON_STATIONS, SUPABASE_URL, SUPABASE_KEY

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "historical")
OUT_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "delta_matrix.csv")


def load_station(station_id: str) -> pd.DataFrame:
    files = glob.glob(os.path.join(DATA_DIR, f"{station_id}_*.csv"))
    if not files:
        return pd.DataFrame()
    df = pd.read_csv(files[0], parse_dates=["date"])
    df = df.rename(columns={"tmax_c": station_id})
    return df.set_index("date")


def compute_city(city: str) -> pd.DataFrame:
    res_id = NOAA_STATIONS[city]
    comp_ids = COMPARISON_STATIONS.get(city, [])
    res_df = load_station(res_id)
    if res_df.empty:
        print(f"  SKIP {city}: no resolution station data")
        return pd.DataFrame()

    rows = []
    for comp_id in comp_ids:
        if comp_id == res_id:
            continue  # skip self-comparison
        comp_df = load_station(comp_id)
        if comp_df.empty:
            continue
        merged = res_df.join(comp_df, how="inner")
        merged["delta"] = merged[res_id] - merged[comp_id]
        merged["month"] = merged.index.month
        for month, grp in merged.groupby("month"):
            if len(grp) < 5:
                continue
            rows.append({
                "city": city,
                "resolution_station": res_id,
                "comparison_station": comp_id,
                "month": int(month),
                "delta_mean": round(grp["delta"].mean(), 4),
                "delta_std": round(grp["delta"].std(), 4),
                "sample_count": len(grp),
            })
    return pd.DataFrame(rows)


def visualize_city(city: str, df: pd.DataFrame):
    sub = df[df["city"] == city]
    if sub.empty:
        return
    print(f"\n{city} monthly deltas:")
    for _, row in sub.iterrows():
        bar = "█" * max(0, int(abs(row["delta_mean"]) * 4))
        sign = "+" if row["delta_mean"] >= 0 else "-"
        flag = " ⚠ unreliable" if row["sample_count"] < 20 else ""
        flag += " ⚠ noisy" if row["delta_std"] > 2.0 else ""
        print(f"  Month {row['month']:2d} | {row['comparison_station']} | "
              f"{sign}{abs(row['delta_mean']):.2f}°C ±{row['delta_std']:.2f} "
              f"n={row['sample_count']} {bar}{flag}")


def main():
    all_rows = []
    for city in NOAA_STATIONS:
        print(f"Computing delta for {city}...")
        df = compute_city(city)
        if not df.empty:
            all_rows.append(df)

    if not all_rows:
        print("No data computed. Run download_noaa.py first.")
        return

    result = pd.concat(all_rows, ignore_index=True)
    result.to_csv(OUT_CSV, index=False)
    print(f"\nSaved {len(result)} rows to {OUT_CSV}")

    for city in NOAA_STATIONS:
        visualize_city(city, result)

    if SUPABASE_URL and SUPABASE_KEY:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        for city in NOAA_STATIONS:
            sb.table("delta_matrix").delete().eq("city", city).execute()
        rows = result.to_dict(orient="records")
        sb.table("delta_matrix").insert(rows).execute()
        print(f"\nUploaded {len(rows)} rows to Supabase delta_matrix table.")
    else:
        print("\nNo Supabase credentials — skipping upload.")


if __name__ == "__main__":
    main()
