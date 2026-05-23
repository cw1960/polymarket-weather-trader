"""Diagnostic: compare morning forecast mean vs actual daily-peak for every
resolved phase2_sweep trade, to detect systematic forecast bias."""
import os, re, statistics
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
from supabase import create_client

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

SINCE = "2026-05-21T19:31:00+00:00"
US = {"NYC","Chicago","Miami","Los Angeles","Dallas","Atlanta","Houston","Austin","Seattle","San Francisco","Denver"}


def fmt_t(c_value, city):
    if c_value is None:
        return "  ?   "
    if city in US:
        return f"{round(c_value * 9 / 5 + 32):3d}°F"
    return f"{round(c_value):3d}°C"


def winner_mid_c(question: str) -> float | None:
    q = (question or "").lower()
    m = re.search(r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°([fc])", q)
    if m:
        unit = m.group(3).upper()
        a, b = float(m.group(1)), float(m.group(2))
        mid = (a + b) / 2
        return (mid - 32) * 5 / 9 if unit == "F" else mid
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)\s*°([fc])", q)
    if m:
        unit = m.group(2).upper()
        v = float(m.group(1))
        return (v - 32) * 5 / 9 if unit == "F" else v
    return None


def main():
    r = (sb.table("trade_signals")
         .select("signal_time,city,forecast_date,outcome,market_price,actual_outcome,winning_bracket,model_probability")
         .eq("signal_phase", "phase2_sweep")
         .gte("signal_time", SINCE)
         .not_.is_("winning_bracket", "null")
         .order("signal_time")
         .execute())

    print(f"{'CITY':14s} {'DATE':10s} {'BRACKET':9s} {'FORECAST':9s} {'ACTUAL':9s} {'WINNER':9s} {'F-A':8s} {'MODEL':6s} {'RESULT':6s}")
    print("-" * 100)
    biases = []        # forecast_mean − actual_peak (°C)
    biases_us = []
    biases_other = []

    for x in r.data:
        city  = x["city"]
        fdate = x["forecast_date"]
        ef = (sb.table("ensemble_forecasts").select("mean_high")
              .eq("city", city).eq("forecast_date", fdate)
              .order("created_at", desc=True).limit(1).execute())
        fmean = float(ef.data[0]["mean_high"]) if ef.data else None
        tr = (sb.table("temp_readings").select("running_max_c")
              .eq("city", city).eq("reading_date", fdate)
              .limit(1).execute())
        actual = float(tr.data[0]["running_max_c"]) if tr.data else None
        wmid = winner_mid_c(x.get("winning_bracket", ""))

        delta_native = ""
        if fmean is not None and actual is not None:
            if city in US:
                delta_native = f"{(fmean - actual) * 9 / 5:+5.1f}F"
            else:
                delta_native = f"{(fmean - actual):+5.1f}C"
            biases.append(fmean - actual)
            (biases_us if city in US else biases_other).append(fmean - actual)

        won = str(x.get("actual_outcome", "")) == "false"
        print(f"{city:14s} {fdate} {x.get('outcome',''):9s} "
              f"{fmt_t(fmean, city):9s} {fmt_t(actual, city):9s} {fmt_t(wmid, city):9s} "
              f"{delta_native:8s} {x.get('model_probability') or 0:.2f}  "
              f"{'WON' if won else 'LOST'}")

    print()
    if biases:
        print("=== AGGREGATE BIAS (morning forecast mean − actual daily peak, °C) ===")
        print(f"  n={len(biases)}  mean={statistics.mean(biases):+.2f}°C  "
              f"median={statistics.median(biases):+.2f}°C  "
              f"stdev={statistics.stdev(biases) if len(biases) > 1 else 0:.2f}°C")
        pos = sum(1 for b in biases if b > 0)
        print(f"  forecast > actual: {pos}/{len(biases)} ({pos/len(biases)*100:.0f}%)")
        if biases_us:
            print(f"  US cities (n={len(biases_us)}):    mean={statistics.mean(biases_us):+.2f}°C")
        if biases_other:
            print(f"  non-US cities (n={len(biases_other)}): mean={statistics.mean(biases_other):+.2f}°C")

        print()
        print("If mean is POSITIVE → bot's forecast over-predicts the peak (real high is cooler).")
        print("If mean is NEGATIVE → bot's forecast under-predicts the peak.")


if __name__ == "__main__":
    main()
