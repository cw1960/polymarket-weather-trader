"""
backtest_walk_forward_100d.py — full-universe, leakage-free 100-day backtest.

Built per the 2026-05-20 senior dev review. Answers:

  1. Does the NO-tail edge survive when the bot evaluates the FULL bracket
     universe (not just signals the prior pipeline logged)?
  2. Does walk-forward (out-of-sample) bias correction still produce edge,
     or was the 73% in-sample win rate mostly leakage?
  3. ROI by predicted-prob bucket and by price bucket (not just win rate).
  4. Per-city ROI with sample-size-aware shrinkage.

Data sources:
  • Polymarket Gamma API   — historical events: bracket structure + outcomePrices
  • Open-Meteo historical-forecast-api — 6 deterministic models per (city, date)
                                          serves as synthetic ensemble. The
                                          ensemble archive returns empty for past
                                          dates so we cannot use 82 members.
  • forecast_bias.compute_walk_forward_correction — out-of-sample bias

Method per (city, date) cell:
  1. Fetch historical Polymarket event → bracket structure + winning bracket
  2. Fetch historical 6-model forecast → mean + std across the 6 models
  3. Apply walk-forward bias correction using ONLY resolved markets from
     forecast_date < target_date
  4. For each bracket: prob_yes via Gaussian CDF over corrected_mean,corrected_std
     in the city's native unit (°F or °C)
  5. Apply new gate (edge≥0.08, prob≥0.55) on BOTH sides → log every bracket
  6. Take top-3 NO candidates by edge per (city, date), $5 each
  7. Compute P&L using actual winning bracket

Excludes:
  - 2026-05-19 (Polymarket oracle bug)
  - Cities × dates with no Polymarket event
  - Cities × dates with no forecast data
  - Anything where bracket parsing fails

Caching:
  - data/backtest_cache/poly_<slug>.json
  - data/backtest_cache/meteo_<lat>_<lon>_<date>.json
  Re-running the script reuses caches. Delete a file to force refetch.

Output:
  - data/backtest_cache/results.csv — one row per (city, date, bracket) evaluation
  - stdout — summary stats, calibration table, per-city table, per-day P&L
"""
from __future__ import annotations
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, str(Path(__file__).parent))

from forecast_bias import compute_walk_forward_correction, ResolvedForecast  # noqa: E402
from config import CITY_UNITS  # noqa: E402
from wunderground import STATION_LATLON  # noqa: E402

# ── Parameters ───────────────────────────────────────────────────────────────

WINDOW_DAYS    = 90                   # how far back to backtest
END_DATE       = date(2026, 5, 19)    # exclusive — yesterday relative to 5/20
SKIP_DATES     = {date(2026, 5, 19)}  # Polymarket oracle disaster day
NEW_EDGE       = 0.08
NEW_MIN_PROB   = 0.55
TOP_N_PER_CITY = 3
TRADE_SIZE_USD = 5.0
STD_FLOOR_C    = 1.5                  # mirror fetch_forecasts.STD_FLOOR_C
MIN_TRAIN_N    = 5                    # walk-forward bias requires n≥5 samples
CAP_BIAS_C     = 2.0                  # ±2°C cap on bias correction

CACHE_DIR = Path("/root/polymarket/data/backtest_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

POLY_BASE  = "https://gamma-api.polymarket.com"
METEO_BASE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
METEO_MODELS = "gfs_seamless,ecmwf_ifs025,icon_seamless,meteofrance_seamless,ukmo_seamless,gem_seamless"

# Polymarket uses different city slugs from the bot's internal names
CITY_SLUG: dict[str, str] = {
    "NYC": "nyc", "Chicago": "chicago", "Miami": "miami",
    "Los Angeles": "los-angeles", "Dallas": "dallas", "Atlanta": "atlanta",
    "Houston": "houston", "Austin": "austin", "Seattle": "seattle",
    "San Francisco": "san-francisco", "Denver": "denver",
    "London": "london", "Paris": "paris", "Madrid": "madrid",
    "Munich": "munich", "Milan": "milan", "Amsterdam": "amsterdam",
    "Warsaw": "warsaw", "Helsinki": "helsinki",
    "Istanbul": "istanbul", "Ankara": "ankara", "Moscow": "moscow",
    "Tel Aviv": "tel-aviv", "Jeddah": "jeddah",
    "Hong Kong": "hong-kong", "Seoul": "seoul", "Tokyo": "tokyo",
    "Busan": "busan", "Taipei": "taipei",
    "Beijing": "beijing", "Shanghai": "shanghai", "Guangzhou": "guangzhou",
    "Shenzhen": "shenzhen", "Chengdu": "chengdu", "Chongqing": "chongqing",
    "Wuhan": "wuhan", "Singapore": "singapore",
    "Kuala Lumpur": "kuala-lumpur", "Manila": "manila", "Jakarta": "jakarta",
    "Lucknow": "lucknow", "Karachi": "karachi", "Wellington": "wellington",
    "Toronto": "toronto", "Mexico City": "mexico-city",
    "São Paulo": "sao-paulo", "Buenos Aires": "buenos-aires",
    "Panama City": "panama-city", "Cape Town": "cape-town", "Lagos": "lagos",
}

# ── Bracket parsing ──────────────────────────────────────────────────────────

import re
# Case-insensitive so we match both "°F" and (after .lower()) "°f"
_RANGE = re.compile(r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°([fc])", re.IGNORECASE)
_LOW   = re.compile(r"(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+below",  re.IGNORECASE)
_HIGH  = re.compile(r"(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+higher", re.IGNORECASE)


def parse_question(q: str) -> tuple[float, float, str] | None:
    """Return (low_native, high_native, unit) or None."""
    q = q.lower()
    m = _RANGE.search(q)
    if m: return (float(m.group(1)), float(m.group(2)) + 1.0 - 1e-9, m.group(3).upper())
    m = _LOW.search(q)
    if m: return (-1000.0, float(m.group(1)) + 1.0 - 1e-9, m.group(2).upper())
    m = _HIGH.search(q)
    if m: return (float(m.group(1)), 1000.0, m.group(2).upper())
    return None


def _f_to_c(t: float) -> float: return (t - 32.0) * 5.0 / 9.0
def _c_to_f(t: float) -> float: return t * 9.0 / 5.0 + 32.0


# ── HTTP caching layer ───────────────────────────────────────────────────────

def _cache_get(path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _cache_put(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body))


def fetch_polymarket_event(city: str, d: date) -> dict | None:
    slug = f"highest-temperature-in-{CITY_SLUG[city]}-on-{d.strftime('%B-%-d-%Y').lower()}"
    cache_path = CACHE_DIR / f"poly_{slug}.json"
    cached = _cache_get(cache_path)
    if cached is not None:
        return cached if cached.get("_found") else None
    try:
        r = requests.get(f"{POLY_BASE}/events/slug/{slug}", timeout=10)
    except Exception:
        time.sleep(0.5)
        return None
    if not r.ok:
        _cache_put(cache_path, {"_found": False})
        return None
    j = r.json()
    j["_found"] = True
    _cache_put(cache_path, j)
    time.sleep(0.05)
    return j


def fetch_open_meteo(lat: float, lon: float, d: date) -> dict | None:
    key = f"meteo_{lat:.4f}_{lon:.4f}_{d.isoformat()}.json"
    cache_path = CACHE_DIR / key
    cached = _cache_get(cache_path)
    if cached is not None:
        return cached if cached.get("_found") else None
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "models": METEO_MODELS,
        "start_date": d.isoformat(), "end_date": d.isoformat(),
        "timezone": "UTC",
    }
    try:
        r = requests.get(METEO_BASE, params=params, timeout=25)
    except Exception:
        time.sleep(0.5)
        return None
    if not r.ok:
        _cache_put(cache_path, {"_found": False})
        return None
    j = r.json()
    j["_found"] = True
    _cache_put(cache_path, j)
    time.sleep(0.05)
    return j


# ── Per-city forecast extraction ─────────────────────────────────────────────

def forecast_stats_c(city: str, d: date) -> tuple[float, float, int] | None:
    """Return (mean_c, std_c, n_models) for (city, date) using historical
    deterministic models. Returns None on missing data."""
    coords = STATION_LATLON.get(city)
    if not coords: return None
    lat, lon = coords
    j = fetch_open_meteo(lat, lon, d)
    if not j: return None
    daily = j.get("daily", {})
    keys = [k for k in daily if k.startswith("temperature_2m_max_")]
    vals = []
    for k in keys:
        v = daily.get(k)
        if v and v[0] is not None:
            vals.append(float(v[0]))
    if len(vals) < 3:    # require ≥3 model agreement
        return None
    mean = sum(vals) / len(vals)
    var  = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
    std  = max(STD_FLOOR_C, math.sqrt(var))
    return (mean, std, len(vals))


# ── Polymarket bracket extraction + winner ───────────────────────────────────

def extract_brackets_and_winner(event: dict, city: str) -> tuple[list[dict], str | None]:
    """Return (brackets, winning_label_or_None).
    Each bracket is {label, low_c, high_c, condition_id, yes_price, no_price}."""
    brackets = []
    winner_label = None
    for m in event.get("markets", []):
        q = m.get("question", "")
        parsed = parse_question(q)
        if not parsed: continue
        low_n, high_n, unit = parsed
        if unit == "F":
            low_c, high_c = _f_to_c(low_n), _f_to_c(high_n)
        else:
            low_c, high_c = low_n, high_n
        # Outcome label is what shows in the bracket — derive a compact label
        if "or below" in q.lower():
            label = f"≤{int(high_n):d}°{unit}"
        elif "or higher" in q.lower():
            label = f"≥{int(low_n):d}°{unit}"
        else:
            # "between A-B" — we used floor for low, +1 for high; show as "A-B°U"
            label = f"{int(low_n):d}-{int(high_n - 0.5):d}°{unit}"
        op = m.get("outcomePrices", "")
        if isinstance(op, str):
            try: op = json.loads(op)
            except Exception: op = []
        yes_price = float(op[0]) if op else None
        # Winner detection: outcomePrices = ["1", "0"] when YES wins.
        if yes_price is not None and yes_price >= 0.999:
            winner_label = label
        brackets.append({
            "label":         label,
            "low_c":         low_c,
            "high_c":        high_c,
            "yes_price":     yes_price,
            "condition_id":  m.get("conditionId", ""),
        })
    return brackets, winner_label


# ── Per-bracket probability (Gaussian) ───────────────────────────────────────

def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0: sigma = 0.5
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def bracket_prob_yes(bracket: dict, mean_c: float, std_c: float, city: str) -> float:
    """Use Gaussian CDF in the city's native unit to match the live pipeline."""
    unit = CITY_UNITS.get(city, "C")
    if unit == "F":
        mu, sigma = _c_to_f(mean_c), std_c * 9.0 / 5.0
        lo, hi = _c_to_f(bracket["low_c"]), _c_to_f(bracket["high_c"])
    else:
        mu, sigma = mean_c, std_c
        lo, hi = bracket["low_c"], bracket["high_c"]
    return max(0.0, _normal_cdf(hi, mu, sigma) - _normal_cdf(lo, mu, sigma))


# ── Walk-forward assembly ────────────────────────────────────────────────────

@dataclass
class CellResult:
    city: str
    forecast_date: date
    bracket: dict
    winner_label: str          # which bracket actually won
    yes_price: float
    no_price: float
    prob_yes: float
    prob_no: float
    edge_yes: float
    edge_no: float
    gate_passed_yes: bool
    gate_passed_no: bool
    ranked_no: int | None
    side_chosen: str | None
    size: float
    won: bool
    pnl: float
    bias_applied: float
    n_train: int


def main() -> int:
    cities = list(CITY_SLUG.keys())
    start_date = END_DATE - timedelta(days=WINDOW_DAYS)
    print(f"Backtest window: {start_date} → {END_DATE}  ({WINDOW_DAYS} days)")
    print(f"Cities: {len(cities)}")
    print(f"Cache dir: {CACHE_DIR}")
    print()

    # ── PHASE 1: build city-history maps for walk-forward training ────────
    # We need, per city, a list of (date, raw_mean_c, winning_bracket_mid_c)
    # ordered by date so compute_walk_forward_correction can filter < as_of_date.
    print("Phase 1: building training maps (full pull may take 10-20 min on cold cache)")
    city_history: dict[str, list[ResolvedForecast]] = defaultdict(list)
    cells_total = 0
    cells_with_data = 0
    cells_with_winner = 0

    universe: list[tuple[str, date, dict, str | None, float, float]] = []
    # (city, date, brackets, winner_label, raw_mean_c, std_c)

    d = start_date
    while d <= END_DATE:
        if d in SKIP_DATES:
            d += timedelta(days=1); continue
        for city in cities:
            cells_total += 1
            stats = forecast_stats_c(city, d)
            if not stats: continue
            mean_c, std_c, _ = stats
            event = fetch_polymarket_event(city, d)
            if not event: continue
            brackets, winner_label = extract_brackets_and_winner(event, city)
            if not brackets: continue
            cells_with_data += 1
            if winner_label:
                cells_with_winner += 1
                # Find the winning bracket's midpoint
                win_b = next((b for b in brackets if b["label"] == winner_label), None)
                if win_b:
                    mid_c = 0.5 * (win_b["low_c"] + win_b["high_c"])
                    # Clamp tail brackets to a reasonable bound
                    if mid_c < -50: mid_c = win_b["high_c"]
                    if mid_c >  60: mid_c = win_b["low_c"]
                    city_history[city].append(
                        ResolvedForecast(d, mean_c, mid_c)
                    )
            universe.append((city, d, brackets, winner_label, mean_c, std_c))
        if (d - start_date).days % 10 == 0:
            print(f"  ...{d}  cells_total={cells_total}  with_forecast+market={cells_with_data}  with_winner={cells_with_winner}")
        d += timedelta(days=1)
    print()
    print(f"Total cells: {cells_total}  with both forecast+market data: {cells_with_data}  with winner: {cells_with_winner}")
    print(f"City-history rows: {sum(len(h) for h in city_history.values())}  cities w/ history: {len(city_history)}")
    print()

    # ── PHASE 2: walk-forward simulation per (city, date) ──────────────────
    # For each cell, recompute the bias using only data with forecast_date < d.
    print("Phase 2: walk-forward simulation")
    results: list[CellResult] = []
    for (city, d, brackets, winner_label, raw_mean_c, std_c) in universe:
        if not winner_label:
            continue
        bias_c, n_train = compute_walk_forward_correction(
            city, d, city_history[city],
            min_samples=MIN_TRAIN_N, cap_abs=CAP_BIAS_C,
        )
        corrected_mean_c = raw_mean_c + bias_c

        candidates_no: list[CellResult] = []
        cell_results: list[CellResult] = []
        for b in brackets:
            yp = b.get("yes_price")
            if yp is None: continue
            np_ = round(1.0 - yp, 4)
            prob_yes = bracket_prob_yes(b, corrected_mean_c, std_c, city)
            prob_no  = 1.0 - prob_yes
            edge_yes = prob_yes - yp
            edge_no  = prob_no  - np_
            gate_yes = (prob_yes >= NEW_MIN_PROB) and (edge_yes >= NEW_EDGE)
            gate_no  = (prob_no  >= NEW_MIN_PROB) and (edge_no  >= NEW_EDGE)
            won_yes  = (b["label"] == winner_label)
            cell = CellResult(
                city=city, forecast_date=d, bracket=b,
                winner_label=winner_label or "",
                yes_price=yp, no_price=np_,
                prob_yes=prob_yes, prob_no=prob_no,
                edge_yes=edge_yes, edge_no=edge_no,
                gate_passed_yes=gate_yes, gate_passed_no=gate_no,
                ranked_no=None, side_chosen=None, size=0.0,
                won=False, pnl=0.0,
                bias_applied=bias_c, n_train=n_train,
            )
            cell_results.append(cell)
            if gate_no:
                candidates_no.append(cell)
        # Top-N NO selection
        candidates_no.sort(key=lambda c: c.edge_no, reverse=True)
        for rank, c in enumerate(candidates_no[:TOP_N_PER_CITY], 1):
            c.ranked_no = rank
            c.side_chosen = "NO"
            c.size = TRADE_SIZE_USD
            # NO wins when YES loses (= this bracket is NOT the winner)
            won = (c.bracket["label"] != winner_label)
            c.won = won
            if won:
                c.pnl = TRADE_SIZE_USD * (1.0 - c.no_price) / c.no_price
            else:
                c.pnl = -TRADE_SIZE_USD
        results.extend(cell_results)

    # ── PHASE 3: stats ─────────────────────────────────────────────────────
    fired = [r for r in results if r.side_chosen]
    print(f"\nFired trades: {len(fired)}  (top-{TOP_N_PER_CITY} per city per day, NO side)")
    if not fired:
        print("  NO TRADES FIRED — gate may be too strict on out-of-sample data.")
        # Continue to analyze full universe even with 0 fires
    else:
        wins = sum(1 for r in fired if r.won)
        pnl  = sum(r.pnl for r in fired)
        print(f"  wins:    {wins} / {len(fired)} = {wins/len(fired):.1%}")
        print(f"  P&L:     ${pnl:+.2f} @ ${TRADE_SIZE_USD}/trade")
        print(f"  $/trade: ${pnl/len(fired):+.3f}")

    # Calibration on full universe (NO-side gate-passers only — every bracket
    # that would have triggered NO, regardless of top-N selection)
    print("\n=== CALIBRATION (NO-side gate-passers, full universe) ===")
    bins: dict[float, list[int]] = defaultdict(lambda: [0, 0])  # n, wins
    for r in results:
        if not r.gate_passed_no or not r.winner_label:
            continue
        won = (r.bracket["label"] != r.winner_label)
        b = round(r.prob_no * 10) / 10
        bins[b][0] += 1
        bins[b][1] += int(won)
    print(f"  {'prob':>5} | {'n':>6} | {'win_rate':>8}")
    for b in sorted(bins):
        n, w = bins[b]
        if n >= 10:
            print(f"  {b:>5.1f} | {n:>6d} | {w/n:>7.1%}")

    # ROI by price bucket (the senior dev's specific request)
    print("\n=== ROI BY NO-PRICE BUCKET (fired trades only) ===")
    pb: dict[float, list[float]] = defaultdict(list)
    for r in fired:
        b = round(r.no_price * 10) / 10
        pb[b].append(r.pnl)
    print(f"  {'price':>5} | {'n':>4} | {'avg P&L':>9} | {'ROI':>7}")
    for b in sorted(pb):
        ps = pb[b]
        avg = sum(ps) / len(ps)
        roi = avg / TRADE_SIZE_USD
        print(f"  {b:>5.2f} | {len(ps):>4d} | ${avg:>+7.3f} | {roi:>+6.1%}")

    # Per-day P&L (fired only)
    print("\n=== PER-DAY P&L (fired NO trades only, $5/trade) ===")
    by_day: dict[date, list[float]] = defaultdict(list)
    for r in fired:
        by_day[r.forecast_date].append(r.pnl)
    days_sorted = sorted(by_day)
    if days_sorted:
        print(f"  {'date':12} | {'n':>4} | {'wins':>4} | {'P&L':>9}")
        for d in days_sorted:
            ps = by_day[d]
            w = sum(1 for x in ps if x > 0)
            print(f"  {d}    | {len(ps):>4d} | {w:>4d} | ${sum(ps):>+8.2f}")
        print(f"\n  total days: {len(days_sorted)}")
        print(f"  +days: {sum(1 for d in days_sorted if sum(by_day[d]) > 0)}  -days: {sum(1 for d in days_sorted if sum(by_day[d]) < 0)}")
        print(f"  worst day: ${min(sum(by_day[d]) for d in days_sorted):+.2f}")
        print(f"  best  day: ${max(sum(by_day[d]) for d in days_sorted):+.2f}")

    # Per-city P&L
    print("\n=== PER-CITY P&L (fired NO trades only, $5/trade) ===")
    by_city: dict[str, list[float]] = defaultdict(list)
    for r in fired:
        by_city[r.city].append(r.pnl)
    sorted_cities = sorted(by_city.items(), key=lambda kv: sum(kv[1]), reverse=True)
    print(f"  {'city':16} | {'n':>4} | {'WR':>5} | {'P&L':>9}")
    for c, ps in sorted_cities:
        w = sum(1 for x in ps if x > 0)
        print(f"  {c:16} | {len(ps):>4d} | {w/len(ps):>4.0%} | ${sum(ps):>+8.2f}")

    # Save raw CSV
    out_csv = CACHE_DIR / "results.csv"
    with out_csv.open("w") as f:
        f.write("city,forecast_date,bracket,yes_price,no_price,prob_yes,prob_no,edge_yes,edge_no,gate_yes,gate_no,bias_c,n_train,ranked_no,side_chosen,size,won,pnl\n")
        for r in results:
            f.write(f"{r.city},{r.forecast_date},{r.bracket['label']},{r.yes_price},{r.no_price},"
                    f"{r.prob_yes:.4f},{r.prob_no:.4f},{r.edge_yes:.4f},{r.edge_no:.4f},"
                    f"{int(r.gate_passed_yes)},{int(r.gate_passed_no)},{r.bias_applied:.3f},{r.n_train},"
                    f"{r.ranked_no or ''},{r.side_chosen or ''},{r.size},{int(r.won)},{r.pnl:.3f}\n")
    print(f"\n  raw CSV saved: {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
