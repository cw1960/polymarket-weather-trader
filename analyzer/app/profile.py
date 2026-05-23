"""Trader profile: stats + by-day breakdown + strategy classification + open positions.

Uses the vendored polymarket-toolkit cashflow PnL engine for audit-grade numbers,
plus our own aggregations layered on top of the raw activity stream.
"""
from __future__ import annotations

import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import VENDOR_PNL

# Make the vendored toolkit importable
sys.path.insert(0, str(VENDOR_PNL))
from compute_precise_pnl import (  # type: ignore  # noqa: E402
    compute_address_pnl,
    fetch_activity_all_timestamp,
    fetch_positions,
)
from lib.pm_http import create_pm_http_client  # type: ignore  # noqa: E402


GAMMA = "https://gamma-api.polymarket.com/markets"
LB_API = "https://lb-api.polymarket.com/profit"


def resolve_username(client: httpx.Client, username: str) -> str | None:
    """Resolve a username to a 0x address via leaderboard paging."""
    for offset in range(0, 5000, 500):
        r = client.get(LB_API, params={"window": "all", "limit": 500, "offset": offset}, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        for row in data:
            if (row.get("name") or "").lower() == username.lower():
                return row.get("proxyWallet")
            if (row.get("pseudonym") or "").lower() == username.lower():
                return row.get("proxyWallet")
        time.sleep(0.2)
    return None


def _bucket_price(p: float) -> str:
    if p < 0.02: return "<0.02"
    if p < 0.05: return "0.02-0.05"
    if p < 0.10: return "0.05-0.10"
    if p < 0.25: return "0.10-0.25"
    if p < 0.50: return "0.25-0.50"
    if p < 0.75: return "0.50-0.75"
    return ">=0.75"


def _classify_strategy(stats: dict) -> dict:
    """Heuristic strategy label. Returns {label, confidence, reasons}."""
    reasons = []
    label = "Diversified"
    n = stats["total_trades"]
    if n == 0:
        return {"label": "Inactive", "confidence": 1.0, "reasons": ["no trades"]}

    weather_share = stats["weather_share"]
    median_buy = stats["median_buy_price"]
    avg_size = stats["avg_buy_size_usd"]
    roundtrip_rate = stats["roundtrip_rate"]

    # Market maker: very high trade count, small avg size, near-balanced buy/sell
    if n > 1000 and avg_size < 5.0 and roundtrip_rate > 0.6:
        label = "Market Maker"
        reasons.append(f"high trade count ({n}) with small avg size (${avg_size:.2f})")
        reasons.append(f"high round-trip rate ({roundtrip_rate:.0%})")
    # Whale: large average size
    elif avg_size > 500:
        label = "Whale"
        reasons.append(f"large avg buy size (${avg_size:,.0f})")
    # Tail scalper: median buy price < 0.05
    elif median_buy < 0.05:
        label = "Tail Scalper"
        reasons.append(f"median buy price ${median_buy:.3f} — buying deep OOTM tails")
        reasons.append(f"hold-to-resolution rate {1-roundtrip_rate:.0%}")
    # Specialist: > 80% concentration in one category
    elif weather_share > 0.8:
        label = "Weather Specialist"
        reasons.append(f"{weather_share:.0%} of trades in weather markets")
    elif median_buy >= 0.10:
        label = "Conviction Trader"
        reasons.append(f"median buy ${median_buy:.2f} — takes positions where market is uncertain")

    return {"label": label, "confidence": 0.7, "reasons": reasons}


def build_profile(
    address: str,
    *,
    deep: bool = False,  # default OFF — audit-grade PnL costs 60-120s extra
    progress=None,       # optional ProgressReporter from jobs.py
) -> tuple[dict[str, Any], list[dict]]:
    """Full profile computation. `deep=True` does PnL via toolkit cashflow method.

    Returns (profile_dict, raw_activity). The raw activity is returned so callers
    (like weather_dissect) can avoid re-fetching ~30K records.
    """
    address = address.lower()
    started = time.time()

    def _p(pct: float, stage: str, detail: str = "") -> None:
        if progress is not None:
            progress.update(pct=pct, stage=stage, detail=detail)

    _p(2, "fetching trades", "paginating activity from Polymarket data-api…")

    # Track cumulative row count so the user sees motion during pagination,
    # which on whale wallets is the longest single stage (~60-180s).
    _row_counter = {"total": 0}

    def _activity_progress(info: dict) -> None:
        _row_counter["total"] += int(info.get("rows", 0) or 0)
        # Scale 2% → 38% based on rows. At ~100K rows we're near the top of
        # the range; bigger wallets just sit near 38% until pagination ends.
        pct = min(38, 2 + _row_counter["total"] / 3000)
        _p(pct, "fetching trades",
           f"{_row_counter['total']:,} trades pulled so far (page {info.get('page','?')})…")

    with create_pm_http_client(timeout=30.0) as client:
        # Activity fetch — the bulk of fetch time on big wallets
        activity, activity_truncated = fetch_activity_all_timestamp(
            client, address, "TRADE", progress_cb=_activity_progress,
        )
        _p(40, "fetching positions", f"{len(activity):,} trades pulled; fetching positions…")
        positions, _ = fetch_positions(client, address)
        precise = None
        if deep:
            _p(50, "computing precise P&L", "running cashflow reconstruction (slow on whale wallets)…")
            # The toolkit's compute_address_pnl doesn't expose progress hooks,
            # so we run a side-thread heartbeat that ticks elapsed seconds into
            # the reporter so the UI doesn't look stuck.
            import threading as _th
            _stop = _th.Event()

            def _heartbeat() -> None:
                started_at = time.time()
                while not _stop.is_set():
                    elapsed = int(time.time() - started_at)
                    _p(
                        50 + min(8, elapsed / 20),  # creeps from 50 → 58 over ~160s
                        "computing precise P&L",
                        f"running cashflow reconstruction… {elapsed}s elapsed",
                    )
                    _stop.wait(3)

            hb = _th.Thread(target=_heartbeat, daemon=True)
            hb.start()
            try:
                precise = compute_address_pnl(client, address)
            except Exception as e:
                precise = {"error": str(e)}
            finally:
                _stop.set()

    trades = [a for a in activity if a.get("type") == "TRADE"]
    weather = [a for a in trades if "temperature" in (a.get("slug") or "").lower()
               or "temperature" in (a.get("eventSlug") or "").lower()]

    # Identity
    sample = trades[0] if trades else {}
    identity = {
        "address": address,
        "username": sample.get("name") or "",
        "pseudonym": sample.get("pseudonym") or "",
        "bio": sample.get("bio") or "",
    }

    # Aggregate stats
    buys = [t for t in trades if t.get("side") == "BUY"]
    sells = [t for t in trades if t.get("side") == "SELL"]
    buy_volume = sum(t["usdcSize"] for t in buys)
    sell_volume = sum(t["usdcSize"] for t in sells)

    median_buy_price = 0.0
    if buys:
        prices = sorted(t["price"] for t in buys)
        median_buy_price = prices[len(prices) // 2]

    bucket_counts = Counter(_bucket_price(t["price"]) for t in buys)

    # Round-trip rate: per (conditionId, asset), is net size ~= 0?
    per_position = defaultdict(lambda: {"net": 0.0, "buy_cost": 0.0, "sell_proceeds": 0.0,
                                         "first_ts": None, "last_ts": None,
                                         "title": "", "slug": "", "outcome": ""})
    for t in trades:
        key = (t["conditionId"], t["asset"])
        sign = 1 if t["side"] == "BUY" else -1
        p = per_position[key]
        p["net"] += sign * t["size"]
        if t["side"] == "BUY":
            p["buy_cost"] += t["usdcSize"]
        else:
            p["sell_proceeds"] += t["usdcSize"]
        if p["first_ts"] is None or t["timestamp"] < p["first_ts"]:
            p["first_ts"] = t["timestamp"]
        if p["last_ts"] is None or t["timestamp"] > p["last_ts"]:
            p["last_ts"] = t["timestamp"]
        p["title"] = t.get("title", "")
        p["slug"] = t.get("slug", "")
        p["outcome"] = t.get("outcome", "")

    from datetime import date as _date_for_stats
    _today_stats = _date_for_stats.today()
    from .comparison import _extract_market_date_from_slug as _date_parse
    closed_positions = sum(1 for p in per_position.values() if abs(p["net"]) < 0.01)

    # Split "open" into truly-open (future markets) vs. unredeemed (past markets
    # where the trader is just sitting on stranded tokens). The latter aren't
    # really open in any actionable sense.
    truly_open = 0
    unredeemed = 0
    for p in per_position.values():
        if abs(p["net"]) < 0.01:
            continue
        d = _date_parse(p.get("slug", ""))
        if d is not None and d < _today_stats:
            unredeemed += 1
        else:
            truly_open += 1
    open_positions = truly_open + unredeemed  # preserved for backward compat
    roundtrip_rate = (closed_positions / len(per_position)) if per_position else 0.0

    # Hold times for closed positions
    hold_seconds = []
    for p in per_position.values():
        if abs(p["net"]) < 0.01 and p["first_ts"] and p["last_ts"]:
            hold_seconds.append(p["last_ts"] - p["first_ts"])
    hold_seconds.sort()
    avg_hold_h = (sum(hold_seconds) / len(hold_seconds) / 3600) if hold_seconds else 0.0
    median_hold_h = (hold_seconds[len(hold_seconds) // 2] / 3600) if hold_seconds else 0.0

    # Category breakdown (rough)
    category_counts: Counter = Counter()
    for t in trades:
        slug = (t.get("eventSlug") or "").lower()
        if "temperature" in slug or "weather" in slug:
            category_counts["weather"] += 1
        elif any(s in slug for s in ["nfl", "nba", "mlb", "nhl", "soccer", "tennis", "ufc"]):
            category_counts["sports"] += 1
        elif any(s in slug for s in ["bitcoin", "ethereum", "crypto", "btc", "eth"]):
            category_counts["crypto"] += 1
        elif any(s in slug for s in ["election", "president", "senate", "congress", "trump", "biden"]):
            category_counts["politics"] += 1
        else:
            category_counts["other"] += 1

    stats = {
        "total_trades": len(trades),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "unique_markets": len(per_position),
        "closed_positions": closed_positions,
        "open_positions": open_positions,
        "truly_open_positions": truly_open,
        "unredeemed_positions": unredeemed,
        "roundtrip_rate": roundtrip_rate,
        "total_volume_usd": buy_volume + sell_volume,
        "buy_volume_usd": buy_volume,
        "sell_volume_usd": sell_volume,
        "net_cashflow_usd": sell_volume - buy_volume,
        "avg_buy_size_usd": (buy_volume / len(buys)) if buys else 0.0,
        "median_buy_price": median_buy_price,
        "buy_price_buckets": dict(bucket_counts),
        "avg_hold_hours": avg_hold_h,
        "median_hold_hours": median_hold_h,
        "weather_share": (category_counts["weather"] / len(trades)) if trades else 0.0,
        "category_counts": dict(category_counts),
    }

    # By-day rollup (closed-position date == max-ts of sells, else last buy ts)
    by_day = _by_day_rollup(per_position)

    # Open positions snapshot (top N by cost)
    from datetime import date as _date
    _today = _date.today()
    open_pos_list = []
    for (cid, asset), p in per_position.items():
        if abs(p["net"]) > 0.01:
            slug = p["slug"]
            # Reuse the date parser from comparison.py — keeps logic in sync
            from .comparison import _extract_market_date_from_slug
            mkt_date = _extract_market_date_from_slug(slug)
            is_unredeemed = mkt_date is not None and mkt_date < _today
            open_pos_list.append({
                "conditionId": cid,
                "title": p["title"],
                "slug": slug,
                "outcome": p["outcome"],
                "size": p["net"],
                "cost_basis_usd": p["buy_cost"] - p["sell_proceeds"],
                "avg_entry_price": ((p["buy_cost"] - p["sell_proceeds"]) / p["net"]) if p["net"] else 0,
                "entered_at": p["first_ts"],
                "market_date": mkt_date.isoformat() if mkt_date else None,
                # True = market already resolved; trader just hasn't redeemed.
                # These aren't really "open" — show them differently in UI.
                "unredeemed_post_resolution": is_unredeemed,
            })
    open_pos_list.sort(key=lambda x: x["cost_basis_usd"], reverse=True)

    strategy = _classify_strategy(stats)

    elapsed_ms = int((time.time() - started) * 1000)
    profile = {
        "identity": identity,
        "stats": stats,
        "strategy": strategy,
        "by_day": by_day,
        # Trajectory: is the trader improving, declining, or steady?
        # Compares recent activity against the lifetime average so the
        # commentary can answer "is this trader on the way up or down?"
        "trajectory": _compute_trajectory(by_day),
        "open_positions": open_pos_list[:50],
        "precise_pnl": asdict(precise) if precise and not isinstance(precise, dict) else precise,
        "meta": {
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "fetch_ms": elapsed_ms,
            "trade_count": len(trades),
            "weather_count": len(weather),
            "raw_activity_count": len(activity),
            "activity_truncated": activity_truncated,
        },
    }
    return profile, activity


def _compute_trajectory(by_day: list[dict]) -> dict:
    """Recent-vs-lifetime comparison to detect whether the trader is on
    a different trajectory than their long-run baseline.  Useful for
    spotting traders who recently turned profitable (worth watching) or
    who recently turned negative (don't get fooled by stale cumulative
    P&L).  Inputs:  by_day is the rollup list, oldest-first.
    """
    if not by_day:
        return {"recent_days": 0}

    # Normalize day strings to dates for windowing.
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    cutoff_30 = today - _td(days=30)
    cutoff_7  = today - _td(days=7)

    def _to_date(s: str | None):
        if not s: return None
        try: return _date.fromisoformat(s[:10])
        except Exception: return None

    rows = [dict(r, _d=_to_date(r.get("date"))) for r in by_day]
    rows = [r for r in rows if r["_d"] is not None]
    if not rows:
        return {"recent_days": 0}

    last7  = [r for r in rows if r["_d"] >= cutoff_7]
    last30 = [r for r in rows if r["_d"] >= cutoff_30]

    def _agg(rs):
        n_days  = len(rs)
        pnl     = sum(float(r.get("pnl", 0) or 0) for r in rs)
        spent   = sum(float(r.get("spent", 0) or 0) for r in rs)
        wins    = sum(int(r.get("wins", 0) or 0) for r in rs)
        losses  = sum(int(r.get("losses", 0) or 0) for r in rs)
        n       = wins + losses
        return {
            "trading_days": n_days,
            "pnl_usd":      round(pnl, 2),
            "spent_usd":    round(spent, 2),
            "wins":         wins,
            "losses":       losses,
            "win_rate_pct": round(100 * wins / n, 1) if n else 0.0,
            "roi_pct":      round(100 * pnl / spent, 2) if spent > 0 else None,
            "avg_daily_pnl": round(pnl / n_days, 2) if n_days else 0.0,
        }

    lifetime = _agg(rows)
    r7       = _agg(last7)
    r30      = _agg(last30)

    # Trajectory verdict — qualitative direction the trader is heading
    # relative to their lifetime baseline.
    verdict = "steady"
    if lifetime["avg_daily_pnl"] is not None and r30["trading_days"] >= 5:
        delta = r30["avg_daily_pnl"] - lifetime["avg_daily_pnl"]
        rel_delta = (delta / abs(lifetime["avg_daily_pnl"])) if lifetime["avg_daily_pnl"] else 0
        # Use 25% relative move OR $50 absolute move as the threshold,
        # whichever is more meaningful for this trader's scale.
        if delta > max(50, 0.25 * abs(lifetime["avg_daily_pnl"])):
            verdict = "improving"
        elif delta < -max(50, 0.25 * abs(lifetime["avg_daily_pnl"])):
            verdict = "declining"

    return {
        "last_7_days":  r7,
        "last_30_days": r30,
        "lifetime":     lifetime,
        "verdict":      verdict,
    }


def _by_day_rollup(per_position: dict) -> list[dict]:
    """Produce by-day stats matching the screenshot format.

    Each position contributes to exactly two day-buckets:
      - `buys` is incremented on the position's first_ts day (always)
      - if closed: `closed`/wins/losses/spent/pnl/hold are added on the
        last_ts day (the close date). If still open: `open` incremented
        on the first_ts day.

    Crucially, no row's closed/pnl fields are copied across days. Earlier
    versions of this function inadvertently propagated close-day P&L into
    the buy-day row when those days differed, producing duplicate-looking
    rows.
    """
    days: dict[str, dict] = {}

    def day_of(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    def empty(date: str) -> dict:
        return {
            "date": date, "buys": 0, "closed": 0, "open": 0,
            "wins": 0, "losses": 0,
            "spent": 0.0, "pnl": 0.0, "hold_h": [],
        }

    for p in per_position.values():
        if not p["first_ts"]:
            continue
        is_closed = abs(p["net"]) < 0.01
        buy_day = day_of(p["first_ts"])

        # Always increment buys on the day the position was first entered.
        days.setdefault(buy_day, empty(buy_day))["buys"] += 1

        if is_closed:
            close_day = day_of(p["last_ts"])
            d = days.setdefault(close_day, empty(close_day))
            d["closed"] += 1
            pnl = p["sell_proceeds"] - p["buy_cost"]
            d["spent"] += p["buy_cost"]
            d["pnl"] += pnl
            if pnl > 0:
                d["wins"] += 1
            else:
                d["losses"] += 1
            d["hold_h"].append((p["last_ts"] - p["first_ts"]) / 3600)
        else:
            days[buy_day]["open"] += 1

    out = []
    for date, d in days.items():
        hold = d["hold_h"]
        avg_hold = (sum(hold) / len(hold)) if hold else 0.0
        roi = (100 * d["pnl"] / d["spent"]) if d["spent"] > 0 else 0.0
        out.append({
            "date": date,
            "buys": d["buys"],
            "closed": d["closed"],
            "open": d["open"],
            "wins": d["wins"],
            "losses": d["losses"],
            "spent": round(d["spent"], 2),
            "pnl": round(d["pnl"], 2),
            "roi_pct": round(roi, 1),
            "avg_hold_hours": round(avg_hold, 1),
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out
