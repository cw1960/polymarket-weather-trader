"""
backtest_counterfactual.py — replay resolved Phase 1 signals under the
post-fix forecast pipeline by regenerating each signal's model probability
from stored ensemble members shifted by (new_forecast_bias - old_delta_matrix).

Method (no live API calls, all data from Supabase):

  1. For each resolved Phase 1 signal:
       - find its ensemble_forecasts row (latest model_run for that
         (city, forecast_date)),
       - read stored members (raw_members + ecmwf_members),
       - compute shift = forecast_bias[city] - delta_matrix[city, month],
         where forecast_bias is the new per-city value from
         scripts/forecast_bias.py and delta_matrix is the buggy
         month-averaged value the old fetch_forecasts.py was applying.

  2. Apply the shift to every member to get the post-fix member pool.

  3. Parse the signal's outcome bracket (e.g. "56-57°F" or "23°C") into
     [low, high] bounds in the market's native unit.

  4. model_prob_new = (count of shifted members in [low, high]) / n_members.

  5. Apply the proposed gate:
       fire if (model_prob_new - market_price) >= 0.08
              AND model_prob_new >= 0.55
       (for NO side: flip the comparison — the bot would have placed NO
        when model thinks the bracket loses by enough margin.)

  6. Simulate P&L: $15 size, profit (1-p)/p * 15 if won, -15 if lost.

LIMITATIONS:

  - delta_matrix month-average is a noisy reconstruction of what
    fetch_forecasts.py was actually adding pre-fix (the live code
    averaged all comparison-station rows for that city+month). We use
    the same averaging logic to mirror it faithfully.

  - std_high is left unchanged. The pre-fix code broadened std by
    delta_std (matrix); the post-fix code uses delta_std=0. Strict
    recovery would tighten std for some city/month pairs. We do not
    do this because member-counting (which we use) does not depend on
    std — it only depends on the member values themselves.

  - We exclude oracle-bug-affected resolutions per the existing
    skip list and any winning_bracket='VOIDED' rows.

  - Real Phase 2 probabilities are computed at lock time from running
    temperature, NOT from forecasts. This backtest is a Phase 1
    sanity check that the new pipeline produces meaningfully different
    (hopefully sharper, less-inverted) probabilities. If Phase 1
    calibration looks honest under regen, Phase 2 should also benefit;
    if Phase 1 is still flat/inverted, the forecast-bias fix didn't
    work and we have a larger problem.
"""
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, str(Path(__file__).parent))

from supabase import create_client  # noqa: E402
from forecast_bias import get_correction as get_forecast_bias  # noqa: E402

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

ORACLE_BUG = {
    ("Miami", "2026-05-17"),
    ("Mexico City", "2026-05-17"),
    ("Seoul", "2026-05-17"),
    ("Hong Kong", "2026-05-17"),
}

TRADE_SIZE = 15.0
NEW_EDGE = 0.08
NEW_MIN_PROB = 0.55

# ── Bracket parsing ──────────────────────────────────────────────────────────

_RANGE_F = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_RANGE_C = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")
_SINGLE_F = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_SINGLE_C = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")
_TAIL_LOW_F = re.compile(r"^\s*<\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_TAIL_HIGH_F = re.compile(r"^\s*>\s*(-?\d+(?:\.\d+)?)\s*°F\s*$")
_TAIL_LOW_C = re.compile(r"^\s*<\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")
_TAIL_HIGH_C = re.compile(r"^\s*>\s*(-?\d+(?:\.\d+)?)\s*°C\s*$")


def _f_to_c(t: float) -> float:
    return (t - 32.0) * 5.0 / 9.0


def parse_bracket(outcome: str) -> tuple[float, float, str] | None:
    """Return (low_c, high_c, original_unit) or None for unrecognized formats."""
    if not outcome:
        return None
    s = outcome.strip()
    m = _RANGE_F.match(s)
    if m: return (_f_to_c(float(m.group(1))), _f_to_c(float(m.group(2))), "F")
    m = _RANGE_C.match(s)
    if m: return (float(m.group(1)), float(m.group(2)), "C")
    m = _SINGLE_F.match(s)
    if m:
        t = float(m.group(1))
        return (_f_to_c(t - 0.5), _f_to_c(t + 0.5), "F")
    m = _SINGLE_C.match(s)
    if m:
        t = float(m.group(1))
        return (t - 0.5, t + 0.5, "C")
    m = _TAIL_LOW_F.match(s)
    if m:
        return (-100.0, _f_to_c(float(m.group(1))), "F")
    m = _TAIL_HIGH_F.match(s)
    if m:
        return (_f_to_c(float(m.group(1))), 100.0, "F")
    m = _TAIL_LOW_C.match(s)
    if m:
        return (-100.0, float(m.group(1)), "C")
    m = _TAIL_HIGH_C.match(s)
    if m:
        return (float(m.group(1)), 100.0, "C")
    return None


# ── delta_matrix lookup that mirrors the old fetch_forecasts.get_delta() ─────

_delta_cache: dict[tuple[str, int], float] = {}


def old_delta_mean(city: str, month: int) -> float:
    key = (city, month)
    if key in _delta_cache:
        return _delta_cache[key]
    try:
        r = (sb.table("delta_matrix")
             .select("delta_mean")
             .eq("city", city).eq("month", month).execute())
        rows = r.data or []
        v = float(sum(x["delta_mean"] for x in rows) / len(rows)) if rows else 0.0
    except Exception:
        v = 0.0
    _delta_cache[key] = v
    return v


# ── forecast lookup ──────────────────────────────────────────────────────────

_forecast_cache: dict[tuple[str, str], dict | None] = {}


def get_forecast_row(city: str, forecast_date: str) -> dict | None:
    key = (city, forecast_date)
    if key in _forecast_cache:
        return _forecast_cache[key]
    try:
        r = (sb.table("ensemble_forecasts")
             .select("city,forecast_date,model_run,mean_high,std_high,raw_members,ecmwf_members,created_at")
             .eq("city", city).eq("forecast_date", forecast_date)
             .order("created_at", desc=True).limit(1).execute())
        out = r.data[0] if r.data else None
    except Exception:
        out = None
    _forecast_cache[key] = out
    return out


# ── Phase 1 signals fetch (paginated) ────────────────────────────────────────

def fetch_signals():
    out = []
    page = 0
    while True:
        r = (sb.table("trade_signals")
             .select("forecast_date,city,side,outcome,market_price,model_probability,"
                     "actual_outcome,winning_bracket,signal_phase,signal_time")
             .eq("signal_phase", "phase1")
             .not_.is_("winning_bracket", "null")
             .not_.is_("market_price", "null")
             .not_.eq("winning_bracket", "VOIDED")
             .range(page * 1000, (page + 1) * 1000 - 1).execute())
        if not r.data:
            break
        out.extend(r.data)
        if len(r.data) < 1000:
            break
        page += 1
    return out


# ── Regen probability for one signal ─────────────────────────────────────────

def regen_prob(signal: dict) -> tuple[float, int] | None:
    """Return (model_prob_new, n_members) or None if cannot regenerate."""
    city = signal["city"]
    fdate = signal["forecast_date"]
    forecast = get_forecast_row(city, fdate)
    if not forecast:
        return None
    members = (forecast.get("raw_members") or []) + (forecast.get("ecmwf_members") or [])
    members = [float(m) for m in members if m is not None]
    if not members:
        return None
    month = datetime.fromisoformat(fdate).month
    delta_new = get_forecast_bias(city)
    delta_old = old_delta_mean(city, month)
    shift = delta_new - delta_old
    shifted = [m + shift for m in members]
    parsed = parse_bracket(signal.get("outcome") or "")
    if not parsed:
        return None
    low_c, high_c, _unit = parsed
    n_in = sum(1 for m in shifted if low_c <= m <= high_c)
    return (n_in / len(shifted), len(shifted))


# ── Simulation & reporting ───────────────────────────────────────────────────

def simulate(rows, predicate, label):
    fires = wins = 0
    pnl = 0.0
    prices = []
    by_side = defaultdict(lambda: [0, 0, 0.0])
    by_prob_bin = defaultdict(lambda: [0, 0])
    for r in rows:
        if not predicate(r):
            continue
        fires += 1
        p = r["market_price"]
        # For NO-side trades the historical row stored side='NO' with
        # market_price = price of NO outcome.  actual_outcome='false'
        # means YES bracket lost → NO wins. So:
        side = r.get("side") or "YES"
        if side == "NO":
            won = (r.get("actual_outcome") == "false")
        else:
            won = (r.get("actual_outcome") == "true")
        if won:
            wins += 1
            pnl += TRADE_SIZE * (1.0 - p) / p
        else:
            pnl -= TRADE_SIZE
        prices.append(p)
        by_side[side][0] += 1
        by_side[side][1] += int(won)
        by_side[side][2] += (TRADE_SIZE * (1.0 - p) / p) if won else -TRADE_SIZE
        mp = r.get("_regen_prob_for_side") or 0
        bn = round(mp * 10) / 10
        by_prob_bin[bn][0] += 1
        by_prob_bin[bn][1] += int(won)

    print(f"\n=== {label} ===")
    print(f"  trades fired:     {fires}")
    if not fires:
        return
    print(f"  wins / losses:    {wins} / {fires - wins}")
    print(f"  win rate:         {wins/fires:.1%}")
    print(f"  simulated P&L:    ${pnl:+.2f}  (size ${TRADE_SIZE}/trade)")
    print(f"  P&L per trade:    ${pnl/fires:+.2f}")
    print(f"  avg buy price:    {sum(prices)/len(prices):.3f}")
    ps = sorted(prices)
    q = lambda f: ps[int(len(ps) * f)] if ps else 0
    print(f"  price distribution: p10={q(0.10):.2f}  p25={q(0.25):.2f}  "
          f"p50={q(0.50):.2f}  p75={q(0.75):.2f}  p90={q(0.90):.2f}")
    print(f"  by side:")
    for side, (f, w, p) in by_side.items():
        if f:
            print(f"    {side}: trades={f}  wins={w}  rate={(w/f):.1%}  pnl=${p:+.2f}")
    print(f"  calibration (regen model_prob_side bin → actual win rate):")
    for b in sorted(by_prob_bin):
        f, w = by_prob_bin[b]
        if f >= 5:
            print(f"    {b:.1f}: n={f}  rate={(w/f):.1%}")


def main():
    signals = fetch_signals()
    pre = len(signals)
    signals = [s for s in signals if (s["city"], s["forecast_date"]) not in ORACLE_BUG]
    print(f"fetched {pre} resolved Phase 1 signals; after oracle-bug filter: {len(signals)}")

    # Regenerate probabilities
    skipped_no_forecast = 0
    skipped_no_bracket = 0
    regen_count = 0
    for s in signals:
        result = regen_prob(s)
        if result is None:
            if get_forecast_row(s["city"], s["forecast_date"]) is None:
                skipped_no_forecast += 1
            else:
                skipped_no_bracket += 1
            s["_regen_prob_yes"] = None
            continue
        s["_regen_prob_yes"], _n = result
        # For NO-side: prob of NO winning = 1 - prob YES wins
        s["_regen_prob_for_side"] = (1 - s["_regen_prob_yes"]) if s.get("side") == "NO" else s["_regen_prob_yes"]
        regen_count += 1

    print(f"regen: ok={regen_count}  no_forecast={skipped_no_forecast}  no_bracket_parse={skipped_no_bracket}")

    have_regen = [s for s in signals if s.get("_regen_prob_for_side") is not None]

    # Calibration audit using regen probabilities
    print("\n=== CALIBRATION (regen prob for traded side vs actual win) ===")
    bins = defaultdict(lambda: [0, 0])
    for s in have_regen:
        p = s["_regen_prob_for_side"]
        won = (s.get("actual_outcome") == "true") if s.get("side") == "YES" else (s.get("actual_outcome") == "false")
        b = round(p * 10) / 10
        bins[b][0] += 1
        bins[b][1] += int(won)
    print("  bin | n     | actual_win_rate")
    for b in sorted(bins):
        n, w = bins[b]
        if n >= 5:
            print(f"  {b:.1f} | {n:5d} | {(w/n):.1%}")

    # OLD price-cap rule using regen prob
    simulate(have_regen,
             lambda r: r["side"] == "YES"
                       and r["market_price"] < 0.30
                       and (r["_regen_prob_for_side"] - r["market_price"]) > 0,
             label="OLD rule (price<0.30, YES, positive regen edge)")

    # NEW gate, both sides
    simulate(have_regen,
             lambda r: (r["_regen_prob_for_side"] - r["market_price"]) >= NEW_EDGE
                       and r["_regen_prob_for_side"] >= NEW_MIN_PROB,
             label=f"NEW gate: regen_edge>={NEW_EDGE}, regen_prob>={NEW_MIN_PROB} (BOTH sides)")

    # NEW gate, YES only
    simulate(have_regen,
             lambda r: r["side"] == "YES"
                       and (r["_regen_prob_for_side"] - r["market_price"]) >= NEW_EDGE
                       and r["_regen_prob_for_side"] >= NEW_MIN_PROB,
             label=f"NEW gate, YES only")

    # NEW gate, NO only
    simulate(have_regen,
             lambda r: r["side"] == "NO"
                       and (r["_regen_prob_for_side"] - r["market_price"]) >= NEW_EDGE
                       and r["_regen_prob_for_side"] >= NEW_MIN_PROB,
             label=f"NEW gate, NO only (sweep proxy)")

    # Sensitivities
    for e, m in [(0.05, 0.55), (0.10, 0.55), (0.08, 0.60), (0.08, 0.50)]:
        simulate(have_regen,
                 lambda r, e=e, m=m: (r["_regen_prob_for_side"] - r["market_price"]) >= e
                                     and r["_regen_prob_for_side"] >= m,
                 label=f"SENSITIVITY: edge>={e}, min_prob>={m}")


if __name__ == "__main__":
    main()
