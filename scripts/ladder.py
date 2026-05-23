"""
Laddered temperature grid strategy — core construction logic.

Philosophy: place many small bets across a band of adjacent brackets around
the forecast center. Most rungs expire worthless; the occasional rung that
hits pays 5-100x+ and covers the entire ladder cost with room to spare.

YES rungs: model_prob > market_price  — we think the bracket is underpriced.
NO  rungs: market_price > model_prob  — we think the bracket is overpriced.
           market_price stored = NO price = 1 - yes_price (cost per share).
           A NO rung wins when its bracket does NOT resolve YES.

Probability source (in priority order)
---------------------------------------
1. Direct member counting from combined GFS+ECMWF ensemble (82 members).
   Pass `members_c` (list of daily-high temps in Celsius) to build_ladder().
2. Gaussian fallback using mean_c / std_c when no members are available.

Sizing
------
Base: flat rung_size_usd per YES rung; rung_size_usd * no_size_factor per NO rung.
Conviction premium (YES only): the single YES rung with the highest edge gets
  an additional allocation proportional to its relative edge over the market price.
  Premium = clamp((edge_ratio - conviction_threshold) / 0.3, 0, 1)
            * (conviction_max_usd - rung_size_usd)
NO rungs never receive conviction premium.

Consensus filter
----------------
If consensus_spread_c > max_spread_c (default 3.0 C), the 6 deterministic
models disagree too much — all rungs are skipped for that city/date.

The total ladder cost (YES + NO combined) is capped per (city, date) market.
"""
import math
from scipy.stats import norm
from config import CITY_UNITS

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

LADDER_DEFAULTS: dict = {
    # Band boundaries (sigma from mean)
    # Wings disabled - Pass 1 showed tails are badly miscalibrated (0.15x ratio at 2sigma+)
    "core_sigma":       2.0,
    "wing_sigma":       2.0,

    # Market price eligibility (YES price in $)
    "core_min_price":   0.02,   # 2c  - below this, spread is too wide to fill reliably
    "core_max_price":   0.30,   # 30c - raised to capture near-money brackets (most liquid)
    "wing_max_price":   0.00,   # 0 = wings truly disabled (no price is <= 0)

    # Minimum calibration-adjusted probability to consider a bucket (YES side)
    "min_model_prob_core": 0.05,   # 5% - focus on meaningful probabilities only
    "min_model_prob_wing": 0.002,  # unused

    # Calibration correction factors from Pass 1 (empirical hit rate / GFS model prob)
    # Applied to model_prob before computing edge: corrected_prob = model_prob * factor
    "calibration_factors": {
        0.0:  1.08,   # 0.0-0.5 sigma: GFS underestimates slightly
        0.5:  1.02,   # 0.5-1.0 sigma: near-perfect
        1.0:  0.93,   # 1.0-1.5 sigma: GFS overestimates slightly
        1.5:  0.76,   # 1.5-2.0 sigma: GFS overestimates by 24%
        2.0:  0.15,   # 2.0-2.5 sigma: essentially never happens - excluded by core_sigma
    },

    # Sizing - calibrated for a ~$1,000 bankroll
    # Adjust rung_size_usd / conviction_max_usd to scale with your bankroll.
    # The signal engine applies a total_run_cap_usd safety cap on top of these.
    "rung_size_usd":    1.00,   # $ per core YES rung (base allocation)
    "wing_size_usd":    1.00,   # $ per wing rung
    "max_market_usd":   10.00,  # hard cap per (city, date) market (YES + NO combined)

    # EV filter using calibration-corrected probability
    "min_ev_core":      0.0,    # only trade YES when corrected_prob >= market price
    "min_ev_wing":     -0.003,  # unused

    # Conviction sizing - extra allocation on the single top-edge core YES rung per event
    # Premium scales linearly from 0 at conviction_threshold to full at threshold+0.30
    # Note: NO rungs never receive conviction premium.
    # Set equal to rung_size_usd to disable conviction premium entirely.
    # Phase 1 is now a flat $1 tracking layer; all meaningful capital goes to Phase 2.
    "conviction_max_usd":    1.00,   # max total $ on the top YES rung (base + premium)
    "conviction_threshold":  0.20,   # relative edge (ev/price) needed before premium kicks in

    # NO-side trading - short overpriced brackets
    # A NO rung fires when: yes_price - corrected_prob >= min_ev_no
    # and the YES price sits in [no_min_yes_price, no_max_yes_price].
    # market_price stored for NO rungs = NO price = 1 - yes_price (actual cost per share).
    "no_min_yes_price": 0.10,   # 10c YES floor - below this the NO return is tiny
    "no_max_yes_price": 0.85,   # 85c YES cap  - above this the bracket is near-certain
    "no_size_factor":   1.00,   # NO rungs at full parity with YES core rungs (raised from 0.75)
    "min_ev_no":        0.05,   # minimum NO edge (yes_price - model_prob) to fire

    # Consensus filter - skip the entire event when models disagree too much
    "max_spread_c":     3.0,    # C; None = disabled
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _f_to_c(t: float) -> float:
    """Convert Fahrenheit to Celsius (for bucket boundary comparisons against C members)."""
    return (t - 32) * 5 / 9


def _bucket_prob_from_members(
    members_c: list[float], low: float, high: float, unit: str
) -> float:
    """
    Direct empirical probability: fraction of ensemble members whose daily
    high falls inside [low, high) in the market's display unit.

    Members are in Celsius; bucket boundaries are in the market unit (F or C).
    Converts boundaries to Celsius before counting.
    """
    if not members_c:
        return 0.0
    INF = 9000.0

    if unit == "F":
        low_c  = _f_to_c(low)  if low  > -INF else -INF
        high_c = _f_to_c(high) if high <  INF else  INF
    else:
        low_c, high_c = low, high

    count = sum(
        1 for m in members_c
        if (low_c  <= -INF or m >= low_c)
        and (high_c >= INF  or m <  high_c)
    )
    return count / len(members_c)


def _bucket_mid(low: float, high: float, mean: float) -> float:
    """Midpoint of a bucket, clamped for tail buckets."""
    INF = 9000.0
    if low <= -INF and high >= INF:
        return mean
    if low <= -INF:
        return high - 1.0
    if high >= INF:
        return low + 1.0
    return (low + high) / 2.0


def _bucket_prob(low: float, high: float, mean: float, std: float) -> float:
    """P(outcome in [low, high]) under N(mean, std)."""
    INF = 9000.0
    if std <= 0:
        return 0.0
    lo_cdf = norm.cdf(low,  mean, std) if low  > -INF else 0.0
    hi_cdf = norm.cdf(high, mean, std) if high <  INF else 1.0
    return max(0.0, float(hi_cdf - lo_cdf))


def _to_market_unit(mean_c: float, std_c: float, unit: str) -> tuple[float, float]:
    """Convert mean/std from Celsius storage to the market's display unit."""
    if unit == "F":
        return mean_c * 9 / 5 + 32, std_c * 9 / 5
    return mean_c, std_c


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_ladder(
    mean_c: float,
    std_c: float,
    buckets: list[dict],
    city: str,
    config: dict | None = None,
    members_c: list[float] | None = None,
    consensus_spread_c: float | None = None,
) -> list[dict]:
    """
    Construct a ladder of rungs for one (city, date) market.

    Parameters
    ----------
    mean_c             : corrected forecast mean high temperature, Celsius
    std_c              : corrected forecast std, Celsius (already has std floor applied)
    buckets            : list of bucket dicts from fetch_markets.py, each with keys:
                         label, low, high, unit, yes_price, no_price,
                         condition_id, market_id, question
    city               : city name (used for unit lookup)
    config             : optional overrides for LADDER_DEFAULTS
    members_c          : combined GFS+ECMWF ensemble members in Celsius (82 members).
                         When provided, bucket probs are computed by direct member
                         counting instead of Gaussian approximation.
    consensus_spread_c : max spread across 6 deterministic models in Celsius.
                         When this exceeds cfg["max_spread_c"], all rungs are skipped.

    Returns
    -------
    List of rung dicts (may be empty if no eligible buckets found).
    Each rung dict contains all bucket fields plus:
      side, market_price, model_prob, ev, edge, distance_sigma, rung_type, size_usd
    For YES rungs: market_price = yes_price.
    For NO  rungs: market_price = 1 - yes_price (cost per share to buy NO).
    """
    cfg = {**LADDER_DEFAULTS, **(config or {})}

    # Consensus filter: skip when models disagree too much
    max_spread = cfg.get("max_spread_c")
    if (
        max_spread is not None
        and consensus_spread_c is not None
        and consensus_spread_c > max_spread
    ):
        return []

    unit = CITY_UNITS.get(city, "C")
    mean, std = _to_market_unit(mean_c, std_c, unit)

    # Apply std floor in the market unit
    std_floor = 2.7 if unit == "F" else 1.5   # raised from 1.8F/1.0C — mean abs error was 1.25C
    std = max(std, std_floor)

    use_members = bool(members_c)
    rungs: list[dict] = []

    for b in buckets:
        yes_price = b["yes_price"]
        if yes_price <= 0 or yes_price >= 1:
            continue

        low  = b["low"]
        high = b["high"]
        mid  = _bucket_mid(low, high, mean)
        dist = abs(mid - mean) / std  # sigma distance from mean

        if use_members:
            # Direct empirical probability from 82-member ensemble.
            # Calibration factors are still applied — the ensemble can have systematic
            # biases (e.g. warm bias at 1-2 sigma) that member counting won't self-correct.
            model_prob = _bucket_prob_from_members(members_c, low, high, b.get("unit", unit))
            factors    = cfg.get("calibration_factors", {})
            band_floor = max((k for k in factors if k <= dist), default=0.0)
            calib_factor   = factors.get(band_floor, 1.0)
            corrected_prob = model_prob * calib_factor
        else:
            # Gaussian fallback with Pass-1 calibration correction
            model_prob = _bucket_prob(low, high, mean, std)
            factors    = cfg.get("calibration_factors", {})
            band_floor = max((k for k in factors if k <= dist), default=0.0)
            calib_factor   = factors.get(band_floor, 1.0)
            corrected_prob = model_prob * calib_factor

        # ---- YES rung --------------------------------------------------------
        yes_ev     = corrected_prob - yes_price
        in_core_sigma = dist <= cfg["core_sigma"]
        in_wing_sigma = dist <= cfg["wing_sigma"]
        is_core = (
            in_core_sigma
            and cfg["core_min_price"] <= yes_price <= cfg["core_max_price"]
            and corrected_prob >= cfg["min_model_prob_core"]
            and yes_ev >= cfg["min_ev_core"]
        )
        is_wing = (
            not is_core
            and in_wing_sigma
            and yes_price <= cfg["wing_max_price"]
            and model_prob >= cfg["min_model_prob_wing"]
            and yes_ev >= cfg["min_ev_wing"]
        )

        if is_core or is_wing:
            rung_type = "core" if is_core else "wing"
            size      = cfg["rung_size_usd"] if is_core else cfg["wing_size_usd"]
            rungs.append({
                **b,
                "side":            "YES",
                "market_price":    yes_price,
                "model_prob":      round(corrected_prob, 4),
                "raw_model_prob":  round(model_prob, 4),
                "calib_factor":    round(calib_factor, 2),
                "ev":              round(yes_ev, 4),
                "edge":            round(yes_ev, 4),
                "distance_sigma":  round(dist, 2),
                "rung_type":       rung_type,
                "size_usd":        size,
            })

        # ---- NO rung ---------------------------------------------------------
        # Fire when the market OVERPRICES the bracket relative to our model.
        no_edge       = yes_price - corrected_prob
        no_min_yes    = cfg.get("no_min_yes_price", 0.10)
        no_max_yes    = cfg.get("no_max_yes_price", 0.85)
        min_ev_no     = cfg.get("min_ev_no", 0.05)
        no_size_factor = cfg.get("no_size_factor", 0.75)

        is_no = (
            in_core_sigma               # only bracket within the model's core band
            and no_min_yes <= yes_price <= no_max_yes
            and no_edge >= min_ev_no
        )

        if is_no:
            no_price = round(1.0 - yes_price, 4)   # actual cost per share to buy NO
            no_size  = round(cfg["rung_size_usd"] * no_size_factor, 2)
            rungs.append({
                **b,
                "side":            "NO",
                "market_price":    no_price,         # cost per share (NO price)
                "yes_price_ref":   yes_price,        # kept for logging reference
                "model_prob":      round(corrected_prob, 4),
                "raw_model_prob":  round(model_prob, 4),
                "calib_factor":    round(calib_factor, 2),
                "ev":              round(no_edge, 4),
                "edge":            round(no_edge, 4),
                "distance_sigma":  round(dist, 2),
                "rung_type":       "no",
                "size_usd":        no_size,
            })

    if not rungs:
        return []

    # Apply per-market cap (proportional reduction across YES + NO combined)
    total = sum(r["size_usd"] for r in rungs)
    if total > cfg["max_market_usd"]:
        scale = cfg["max_market_usd"] / total
        for r in rungs:
            r["size_usd"] = round(r["size_usd"] * scale, 2)

    # Conviction premium: extra allocation on the single highest-edge YES core rung only
    core_yes_rungs = [r for r in rungs if r["rung_type"] == "core" and r["side"] == "YES"]
    if core_yes_rungs:
        best = max(core_yes_rungs, key=lambda r: r["ev"])
        price = best["market_price"]
        if price > 0:
            edge_ratio           = best["ev"] / price
            conviction_threshold = cfg["conviction_threshold"]
            conviction_max       = cfg["conviction_max_usd"]
            base_size            = cfg["rung_size_usd"]
            if edge_ratio > conviction_threshold:
                t       = min((edge_ratio - conviction_threshold) / 0.30, 1.0)
                premium = t * (conviction_max - base_size)
                best["size_usd"] = round(best["size_usd"] + premium, 2)

    # Sort: YES core first (nearest to mean), then NO rungs, then wings
    rungs.sort(key=lambda r: (
        0 if (r["rung_type"] == "core" and r["side"] == "YES") else
        1 if r["rung_type"] == "no" else 2,
        r["distance_sigma"]
    ))
    return rungs


# ---------------------------------------------------------------------------
# Ladder summary
# ---------------------------------------------------------------------------

def ladder_summary(rungs: list[dict], mean_c: float, std_c: float, city: str) -> dict:
    """Human-readable summary for logging."""
    if not rungs:
        return {}
    unit      = CITY_UNITS.get(city, "C")
    mean, std = _to_market_unit(mean_c, std_c, unit)
    core      = [r for r in rungs if r["rung_type"] == "core"]
    wings     = [r for r in rungs if r["rung_type"] == "wing"]
    no_rungs  = [r for r in rungs if r["rung_type"] == "no"]
    total     = sum(r["size_usd"] for r in rungs)
    return {
        "num_rungs":    len(rungs),
        "num_core":     len(core),
        "num_wings":    len(wings),
        "num_no":       len(no_rungs),
        "total_usd":    round(total, 2),
        "mean":         f"{mean:.1f} {unit}",
        "std":          f"{std:.1f} {unit}",
        "price_range":  (
            f"{min(r['market_price'] for r in rungs)*100:.1f}c-"
            f"{max(r['market_price'] for r in rungs)*100:.1f}c"
        ),
    }


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mock_buckets = [
        {"label": "<=49F", "low": -9999, "high": 49.5,  "unit": "F", "yes_price": 0.002, "no_price": 0.998, "condition_id": "a", "market_id": "1", "question": "..."},
        {"label": "50-51F","low": 49.5,  "high": 51.5,  "unit": "F", "yes_price": 0.005, "no_price": 0.995, "condition_id": "b", "market_id": "2", "question": "..."},
        {"label": "52-53F","low": 51.5,  "high": 53.5,  "unit": "F", "yes_price": 0.015, "no_price": 0.985, "condition_id": "c", "market_id": "3", "question": "..."},
        {"label": "54-55F","low": 53.5,  "high": 55.5,  "unit": "F", "yes_price": 0.06,  "no_price": 0.94,  "condition_id": "d", "market_id": "4", "question": "..."},
        {"label": "56-57F","low": 55.5,  "high": 57.5,  "unit": "F", "yes_price": 0.14,  "no_price": 0.86,  "condition_id": "e", "market_id": "5", "question": "..."},
        {"label": "58-59F","low": 57.5,  "high": 59.5,  "unit": "F", "yes_price": 0.30,  "no_price": 0.70,  "condition_id": "f", "market_id": "6", "question": "..."},
        {"label": "60-61F","low": 59.5,  "high": 61.5,  "unit": "F", "yes_price": 0.25,  "no_price": 0.75,  "condition_id": "g", "market_id": "7", "question": "..."},
        {"label": ">=62F", "low": 61.5,  "high": 9999,  "unit": "F", "yes_price": 0.10,  "no_price": 0.90,  "condition_id": "h", "market_id": "8", "question": "..."},
    ]
    mean_c, std_c = 12.8, 2.0   # NYC April: ~55F
    rungs = build_ladder(mean_c, std_c, mock_buckets, "NYC")
    summary = ladder_summary(rungs, mean_c, std_c, "NYC")
    print(f"NYC ladder: {summary}")
    for r in rungs:
        side = r["side"]
        ref  = r["yes_price"] if side == "YES" else r.get("yes_price_ref", "?")
        print(f"  [{r['rung_type']:4}] {side} {r['label']:<12} "
              f"model={r['model_prob']*100:5.1f}%  "
              f"yes={ref*100 if isinstance(ref, float) else ref:.1f}c  "
              f"pay={r['market_price']*100:5.1f}c  "
              f"ev={r['ev']:+.3f}  dist={r['distance_sigma']:.1f}sigma  "
              f"${r['size_usd']:.2f}")
