"""
Probability distribution over temperature buckets + edge computation.

All internal model values are in Celsius. For cities that resolve in °F,
we convert corrected_mean and corrected_std to Fahrenheit before computing
bucket probabilities so the outcome labels match what Polymarket shows.

Conversion:
  mean_F  = mean_C * 9/5 + 32
  std_F   = std_C  * 9/5   (std scales but does not shift)
"""
import math
from scipy.stats import norm
from config import MIN_EDGE, KELLY_FRACTION, MAX_POSITION_USD, MAX_PCT_BANKROLL, CITY_UNITS


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def get_unit(city: str) -> str:
    return CITY_UNITS.get(city, "C")


def get_polymarket_buckets(city: str, forecast_date: str) -> list[dict]:
    """Return 1-degree buckets covering the plausible temperature range for this city."""
    unit = get_unit(city)
    if unit == "F":
        # Wide enough to cover any US city in any season
        return [
            {"label": f"{t}°F", "low": t - 0.5, "high": t + 0.5}
            for t in range(10, 121)
        ]
    else:
        # Celsius: cover -10 to 50 for global coverage
        return [
            {"label": f"{t}°C", "low": t - 0.5, "high": t + 0.5}
            for t in range(-10, 51)
        ]


def compute_bucket_probabilities(
    corrected_mean_c: float,
    corrected_std_c: float,
    buckets: list[dict],
    city: str = "",
) -> dict[str, float]:
    """
    Compute probability for each bucket.
    Converts mean/std to °F first if the city resolves in Fahrenheit.
    """
    unit = get_unit(city)
    if unit == "F":
        mean = c_to_f(corrected_mean_c)
        std = corrected_std_c * 9 / 5
    else:
        mean = corrected_mean_c
        std = corrected_std_c

    if std <= 0:
        std = 0.5  # prevent degenerate distribution

    probs: dict[str, float] = {}
    for b in buckets:
        p = norm.cdf(b["high"], mean, std) - norm.cdf(b["low"], mean, std)
        probs[b["label"]] = max(0.0, float(p))

    total = sum(probs.values())
    if total > 0 and abs(total - 1.0) > 0.01:
        probs = {k: v / total for k, v in probs.items()}
    return probs


def compute_edge(
    bucket_probabilities: dict[str, float],
    market_prices: dict[str, float],
) -> list[dict]:
    """
    Compare model probabilities to market prices and return signals
    where abs(edge) >= MIN_EDGE. market_prices keys must match bucket labels.
    """
    signals = []
    for outcome, model_prob in bucket_probabilities.items():
        market_price = market_prices.get(outcome)
        if market_price is None or market_price <= 0:
            continue
        edge = model_prob - market_price
        if abs(edge) < MIN_EDGE:
            continue
        side = "YES" if edge > 0 else "NO"
        win_prob = model_prob if side == "YES" else 1 - model_prob
        odds = (1 - market_price) / market_price
        kelly = (win_prob * odds - (1 - win_prob)) / odds if odds > 0 else 0
        signals.append({
            "outcome": outcome,
            "side": side,
            "market_price": round(market_price, 4),
            "model_probability": round(model_prob, 4),
            "edge": round(abs(edge), 4),
            "kelly_fraction": round(max(0.0, kelly), 4),
            "recommended_position": 0.0,
        })
    return sorted(signals, key=lambda x: x["edge"], reverse=True)


def position_size(
    edge: float,
    market_price: float,
    bankroll: float,
    kelly_fraction: float = KELLY_FRACTION,
    max_position: float = MAX_POSITION_USD,
) -> float:
    win_prob = market_price + edge
    odds = (1 - market_price) / market_price if market_price > 0 else 0
    kelly = (win_prob * odds - (1 - win_prob)) / odds if odds > 0 else 0
    size = max(0.0, kelly) * kelly_fraction * bankroll
    return min(size, max_position, bankroll * MAX_PCT_BANKROLL)


def test_with_mock_data():
    """
    Sanity check: NYC (°F market) and London (°C market).
    Verify that outcome labels and edge calculations are in the correct unit.
    """
    print("\n=== MOCK SIGNAL TEST ===\n")

    # NYC — resolves in °F
    nyc_mean_c = 18.5   # ~65°F
    nyc_std_c = 1.5
    buckets_nyc = get_polymarket_buckets("NYC", "2026-04-26")
    probs_nyc = compute_bucket_probabilities(nyc_mean_c, nyc_std_c, buckets_nyc, city="NYC")
    # Mock market: slightly wrong around 65°F
    market_nyc = {b["label"]: 0.02 for b in buckets_nyc}
    market_nyc["65°F"] = 0.28
    market_nyc["66°F"] = 0.20
    signals_nyc = compute_edge(probs_nyc, market_nyc)
    print(f"NYC (°F): mean={c_to_f(nyc_mean_c):.1f}°F | top model bucket: "
          f"{max(probs_nyc, key=probs_nyc.get)} = {max(probs_nyc.values()):.1%}")
    for s in signals_nyc[:3]:
        sized = position_size(s["edge"], s["market_price"], 1000)
        print(f"  {s['outcome']} BUY {s['side']} | "
              f"model={s['model_probability']:.0%} market={s['market_price']:.0%} "
              f"edge=+{s['edge']*100:.0f}pts | ${sized:.2f}")

    print()

    # London — resolves in °C
    lon_mean_c = 14.0
    lon_std_c = 2.0
    buckets_lon = get_polymarket_buckets("London", "2026-04-26")
    probs_lon = compute_bucket_probabilities(lon_mean_c, lon_std_c, buckets_lon, city="London")
    market_lon = {b["label"]: 0.02 for b in buckets_lon}
    market_lon["14°C"] = 0.20
    market_lon["15°C"] = 0.15
    signals_lon = compute_edge(probs_lon, market_lon)
    print(f"London (°C): mean={lon_mean_c:.1f}°C | top model bucket: "
          f"{max(probs_lon, key=probs_lon.get)} = {max(probs_lon.values()):.1%}")
    for s in signals_lon[:3]:
        sized = position_size(s["edge"], s["market_price"], 1000)
        print(f"  {s['outcome']} BUY {s['side']} | "
              f"model={s['model_probability']:.0%} market={s['market_price']:.0%} "
              f"edge=+{s['edge']*100:.0f}pts | ${sized:.2f}")

    if signals_nyc and "°F" not in signals_nyc[0]["outcome"]:
        print("\nERROR: NYC signals should have °F labels — unit conversion is broken.")
    elif signals_lon and "°C" not in signals_lon[0]["outcome"]:
        print("\nERROR: London signals should have °C labels — unit conversion is broken.")
    else:
        print("\n✓ Unit handling looks correct.")


if __name__ == "__main__":
    test_with_mock_data()
