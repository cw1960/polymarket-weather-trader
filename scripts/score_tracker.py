"""Compute Brier scores from resolved Polymarket markets and check GO_LIVE_READY."""
import requests
from datetime import datetime, timezone, timedelta
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def fetch_resolved_markets(days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        r = requests.get(
            GAMMA_URL,
            params={"tag_id": 12, "closed": "true", "limit": 200},
            timeout=15,
        )
        r.raise_for_status()
        markets = r.json()
        return [m for m in markets if m.get("resolutionTime", "") >= cutoff and m.get("winner")]
    except Exception as e:
        print(f"ERROR fetching resolved markets: {e}")
        return []


def score_market(market: dict):
    winner = market.get("winner", "")
    market_id = market.get("id", "")

    res = (
        sb.table("trade_signals")
        .select("*")
        .eq("market_id", market_id)
        .is_("brier_score", None)
        .execute()
    )
    for sig in res.data or []:
        won = sig["outcome"] == winner
        model_p = sig["model_probability"] if won else 1 - sig["model_probability"]
        market_p = sig["market_price"] if won else 1 - sig["market_price"]
        our_brier = (model_p - 1) ** 2
        sb.table("trade_signals").update({
            "actual_outcome": won,
            "brier_score": round(our_brier, 4),
        }).eq("id", sig["id"]).execute()


def compute_stats() -> dict:
    # Explicit .limit(50_000) — Supabase silently caps replies at 1000
    # rows without it, which would freeze `total` and bias every per-city
    # mean once we accumulate >1000 scored predictions.  go_live_ready
    # depends on `total >= 200` so the cap is currently inert, but per-
    # city averages were already silently biased.
    res = (sb.table("trade_signals")
           .select("*")
           .not_.is_("brier_score", None)
           .limit(50_000)
           .execute())
    scored = res.data or []
    if not scored:
        return {}

    total = len(scored)
    our_avg = sum(r["brier_score"] for r in scored) / total
    market_avg = sum((r["market_price"] - 1) ** 2 for r in scored) / total

    by_city: dict[str, list] = {}
    for r in scored:
        by_city.setdefault(r["city"], []).append(r)

    city_scores = {
        city: sum(r["brier_score"] for r in rows) / len(rows)
        for city, rows in by_city.items()
    }

    traded = [r for r in scored if r.get("traded") and r.get("actual_outcome") is not None]
    wins = [
        r for r in traded
        if (r["side"] == "YES" and r["actual_outcome"]) or (r["side"] == "NO" and not r["actual_outcome"])
    ]
    win_rate = (len(wins) / len(traded) * 100) if traded else 0

    worst_city_score = max(city_scores.values()) if city_scores else 1.0
    go_live = (
        total >= 200
        and our_avg < 0.15
        and worst_city_score <= 0.22
        and win_rate > 65
    )

    return {
        "total": total,
        "our_avg": our_avg,
        "market_avg": market_avg,
        "city_scores": city_scores,
        "win_rate": win_rate,
        "go_live_ready": go_live,
        "failing": {
            "predictions": total < 200,
            "brier": our_avg >= 0.15,
            "city_brier": worst_city_score > 0.22,
            "win_rate": win_rate <= 65,
        },
    }


def print_report(stats: dict):
    print("\n=== BRIER SCORE REPORT ===")
    print(f"Period: last 7 days")
    print(f"Predictions scored: {stats['total']}")
    bs_ok = "✅" if stats["our_avg"] < 0.15 else "❌"
    print(f"\nOur model Brier score: {stats['our_avg']:.3f} {bs_ok} (target < 0.15)")
    print(f"Market price Brier score: {stats['market_avg']:.3f}")
    pct = (1 - stats["our_avg"] / stats["market_avg"]) * 100 if stats["market_avg"] > 0 else 0
    print(f"Our improvement over market: {pct:.0f}%")
    print("\nBy city:")
    for city, score in sorted(stats["city_scores"].items(), key=lambda x: x[1]):
        flag = "⚠️ above target" if score > 0.22 else ""
        print(f"  {city:10s}: {score:.3f} {flag}")
    print(f"\nPaper trade win rate: {stats['win_rate']:.1f}%")
    print(f"\nGO_LIVE_READY = {stats['go_live_ready']}")
    if not stats["go_live_ready"]:
        f = stats["failing"]
        print("Not ready because:")
        if f["predictions"]: print(f"  - Need {200 - stats['total']} more scored predictions")
        if f["brier"]:       print(f"  - Brier score {stats['our_avg']:.3f} must be < 0.15")
        if f["city_brier"]:  print(f"  - A city Brier score exceeds 0.22")
        if f["win_rate"]:    print(f"  - Win rate {stats['win_rate']:.1f}% must be > 65%")


def main():
    print("Fetching resolved markets...")
    markets = fetch_resolved_markets()
    print(f"Found {len(markets)} resolved markets to score.")
    for m in markets:
        score_market(m)
    stats = compute_stats()
    if stats:
        print_report(stats)
    else:
        print("No scored predictions yet. Run signal_engine.py for 2-4 weeks first.")
    return stats.get("go_live_ready", False)


if __name__ == "__main__":
    main()
