"""Pre-compute facts the AI cannot ignore when writing commentary.

These get injected into Claude's system prompt as concrete numbers — so it
can't recommend an action whose math contradicts our actual data.

Four classes of fact:
  1. Our validated edge zones (hit rate by price bucket, from bot's own trades)
  2. Overlap with this trader (cities we both trade, market overlap)
  3. Anti-precedents (prior analyzer runs with similar strategy labels)
  4. Kelly sizing reference table at the trader's typical entry prices
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .config import SUPABASE_KEY, SUPABASE_URL


_sb_client = None


def _sb():
    global _sb_client
    if _sb_client is None and SUPABASE_URL and SUPABASE_KEY:
        from supabase import create_client  # type: ignore
        _sb_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _sb_client


# ── Fact 1: our own validated edge zones ────────────────────────────────────

def our_validated_zones() -> dict[str, Any]:
    """For each fill_price bucket and city, what's OUR resolved win rate?

    Pulled from trade_signals where pnl_usd is populated, since live start.
    Tells the AI: where do *we* actually have validated edge vs. where are
    we still extrapolating.
    """
    sb = _sb()
    if not sb:
        return {"error": "supabase unavailable"}

    try:
        ls = sb.table("system_config").select("value").eq("key", "live_start_date").maybe_single().execute()
        live_start = (ls.data or {}).get("value")
    except Exception:
        live_start = None

    rows: list[dict] = []
    try:
        q = (sb.table("trade_signals")
             .select("city, outcome, signal_phase, fill_price, recommended_position, pnl_usd")
             .eq("signal_phase", "phase2")
             .not_.is_("pnl_usd", "null"))
        if live_start:
            q = q.gte("forecast_date", live_start).in_("order_status", ["filled", "sold"])
        rows = q.execute().data or []
    except Exception as e:
        return {"error": str(e)}

    if not rows:
        return {
            "live_resolved_count": 0,
            "note": "no resolved live trades yet — our edge is unvalidated at every price point",
        }

    # Bucket by price
    bucket_of = lambda p: (  # noqa: E731
        "<0.05" if p < 0.05 else
        "0.05-0.10" if p < 0.10 else
        "0.10-0.25" if p < 0.25 else
        "0.25-0.50" if p < 0.50 else
        "0.50-0.75" if p < 0.75 else
        ">=0.75"
    )
    by_bucket: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "cost": 0.0})
    for r in rows:
        p = r.get("fill_price")
        if p is None:
            continue
        b = bucket_of(float(p))
        s = by_bucket[b]
        s["n"] += 1
        if (r.get("pnl_usd") or 0) > 0:
            s["wins"] += 1
        s["pnl"] += r.get("pnl_usd") or 0
        s["cost"] += r.get("recommended_position") or 0

    cities = Counter(r["city"] for r in rows)

    bucket_summary: dict[str, Any] = {}
    for b, s in by_bucket.items():
        bucket_summary[b] = {
            "n_resolved": int(s["n"]),
            "win_rate": round(100 * s["wins"] / s["n"], 1) if s["n"] else None,
            "pnl_usd": round(s["pnl"], 2),
            "roi_pct": round(100 * s["pnl"] / s["cost"], 1) if s["cost"] > 0 else None,
        }

    return {
        "live_resolved_count": len(rows),
        "by_price_bucket": bucket_summary,
        "cities_traded_live": [c for c, _ in cities.most_common(20)],
        "min_resolved_for_confidence": 30,  # rough heuristic
    }


# ── Fact 2: overlap with this trader ────────────────────────────────────────

def overlap_with_trader(trader_profile: dict) -> dict[str, Any]:
    """Cities and markets we share with this trader."""
    sb = _sb()
    if not sb:
        return {}

    # Trader's weather cities (from weather_dissection)
    wd = trader_profile.get("weather_dissection") or {}
    trader_cities = [c.get("city", "").lower() for c in (wd.get("cities") or [])]
    trader_cities_set = set(c for c in trader_cities if c)
    if not trader_cities_set:
        return {"shared_cities": [], "shared_market_count": 0}

    # Our live-traded cities
    try:
        ls = sb.table("system_config").select("value").eq("key", "live_start_date").maybe_single().execute()
        live_start = (ls.data or {}).get("value")
    except Exception:
        live_start = None
    try:
        q = (sb.table("trade_signals")
             .select("city, condition_id, fill_price, recommended_position, pnl_usd")
             .eq("signal_phase", "phase2"))
        if live_start:
            q = q.gte("forecast_date", live_start).in_("order_status", ["filled", "sold"])
        ours = q.execute().data or []
    except Exception:
        ours = []

    our_cities = {(r.get("city") or "").lower() for r in ours if r.get("city")}
    our_cids = {(r.get("condition_id") or "").lower() for r in ours if r.get("condition_id")}

    # Trader's conditionIds from open_positions (only ones we have at hand)
    trader_cids = {(p.get("conditionId") or "").lower()
                   for p in (trader_profile.get("open_positions") or [])}

    shared_cities = sorted(trader_cities_set & our_cities)
    shared_cids = trader_cids & our_cids

    return {
        "shared_cities": shared_cities,
        "shared_market_count": len(shared_cids),
        "our_cities_count": len(our_cities),
        "trader_cities_count": len(trader_cities_set),
        "shared_cities_note": (
            f"We've live-traded {len(shared_cities)} of the trader's "
            f"{len(trader_cities_set)} active cities"
        ),
    }


# ── Fact 3: anti-precedents — prior traders we've seen with similar profile ──

def anti_precedents(trader_profile: dict) -> dict[str, Any]:
    """Look back at prior analyzer_runs with similar strategy label and
    summarize whether that style has historically panned out."""
    sb = _sb()
    if not sb:
        return {}

    label = (trader_profile.get("strategy") or {}).get("label", "")
    if not label:
        return {}

    try:
        rows = (sb.table("analyzer_runs")
                .select("wallet, stats_json")
                .limit(200)
                .execute().data or [])
    except Exception:
        return {}

    # Filter to same label, excluding the current wallet
    this_wallet = (trader_profile.get("identity") or {}).get("address", "").lower()
    matches = []
    for r in rows:
        sj = r.get("stats_json") or {}
        if (sj.get("strategy") or {}).get("label") != label:
            continue
        if r.get("wallet", "").lower() == this_wallet:
            continue
        matches.append(sj)

    if not matches:
        return {"strategy_label": label, "n_priors": 0}

    # Aggregate stats across similar traders
    total_trades = sum((m.get("stats") or {}).get("total_trades", 0) for m in matches)
    total_volume = sum((m.get("stats") or {}).get("total_volume_usd", 0) for m in matches)
    net_cashflow = sum((m.get("stats") or {}).get("net_cashflow_usd", 0) for m in matches)

    # Aggregate weather price-bucket P&L across priors (where we have it)
    bucket_agg: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "cost": 0.0})
    for m in matches:
        for b in ((m.get("weather_dissection") or {}).get("price_bucket_pnl") or []):
            bk = b.get("bucket", "?")
            s = bucket_agg[bk]
            s["n"]    += b.get("n_resolved", 0)
            s["wins"] += b.get("wins", 0)
            s["pnl"]  += b.get("pnl_usd", 0)
            s["cost"] += b.get("cost_usd", 0)

    bucket_summary: dict[str, Any] = {}
    for b, s in bucket_agg.items():
        bucket_summary[b] = {
            "n_resolved": int(s["n"]),
            "win_rate_pct": round(100 * s["wins"] / s["n"], 1) if s["n"] else None,
            "pnl_usd": round(s["pnl"], 2),
            "roi_pct": round(100 * s["pnl"] / s["cost"], 1) if s["cost"] > 0 else None,
        }

    return {
        "strategy_label": label,
        "n_priors": len(matches),
        "aggregate_total_trades": total_trades,
        "aggregate_net_cashflow_usd": round(net_cashflow, 2),
        "aggregate_price_bucket_pnl": bucket_summary,
    }


# ── Fact 4: Kelly sizing reference at this trader's typical entry prices ────

def kelly_sizing_table(trader_profile: dict, bot_config: dict, bankroll_usd: float) -> dict[str, Any]:
    """For a few candidate (market_price, our_model_p) combinations, compute
    the actual Kelly sizing under our config. Makes 'just put X on it' calls
    impossible to fake."""
    KELLY_FRACTION = float(bot_config.get("KELLY_FRACTION", 0.15))
    MAX_POSITION_USD = float(bot_config.get("MAX_POSITION_USD", 100))
    MAX_PCT_BANKROLL = float(bot_config.get("MAX_PCT_BANKROLL", 0.05))
    MIN_EDGE = float(bot_config.get("MIN_EDGE", 0.08))

    def kelly_size(market_p: float, model_p: float) -> dict[str, Any]:
        """Standard Kelly for a 0-1 payoff binary: f* = (p - q*b)/b
        where b = (1-market_p)/market_p (odds received), q = 1-p.
        Result is fraction of bankroll. Cap by our KELLY_FRACTION."""
        if market_p <= 0 or market_p >= 1:
            return {"note": "degenerate price"}
        b = (1 - market_p) / market_p  # net odds
        f_full = (model_p * (1 + b) - 1) / b
        f_capped = max(0, min(f_full * KELLY_FRACTION, MAX_PCT_BANKROLL))
        usd = min(f_capped * bankroll_usd, MAX_POSITION_USD)
        return {
            "market_price": market_p,
            "model_probability": model_p,
            "edge": round(model_p - market_p, 4),
            "passes_min_edge": (model_p - market_p) >= MIN_EDGE,
            "full_kelly_pct": round(100 * f_full, 2) if f_full > 0 else 0,
            "fractional_kelly_pct": round(100 * f_capped, 3),
            "max_position_usd": round(usd, 2),
        }

    # Typical interesting cases
    return {
        "params": {
            "KELLY_FRACTION": KELLY_FRACTION,
            "MAX_POSITION_USD": MAX_POSITION_USD,
            "MAX_PCT_BANKROLL": MAX_PCT_BANKROLL,
            "MIN_EDGE": MIN_EDGE,
            "bankroll_usd": bankroll_usd,
        },
        "sizing_at_various_prices": [
            kelly_size(0.01, 0.09),  # deep tail with 8% edge
            kelly_size(0.05, 0.13),  # near-tail with 8% edge
            kelly_size(0.20, 0.30),  # modal-zone with 10% edge
            kelly_size(0.50, 0.60),  # modal-zone with 10% edge
            kelly_size(0.95, 0.85),  # short the certainty (opposite side, 10% edge)
        ],
        "note": (
            "Any proposed action must cite which row of this table applies. "
            "If the proposed sizing exceeds max_position_usd, the action is invalid."
        ),
    }


# ── Fact 5: high-conviction open positions worth monitoring ─────────────────

# Cities we are likely to trade based on station-delta coverage. Used to
# filter the trader's open positions down to ones with operational
# relevance for us.
_DEFAULT_OUR_CITY_SET = {
    # Europe
    "london", "paris", "madrid", "munich", "amsterdam", "milan", "berlin",
    "warsaw", "rome", "vienna", "lisbon", "dublin", "stockholm", "oslo",
    "copenhagen", "helsinki", "istanbul", "ankara",
    # North America
    "nyc", "new york city", "chicago", "atlanta", "dallas", "houston",
    "miami", "seattle", "austin", "los angeles", "boston", "denver",
    "phoenix", "philadelphia", "san francisco", "toronto", "montreal",
    "vancouver", "mexico city", "monterrey",
    # Asia
    "tokyo", "hong kong", "seoul", "shanghai", "beijing", "taipei",
    "wuhan", "guangzhou", "shenzhen", "chongqing", "chengdu",
    "singapore", "kuala lumpur", "bangkok", "jakarta", "manila",
    "mumbai", "delhi", "ho chi minh city",
    # South America / Other
    "buenos aires", "rio de janeiro", "sao paulo", "santiago", "lima",
    "panama city", "lagos", "cape town", "cairo", "dubai",
    "sydney", "melbourne", "auckland", "wellington",
}


def _our_traded_cities(sb) -> set[str]:
    """Live-traded cities from our trade_signals. Falls back to the default set."""
    if not sb:
        return _DEFAULT_OUR_CITY_SET
    try:
        ls = sb.table("system_config").select("value").eq("key", "live_start_date").maybe_single().execute()
        live_start = (ls.data or {}).get("value")
    except Exception:
        live_start = None
    try:
        q = sb.table("trade_signals").select("city").eq("signal_phase", "phase2")
        if live_start:
            q = q.gte("forecast_date", live_start)
        rows = q.execute().data or []
    except Exception:
        rows = []
    live_set = {(r.get("city") or "").lower() for r in rows if r.get("city")}
    # Union with default since a brand-new live bot would otherwise have an
    # empty set and miss every monitor opportunity.
    return live_set | _DEFAULT_OUR_CITY_SET


_MONTH_TO_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extract_city_from_slug(slug: str) -> str:
    """Parse city name from a Polymarket weather-market slug.

    Slug format: 'highest-temperature-in-<city-name>-on-<date>-<bucket>'.
    Multi-word cities ('hong-kong', 'new-york-city', 'los-angeles', 'panama-
    city', 'kuala-lumpur') need everything between 'in' and 'on' joined
    with spaces — not just the single token after 'in'.
    """
    if not slug:
        return ""
    parts = slug.lower().split("-")
    try:
        i_in = parts.index("in")
        i_on = parts.index("on", i_in + 1)
    except ValueError:
        return ""
    if i_on <= i_in + 1:
        return ""
    return " ".join(parts[i_in + 1:i_on])


def _extract_market_date_from_slug(slug: str):
    """Parse YYYY-MM-DD from a Polymarket weather slug, or None if unparseable.

    Slug examples:
      highest-temperature-in-london-on-may-15-2026-16c
      highest-temperature-in-new-york-city-on-april-26-2026-54-55f
    """
    if not slug:
        return None
    parts = slug.lower().split("-")
    try:
        i_on = parts.index("on")
    except ValueError:
        return None
    # Look for <month-name> <day> <year> in the next few tokens
    for i in range(i_on + 1, min(i_on + 6, len(parts) - 2)):
        m = parts[i][:3]
        if m in _MONTH_TO_NUM:
            try:
                day = int(parts[i + 1])
                year = int(parts[i + 2])
                from datetime import date
                return date(year, _MONTH_TO_NUM[m], day)
            except (ValueError, IndexError):
                return None
    return None


# Conviction-zone thresholds. We widen from the original (0.85/0.05) because
# meaningful information starts at ~0.75: a wallet sized at $70 sitting on a
# 0.75-priced NO is still signalling clear conviction, and the 1¢-3¢ fade
# opportunity on the other side is real if our model disagrees.
_HIGH_CONVICTION = 0.75
_LOW_CONVICTION = 0.10


def monitor_candidates(trader_profile: dict, max_n: int = 8) -> list[dict[str, Any]]:
    """Trader's open positions worth watching, ranked by cost basis.

    A position qualifies if ALL:
      - market resolution date is today or in the future (skip stranded
        post-resolution unredeemed positions — they're settled, not open
        to take the other side of), AND
      - position size ≥$25 (filter rounding-dust positions), AND
      - entry price in a conviction zone (≥0.75 or ≤0.10), AND
      - market's city is one we trade or are likely to trade.

    Returns at most `max_n` positions, sorted by absolute cost basis.
    """
    open_positions = trader_profile.get("open_positions") or []
    if not open_positions:
        return []

    from datetime import date
    today = date.today()

    sb = _sb()
    our_cities = _our_traded_cities(sb)

    out: list[dict[str, Any]] = []
    for p in open_positions:
        price = p.get("avg_entry_price")
        cost = abs(p.get("cost_basis_usd") or 0)
        if price is None or cost < 25:
            continue
        if not (price >= _HIGH_CONVICTION or price <= _LOW_CONVICTION):
            continue
        slug = p.get("slug", "")
        # Filter out markets that have already resolved. Positions with a
        # past resolution date are stranded post-settlement and not
        # tradable — including them in the monitor panel is misleading.
        mkt_date = _extract_market_date_from_slug(slug)
        if mkt_date is not None and mkt_date < today:
            continue
        city = _extract_city_from_slug(slug)
        if city not in our_cities:
            continue
        out.append({
            "market_title":          p.get("title", ""),
            "condition_id":          p.get("conditionId", ""),
            "trader_side":           p.get("outcome", ""),
            "trader_entry_price":    round(float(price), 4),
            "trader_cost_usd":       round(cost, 2),
            "city":                  city,
            "market_date":           mkt_date.isoformat() if mkt_date else None,
            "entered_at":            p.get("entered_at"),
        })
    out.sort(key=lambda r: r["trader_cost_usd"], reverse=True)
    return out[:max_n]


# ── Public API ──────────────────────────────────────────────────────────────

def compute_all_facts(trader_profile: dict, bot_config: dict, bankroll_usd: float) -> dict[str, Any]:
    """One call to assemble every pre-computed fact for the system prompt."""
    return {
        "our_validated_zones":   our_validated_zones(),
        "overlap_with_trader":   overlap_with_trader(trader_profile),
        "anti_precedents":       anti_precedents(trader_profile),
        "kelly_sizing":          kelly_sizing_table(trader_profile, bot_config, bankroll_usd),
        "monitor_candidates":    monitor_candidates(trader_profile),
    }
