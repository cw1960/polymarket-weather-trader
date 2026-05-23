"""
Backtest: Variance-Adjusted K (per senior dev recommendation)
==============================================================
Compare three strategies on historical real-money trades:
  1. Original (no shrinkage, raw delta)
  2. Static K=5 (current production logic)
  3. Variance-adjusted K (proposed)

Variance is computed from observed deltas in trade_signals (in °C).
"""
import re
import statistics
from collections import defaultdict
from config import SUPABASE_URL, SUPABASE_KEY, CITY_UNITS
from supabase import create_client

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

K_BASE = 5
BOUNDARY_BUFFER_C = 0.3
DEFAULT_DELTA = 1.0
CALIB_MIN = 3


def get_observed_deltas() -> dict:
    """Return {city: [observed_delta_c, ...]} from resolved Phase 2 trades."""
    res = (sb.table("trade_signals")
           .select("city, mean_high, winning_bracket")
           .eq("signal_phase", "phase2")
           .not_.is_("pnl_usd", "null")
           .not_.is_("mean_high", "null")
           .limit(500)
           .execute())
    out = defaultdict(list)
    for r in res.data:
        win_nums = re.findall(r"-?\d+", r.get("winning_bracket", "") or "")
        if not win_nums:
            continue
        actual_native = float(win_nums[0])
        mean_high = float(r["mean_high"])
        if mean_high == 0:
            continue
        unit = CITY_UNITS.get(r["city"], "C")
        actual_c = (actual_native - 32) * 5 / 9 if unit == "F" else actual_native
        out[r["city"]].append(actual_c - mean_high)
    return out


def predict(running_max_c: float, raw_delta: float, samples: int, k: float,
            apply_buffer: bool = True) -> int:
    """Replay logic with given K and current delta. Returns predicted bracket (integer °C)."""
    if samples >= CALIB_MIN:
        eff_delta = (samples / (samples + k)) * raw_delta
    else:
        eff_delta = DEFAULT_DELTA
    adjusted = running_max_c + eff_delta
    bracket = int(round(adjusted))
    bracket_low = bracket - 0.5
    distance_above = adjusted - bracket_low
    if apply_buffer and 0 <= distance_above <= BOUNDARY_BUFFER_C + 1e-6 and bracket > 0:
        bracket -= 1
    return bracket


def main():
    obs = get_observed_deltas()

    # Compute σ for cities with n>=3
    sigmas = {}
    for city, deltas in obs.items():
        if len(deltas) >= CALIB_MIN:
            sigmas[city] = statistics.stdev(deltas)

    sigma_global = statistics.median(sigmas.values()) if sigmas else 0.5
    print(f"Global median σ across {len(sigmas)} cities: {sigma_global:.3f}°C")
    print()

    # Load all real-money trades
    trades = (sb.table("trade_signals")
              .select("*")
              .eq("signal_phase", "phase2")
              .not_.is_("pnl_usd", "null")
              .order("forecast_date")
              .limit(500)
              .execute()).data
    real = [t for t in trades if float(t.get("recommended_position") or 0) > 1]

    # Current deltas
    ds = sb.table("resolution_stations").select("city, delta_c, delta_samples").execute()
    dmap = {r["city"]: (float(r.get("delta_c") or 0), int(r.get("delta_samples") or 0))
            for r in ds.data}

    # Replay each trade under three strategies
    strategies = {
        "Original (no shrinkage)": lambda c, d, n: predict(c, d, n, k=0, apply_buffer=False),
        "Static K=5":              lambda c, d, n: predict(c, d, n, k=K_BASE, apply_buffer=True),
        "Variance-adjusted K":     None,  # filled in below
    }

    def variance_adjusted_predict(running_max, raw_delta, samples, city):
        sigma = sigmas.get(city)
        if sigma is None:
            k = K_BASE
        else:
            ratio = sigma / sigma_global if sigma_global > 0 else 1.0
            k = max(1.0, min(10.0, K_BASE * ratio))
        return predict(running_max, raw_delta, samples, k=k, apply_buffer=True)

    print(f"{'Date':10} {'City':14} {'Lock':6} {'Raw δ':7} {'Actual':6}  "
          f"{'Orig':5}  {'K=5':5}  {'K_adj':6} {'σ_city':7} {'K_used':6}")
    print("-" * 95)

    counts = {s: {"correct": 0, "wrong": 0} for s in strategies}
    counts["Variance-adjusted K"] = {"correct": 0, "wrong": 0}

    for t in real:
        city = t["city"]
        bet_nums = re.findall(r"-?\d+", t["outcome"])
        win_nums = re.findall(r"-?\d+", t.get("winning_bracket", "") or "")
        if not bet_nums or not win_nums:
            continue
        actual_native = int(win_nums[0])
        unit = CITY_UNITS.get(city, "C")
        actual_c = int(round((actual_native - 32) * 5 / 9)) if unit == "F" else actual_native

        lock_max = float(t.get("mean_high") or 0)
        if lock_max == 0:
            continue

        raw_d, samples = dmap.get(city, (0, 0))

        p_orig = predict(lock_max, raw_d, samples, k=0, apply_buffer=False)
        p_k5   = predict(lock_max, raw_d, samples, k=K_BASE, apply_buffer=True)
        p_var  = variance_adjusted_predict(lock_max, raw_d, samples, city)

        # For F cities, convert predicted back to F for comparison? No — we compare in C
        # since predict() returns rounded C. Actually for F city with bracket "70-71°F",
        # win_nums[0]=70 native; but our predict returns C value. They won't match.
        # Skip F cities for this analysis since the bracket arithmetic differs.
        if unit == "F":
            continue

        sigma_c = sigmas.get(city)
        if sigma_c is None:
            k_used = K_BASE
        else:
            k_used = max(1.0, min(10.0, K_BASE * (sigma_c / sigma_global)))

        for label, pred in [("Original (no shrinkage)", p_orig),
                            ("Static K=5", p_k5),
                            ("Variance-adjusted K", p_var)]:
            if pred == actual_c:
                counts[label]["correct"] += 1
            else:
                counts[label]["wrong"] += 1

        sigma_str = f'{sigma_c:.2f}' if sigma_c is not None else 'N/A'
        print(f"{t.get('forecast_date',''):10} {city:14} {lock_max:5.1f}° "
              f"{raw_d:+6.2f}  {actual_c:5}°   "
              f"{p_orig:4}°  {p_k5:4}°  {p_var:5}°   {sigma_str:>7}  {k_used:5.2f}")

    print()
    print("=" * 70)
    print("SUMMARY (C-unit cities only)")
    print("=" * 70)
    for label, c in counts.items():
        n = c["correct"] + c["wrong"]
        if n == 0: continue
        wr = c["correct"] / n * 100
        print(f"  {label:30} {c['correct']:2}W / {c['wrong']:2}L  ({wr:5.1f}%)")


if __name__ == "__main__":
    main()
