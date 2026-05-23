"""
backtest_calibration_only.py — out-of-sample calibration test.

Strips away the simulated-P&L logic (which was broken because cached Polymarket
outcomePrices are POST-resolution, not pre-trade). Measures only:

  For every bracket in every resolved (city, date) cell:
    - compute prob_yes under the post-fix model with WALK-FORWARD bias
    - bin by prob_yes (0.0, 0.1, ..., 1.0)
    - count how often that bracket actually won

  If the model is well calibrated:
    bracket with prob_yes = 0.7 → actually wins ~70% of the time
    bracket with prob_yes = 0.1 → actually wins ~10% of the time

This is the single most important number the senior dev asked for: does
the post-fix model produce calibrated probabilities OUT OF SAMPLE?

Reads the cached JSONs left by backtest_walk_forward_100d.py. No HTTP calls.
"""
from __future__ import annotations
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backtest_walk_forward_100d import (
    CITY_SLUG, fetch_polymarket_event, forecast_stats_c,
    extract_brackets_and_winner, bracket_prob_yes,
    WINDOW_DAYS, END_DATE, SKIP_DATES, MIN_TRAIN_N, CAP_BIAS_C,
)
from forecast_bias import compute_walk_forward_correction, ResolvedForecast


def main() -> int:
    cities = list(CITY_SLUG.keys())
    start = END_DATE - timedelta(days=WINDOW_DAYS)

    # Phase 1: collect winners + raw means per city
    city_history: dict[str, list[ResolvedForecast]] = defaultdict(list)
    universe: list[tuple[str, date, list[dict], str, float, float]] = []
    d = start
    while d <= END_DATE:
        if d in SKIP_DATES:
            d += timedelta(days=1); continue
        for city in cities:
            stats = forecast_stats_c(city, d)
            if not stats: continue
            mean_c, std_c, _ = stats
            event = fetch_polymarket_event(city, d)
            if not event: continue
            brackets, winner_label = extract_brackets_and_winner(event, city)
            if not brackets or not winner_label: continue
            universe.append((city, d, brackets, winner_label, mean_c, std_c))
            win_b = next((b for b in brackets if b["label"] == winner_label), None)
            if win_b:
                mid_c = 0.5 * (win_b["low_c"] + win_b["high_c"])
                if mid_c < -50: mid_c = win_b["high_c"]
                if mid_c >  60: mid_c = win_b["low_c"]
                city_history[city].append(ResolvedForecast(d, mean_c, mid_c))
        d += timedelta(days=1)

    print(f"Universe: {len(universe)} resolved (city, date) cells across "
          f"{len(set(c for c, *_ in universe))} cities")

    # Phase 2: walk-forward calibration
    yes_bins:  dict[float, list[int]] = defaultdict(lambda: [0, 0])  # n, wins
    no_bins:   dict[float, list[int]] = defaultdict(lambda: [0, 0])

    n_train_below_floor = 0
    n_train_above_floor = 0
    bias_used: list[float] = []

    for (city, d, brackets, winner_label, raw_mean_c, std_c) in universe:
        bias_c, n_train = compute_walk_forward_correction(
            city, d, city_history[city],
            min_samples=MIN_TRAIN_N, cap_abs=CAP_BIAS_C,
        )
        if n_train < MIN_TRAIN_N: n_train_below_floor += 1
        else:                     n_train_above_floor += 1
        bias_used.append(bias_c)
        corrected_mean_c = raw_mean_c + bias_c

        for b in brackets:
            prob_yes = bracket_prob_yes(b, corrected_mean_c, std_c, city)
            prob_no  = 1.0 - prob_yes
            won_yes  = (b["label"] == winner_label)
            yk = round(prob_yes * 10) / 10
            yes_bins[yk][0] += 1
            yes_bins[yk][1] += int(won_yes)
            nk = round(prob_no * 10) / 10
            no_bins[nk][0]  += 1
            no_bins[nk][1]  += int(not won_yes)

    print()
    print(f"cells with walk-forward bias active (n_train >= {MIN_TRAIN_N}): "
          f"{n_train_above_floor} / {n_train_above_floor + n_train_below_floor}")
    if bias_used:
        nonzero = [b for b in bias_used if b != 0]
        print(f"  bias values used: n_nonzero={len(nonzero)}  "
              f"range={min(bias_used):+.2f} to {max(bias_used):+.2f}  "
              f"mean_abs={sum(abs(b) for b in bias_used)/len(bias_used):.2f}")

    # ── YES-side calibration (the headline number) ─────────────────────
    print()
    print("=== YES-SIDE CALIBRATION (out of sample, walk-forward bias) ===")
    print("    Compare to in-sample (which had leakage):")
    print("      0.5 → 41.7%  0.7 → 66.4%  0.9 → 76.4%  1.0 → 83.3%")
    print()
    print(f"    {'bucket':>6} | {'n':>6} | {'win_rate':>9}")
    perfect = 0.0
    total_n = 0
    for b in sorted(yes_bins):
        n, w = yes_bins[b]
        if n >= 20:
            rate = w / n
            print(f"    {b:>6.1f} | {n:>6d} | {rate:>8.1%}")
            # Brier-style accumulation for diagnostic
            perfect += (rate - b) ** 2 * n
            total_n += n
    if total_n > 0:
        rmse = (perfect / total_n) ** 0.5
        print(f"    RMSE(predicted - actual): {rmse:.3f}")
        print(f"    (0 = perfect; >0.15 = significant miscalibration)")

    # ── NO-side calibration (for the strategy we actually trade) ───────
    print()
    print("=== NO-SIDE CALIBRATION (mirror — should be 1 minus YES) ===")
    print(f"    {'bucket':>6} | {'n':>6} | {'win_rate':>9}")
    for b in sorted(no_bins):
        n, w = no_bins[b]
        if n >= 20:
            print(f"    {b:>6.1f} | {n:>6d} | {w/n:>8.1%}")

    # ── How many brackets pass the gate? ───────────────────────────────
    print()
    print("=== GATE CANDIDATES (no market-price filter, just model) ===")
    print("    NO-side, prob_no >= 0.55:")
    p55_n = sum(n for b, (n, w) in no_bins.items() if b >= 0.55)
    p55_w = sum(w for b, (n, w) in no_bins.items() if b >= 0.55)
    if p55_n > 0:
        print(f"    n={p55_n}  wins={p55_w}  rate={p55_w/p55_n:.1%}")
        # In-sample comparison
        print(f"    (In-sample full-universe calibration showed ~73% on this subset)")

    print("\n    YES-side, prob_yes >= 0.55:")
    py55_n = sum(n for b, (n, w) in yes_bins.items() if b >= 0.55)
    py55_w = sum(w for b, (n, w) in yes_bins.items() if b >= 0.55)
    if py55_n > 0:
        print(f"    n={py55_n}  wins={py55_w}  rate={py55_w/py55_n:.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
