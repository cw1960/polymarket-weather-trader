"""Weather-market specific analysis: entry timing vs GFS runs, price-bucket P&L,
city specialization, hold-time distribution, market-resolution lookups.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict

import httpx

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB  = "https://clob.polymarket.com"
GFS_RUNS_UTC = [0, 6, 12, 18]


def fetch_open_position_bids(
    client: httpx.Client,
    open_positions: list[tuple[str, float, float]],
    progress=None,
    progress_start: float = 95.0,
    progress_end: float = 99.0,
) -> dict[str, float | None]:
    """Fetch current best bid for the trader's side on each open position.

    Input:  list of (token_id, _, _) — the asset the trader holds.
    Output: {token_id: best_bid_or_None}

    Best-bid is the price at which the trader could sell RIGHT NOW.  Used
    to mark open positions to market so the bucket-P&L numbers reflect
    today's reality, not "well, we don't know yet."

    Failed lookups return None (don't poison the bucket) — caller falls
    back to "unknown" treatment.  Parallelised over httpx for speed.
    """
    import concurrent.futures as cf
    if not open_positions:
        return {}
    token_ids = list({tid for tid, _, _ in open_positions if tid})
    if not token_ids:
        return {}

    bids: dict[str, float | None] = {}

    def _fetch_one(tid: str) -> tuple[str, float | None]:
        try:
            r = client.get(f"{CLOB}/book", params={"token_id": tid}, timeout=10)
            r.raise_for_status()
            book = r.json() or {}
            # Polymarket returns bids sorted desc — first one is the best bid.
            # Fall back to "asks - tick" if bid side is dry.
            bids_list = book.get("bids") or []
            if bids_list:
                return tid, float(bids_list[0].get("price", 0))
            asks = book.get("asks") or []
            if asks:
                # If bid side is empty, the trader can't actually sell.
                # Conservative: estimate liquidation value as 80% of best ask.
                # (You'd cross the spread and pay fees.)
                return tid, max(0.0, float(asks[0].get("price", 0)) * 0.80)
            return tid, None
        except Exception:
            return tid, None

    # Polymarket tolerates ~20-30 concurrent requests well; cap at 10 to be
    # neighbourly. 274 open positions @ 10 parallel = ~30 batches = ~10s.
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for i, (tid, bid) in enumerate(ex.map(_fetch_one, token_ids)):
            bids[tid] = bid
            if progress is not None and i % 25 == 0 and len(token_ids) > 50:
                pct = progress_start + (progress_end - progress_start) * (i / len(token_ids))
                progress.update(
                    stage="marking open positions to market",
                    detail=f"{i}/{len(token_ids)}",
                    pct=pct,
                )
    return bids


def _is_weather(t: dict) -> bool:
    s = (t.get("slug") or "").lower() + " " + (t.get("eventSlug") or "").lower()
    return "temperature" in s or "weather" in s


def _city_from_slug(slug: str) -> str:
    """Parse city from weather-market slug, handling multi-word cities."""
    if not slug:
        return "unknown"
    parts = slug.lower().split("-")
    try:
        i_in = parts.index("in")
        i_on = parts.index("on", i_in + 1)
    except ValueError:
        return "unknown"
    if i_on <= i_in + 1:
        return "unknown"
    return " ".join(parts[i_in + 1:i_on])


def _gfs_phase_hours(ts: int) -> float:
    """Hours since the most recent GFS run (00z/06z/12z/18z) at trade time."""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    hour = dt.hour + dt.minute / 60
    last_run = max(r for r in GFS_RUNS_UTC if r <= hour) if any(r <= hour for r in GFS_RUNS_UTC) else -6 + max(GFS_RUNS_UTC)
    return hour - last_run


def fetch_resolutions(
    client: httpx.Client,
    condition_ids: list[str],
    progress=None,
    progress_start: float = 60.0,
    progress_end: float = 95.0,
) -> dict:
    """Fetch market metadata (open + closed) for the given conditionIds.

    Three-stage optimisation:
      1. Pull already-known markets from analyzer_market_cache (Supabase) —
         on re-analyses of large wallets, the bulk of conditionIds are
         resolved-and-cached and this stage takes ~100ms total.
      2. Fetch only the remaining unknowns from gamma-api in parallel.
      3. Persist newly-fetched resolved markets back to the cache so the
         NEXT analyzer call doesn't pay for them either.

    Emits progress updates to `progress` if provided.
    """
    out: dict = {}
    cids = list(condition_ids)
    if not cids:
        return out

    # Stage 1: cache lookup
    try:
        from .cache import _client as _sb_client
        sb = _sb_client()
    except Exception:
        sb = None
    cached_ids: set[str] = set()
    if sb is not None:
        try:
            # Supabase has a URL-length cap; batch in groups of 200
            for i in range(0, len(cids), 200):
                slice_ = cids[i:i + 200]
                r = (sb.table("analyzer_market_cache")
                     .select("condition_id, slug, closed, outcome_prices, end_date")
                     .in_("condition_id", slice_)
                     .execute())
                for row in (r.data or []):
                    cid = row["condition_id"]
                    cached_ids.add(cid)
                    # Rebuild a minimal market dict matching gamma-api shape
                    out[cid] = {
                        "conditionId":   cid,
                        "slug":          row.get("slug") or "",
                        "closed":        bool(row.get("closed")),
                        "outcomePrices": row.get("outcome_prices") or "[]",
                        "endDate":       row.get("end_date"),
                    }
        except Exception:
            # Cache table may not exist yet — silently fall through to full fetch
            cached_ids = set()

    needed = [c for c in cids if c not in cached_ids]
    if progress is not None and cached_ids:
        progress.update(
            stage="fetching market resolutions",
            detail=f"{len(cached_ids):,} cached, {len(needed):,} remaining…",
        )

    if not needed:
        if progress is not None:
            progress.update(pct=progress_end)
        return out

    # Stage 2: parallel gamma-api fetch for cache misses
    total_batches = 2 * ((len(needed) + 19) // 20)
    done_count = 0
    fresh_markets: list[dict] = []

    import concurrent.futures as cf

    def _fetch_batch(args: tuple[str, list[str]]) -> list[dict]:
        closed_flag, batch = args
        params: list[tuple[str, str]] = [("closed", closed_flag), ("limit", "100")]
        params += [("condition_ids", c) for c in batch]
        try:
            r = client.get(GAMMA, params=params, timeout=30)
            r.raise_for_status()
            return r.json() or []
        except Exception:
            return []

    tasks: list[tuple[str, list[str]]] = []
    for closed_flag in ("true", "false"):
        for i in range(0, len(needed), 20):
            tasks.append((closed_flag, needed[i:i + 20]))

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for markets in pool.map(_fetch_batch, tasks):
            for m in markets:
                out[m["conditionId"]] = m
                fresh_markets.append(m)
            done_count += 1
            if progress is not None and total_batches > 0:
                pct = progress_start + (progress_end - progress_start) * (done_count / total_batches)
                progress.update(
                    pct=pct,
                    detail=f"{done_count}/{total_batches} batches… ({len(cached_ids):,} cached)",
                )

    # Stage 3: write resolved markets back to cache. Only persist closed=true,
    # since open markets can flip and we don't want to serve stale data.
    if sb is not None and fresh_markets:
        try:
            to_cache = []
            seen = set()
            for m in fresh_markets:
                if not m.get("closed"):
                    continue
                cid = m.get("conditionId")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                to_cache.append({
                    "condition_id":   cid,
                    "slug":           m.get("slug", ""),
                    "closed":         True,
                    "outcome_prices": m.get("outcomePrices") or "[]",
                    "end_date":       m.get("endDate"),
                })
            # Upsert in chunks
            for i in range(0, len(to_cache), 500):
                sb.table("analyzer_market_cache").upsert(to_cache[i:i + 500]).execute()
        except Exception:
            # Cache writes are best-effort; never block on them
            pass

    return out


def dissect(activity: list[dict], *, fetch_markets: bool = True, progress=None) -> dict:
    """Run the weather-specific dissection over an activity stream."""
    trades = [t for t in activity if t.get("type") == "TRADE" and _is_weather(t)]
    if not trades:
        return {"weather_trades": 0}

    # GFS-phase histogram of buys
    phase_buckets = Counter()
    for t in trades:
        if t.get("side") != "BUY":
            continue
        ph = _gfs_phase_hours(t["timestamp"])
        bkt = int(ph)  # 0,1,2,3,4,5 → hours since last GFS run
        phase_buckets[bkt] += 1

    # City specialization
    city_stats: dict = defaultdict(lambda: {"trades": 0, "buy_volume": 0.0})
    for t in trades:
        c = _city_from_slug(t.get("eventSlug", ""))
        city_stats[c]["trades"] += 1
        if t.get("side") == "BUY":
            city_stats[c]["buy_volume"] += t["usdcSize"]
    city_list = sorted(
        [{"city": c, **v} for c, v in city_stats.items()],
        key=lambda x: x["trades"], reverse=True,
    )

    # Per-position aggregation.  We also stash the city here so we can
    # later compute per-city per-bucket P&L (useful when our edge zone
    # only overlaps the trader's in specific shared cities).
    per_pos: dict = defaultdict(lambda: {"net": 0.0, "buy_cost": 0.0, "sell_proceeds": 0.0,
                                          "buy_size": 0.0, "first_ts": None, "last_ts": None,
                                          "outcome": "", "title": "", "slug": "", "city": ""})
    for t in trades:
        key = (t["conditionId"], t["asset"])
        sign = 1 if t["side"] == "BUY" else -1
        p = per_pos[key]
        p["net"] += sign * t["size"]
        if t["side"] == "BUY":
            p["buy_cost"] += t["usdcSize"]
            p["buy_size"] += t["size"]
        else:
            p["sell_proceeds"] += t["usdcSize"]
        p["first_ts"] = min(t["timestamp"], p["first_ts"]) if p["first_ts"] else t["timestamp"]
        p["last_ts"] = max(t["timestamp"], p["last_ts"]) if p["last_ts"] else t["timestamp"]
        p["outcome"] = t.get("outcome", "")
        p["title"] = t.get("title", "")
        p["slug"] = t.get("slug", "")
        if not p["city"]:
            p["city"] = _city_from_slug(t.get("eventSlug", "") or t.get("slug", ""))

    # Price-bucket P&L (only meaningful if we resolve markets)
    #   cost        = NET cost basis after sells (can go negative on round-trips)
    #   gross_cost  = total $ deployed on buys (always positive, used for ROI)
    #   payout      = $ paid out at resolution for held tokens
    #   sells       = $ recovered before resolution
    # Per-bucket stats.  Three independent flows:
    #   resolved  → n, wins, cost, gross_cost, sells, payout
    #   open      → open (count), open_cost, open_mtm, open_best, open_worst
    #
    # open_mtm   = sum of (size × current_bid) − cost for truly-open positions.
    #              "What would I get if I liquidated everything right now?"
    # open_best  = sum of (size × $1) − cost — best-case all-win projection.
    # open_worst = sum of (−cost) — worst-case all-lose projection.
    # These three bracket the trader's TRUE current P&L on open positions.
    bucket_stats: dict = defaultdict(lambda: {
        "n": 0, "wins": 0, "cost": 0.0,
        "gross_cost": 0.0, "sells": 0.0,
        "payout": 0.0, "open": 0,
        "open_cost": 0.0, "open_mtm": 0.0,
        "open_best": 0.0, "open_worst": 0.0,
    })
    markets: dict = {}
    if fetch_markets:
        from .config import VENDOR_PNL  # noqa: F401
        with httpx.Client(http2=False, timeout=30.0) as client:
            markets = fetch_resolutions(
                client, list({k[0] for k in per_pos.keys()}), progress=progress,
            )

    # First pass: classify each position as resolved or truly-open and
    # accumulate the bucket totals for resolved positions.  Truly-open
    # positions go into a worklist for mark-to-market enrichment below.
    open_worklist: list[tuple[str, str, float, float]] = []  # (bucket, token, net_size, cost)
    for key, p in per_pos.items():
        if p["buy_size"] <= 0:
            continue
        avg_buy = p["buy_cost"] / p["buy_size"]
        if avg_buy < 0.02: b = "<0.02"
        elif avg_buy < 0.05: b = "0.02-0.05"
        elif avg_buy < 0.10: b = "0.05-0.10"
        elif avg_buy < 0.25: b = "0.10-0.25"
        elif avg_buy < 0.50: b = "0.25-0.50"
        else: b = ">=0.50"

        cost = p["buy_cost"] - p["sell_proceeds"]

        m = markets.get(key[0])
        if not m or not m.get("closed"):
            # Truly-open: queue for mark-to-market.  key[1] is the asset
            # (token_id) which is what we need to look up the book for.
            bucket_stats[b]["open"] += 1
            bucket_stats[b]["open_cost"] += cost
            open_worklist.append((b, key[1], p["net"], cost))
            continue
        op = json.loads(m.get("outcomePrices", "[]") or "[]")
        yes_won = (float(op[0]) > 0.5) if op else False
        held_won = (p["outcome"] == "Yes" and yes_won) or (p["outcome"] == "No" and not yes_won)
        payout = p["net"] * (1.0 if held_won else 0.0)
        bucket_stats[b]["n"] += 1
        if held_won:
            bucket_stats[b]["wins"] += 1
        bucket_stats[b]["cost"] += cost
        bucket_stats[b]["gross_cost"] += p["buy_cost"]
        bucket_stats[b]["sells"] += p["sell_proceeds"]
        bucket_stats[b]["payout"] += payout

    # Second pass: fetch current best bid for every truly-open position
    # and compute mark-to-market + best/worst-case bounds per bucket.
    # This is the change that addresses "we're just speculating" — by
    # showing the trader's liquidation value alongside the resolved P&L
    # we replace speculation with three concrete numbers per bucket.
    if open_worklist and fetch_markets:
        with httpx.Client(http2=False, timeout=30.0) as client:
            bids = fetch_open_position_bids(
                client,
                [(tid, net, cost) for _b, tid, net, cost in open_worklist],
                progress=progress,
            )
        for b, tid, net, cost in open_worklist:
            bid = bids.get(tid)
            # mark-to-market: if bid is None (book empty), treat as 0
            mtm_value = (net * bid) if bid is not None else 0.0
            mtm_pnl   = mtm_value - cost
            best_pnl  = (net * 1.0) - cost     # all wins resolve at $1
            worst_pnl = -cost                  # all losses pay $0
            bucket_stats[b]["open_mtm"]   += mtm_pnl
            bucket_stats[b]["open_best"]  += best_pnl
            bucket_stats[b]["open_worst"] += worst_pnl

    # ── Per-city per-bucket P&L ────────────────────────────────────────
    # Same accounting as the global bucket but sliced by city.  Used by
    # the prompt + UI to answer "where does the edge actually live"
    # when only some shared cities are profitable.  Aggregated for
    # ALL cities, not just shared ones — UI/prompt can filter to
    # shared cities for the per-city breakdown card.
    pcb: dict = defaultdict(lambda: defaultdict(lambda: {
        "n": 0, "wins": 0, "gross_cost": 0.0, "sells": 0.0,
        "payout": 0.0, "open": 0, "open_mtm": 0.0,
    }))
    # Build a quick lookup of bid by token for the open MTM allocation
    open_bid_lookup: dict[str, float | None] = {}
    if open_worklist and fetch_markets:
        # bids was computed above in scope; re-derive defensively in case
        # the previous block was skipped.
        try:
            open_bid_lookup = bids   # type: ignore[name-defined]
        except NameError:
            open_bid_lookup = {}

    for key, p in per_pos.items():
        if p["buy_size"] <= 0:
            continue
        avg_buy = p["buy_cost"] / p["buy_size"]
        if avg_buy < 0.02: b = "<0.02"
        elif avg_buy < 0.05: b = "0.02-0.05"
        elif avg_buy < 0.10: b = "0.05-0.10"
        elif avg_buy < 0.25: b = "0.10-0.25"
        elif avg_buy < 0.50: b = "0.25-0.50"
        else: b = ">=0.50"
        city = (p.get("city") or "unknown").lower()

        m = markets.get(key[0])
        if m and m.get("closed"):
            op = json.loads(m.get("outcomePrices", "[]") or "[]")
            yes_won = (float(op[0]) > 0.5) if op else False
            held_won = (p["outcome"] == "Yes" and yes_won) or (p["outcome"] == "No" and not yes_won)
            payout = p["net"] * (1.0 if held_won else 0.0)
            pcb[city][b]["n"] += 1
            if held_won:
                pcb[city][b]["wins"] += 1
            pcb[city][b]["gross_cost"] += p["buy_cost"]
            pcb[city][b]["sells"] += p["sell_proceeds"]
            pcb[city][b]["payout"] += payout
        else:
            # Truly open — record open count + mark-to-market contribution
            pcb[city][b]["open"] += 1
            cost = p["buy_cost"] - p["sell_proceeds"]
            bid = open_bid_lookup.get(key[1])
            if bid is not None:
                pcb[city][b]["open_mtm"] += (p["net"] * bid) - cost

    per_city_bucket_pnl: list[dict] = []
    for city, buckets in pcb.items():
        for b, s in buckets.items():
            resolved_pnl = s["payout"] + s["sells"] - s["gross_cost"]
            per_city_bucket_pnl.append({
                "city":               city,
                "bucket":             b,
                "n_resolved":         s["n"],
                "n_open":             s["open"],
                "wins":               s["wins"],
                "win_rate_pct":       (100 * s["wins"] / s["n"]) if s["n"] else 0.0,
                "gross_cost_usd":     round(s["gross_cost"], 2),
                "pnl_usd":            round(resolved_pnl, 2),
                "open_mtm_pnl":       round(s["open_mtm"], 2),
                "true_pnl_estimate":  round(resolved_pnl + s["open_mtm"], 2),
                "roi_pct": round(100 * resolved_pnl / s["gross_cost"], 1) if s["gross_cost"] > 0 else None,
            })
    # Sort by absolute true P&L impact, descending — most consequential first
    per_city_bucket_pnl.sort(key=lambda r: abs(r["true_pnl_estimate"]), reverse=True)

    # Hold time distribution
    holds = []
    for p in per_pos.values():
        if abs(p["net"]) < 0.01 and p["first_ts"] and p["last_ts"]:
            holds.append((p["last_ts"] - p["first_ts"]) / 3600)
    holds.sort()

    def pct(arr, q):
        if not arr:
            return 0.0
        idx = min(len(arr) - 1, int(q * len(arr)))
        return arr[idx]

    return {
        "weather_trades": len(trades),
        "gfs_phase_histogram": {str(k): v for k, v in sorted(phase_buckets.items())},
        "cities": city_list[:25],
        "price_bucket_pnl": [
            {
                "bucket": b,
                "n_resolved": s["n"],
                "n_open": s["open"],
                "wins": s["wins"],
                "win_rate_pct": (100 * s["wins"] / s["n"]) if s["n"] else 0.0,
                # cost_usd is the net cost still at risk (gross buys minus
                # sells already realized). Can go negative if they round-
                # tripped at a profit before resolution — that's not a bug.
                "cost_usd": round(s["cost"], 2),
                # gross_cost_usd = total dollars deployed on buys (always
                # positive). Use this as the ROI denominator.
                "gross_cost_usd": round(s["gross_cost"], 2),
                "sells_usd": round(s["sells"], 2),
                "payout_usd": round(s["payout"], 2),
                # pnl_usd = total returns (payout + sells) - gross buys.
                # Same as payout - net_cost. Either formulation works.
                "pnl_usd": round(s["payout"] + s["sells"] - s["gross_cost"], 2),
                # ROI on gross deployed, not net — so a profitable round-
                # trip shows positive ROI instead of a confusing "—".
                "roi_pct": round(
                    100 * (s["payout"] + s["sells"] - s["gross_cost"]) / s["gross_cost"],
                    1,
                ) if s["gross_cost"] > 0 else 0.0,
                # Flag positions the trader cashed out of before resolution.
                "round_tripped": s["sells"] > 0.5 * s["gross_cost"],

                # ── Open-position bracketing (NEW) ──
                # Three numbers that replace the previous "open: 29" black
                # box with concrete bounds on the trader's true P&L:
                #
                #   open_cost_usd   — total dollars still at risk on open
                #                     positions in this bucket.
                #   open_mtm_pnl    — P&L if the trader liquidated every
                #                     open position right now at the best
                #                     bid. Closest to "true unrealized."
                #   open_best_pnl   — P&L if ALL open positions win at $1.
                #   open_worst_pnl  — P&L if ALL open positions lose ($0).
                #
                # open_mtm_pnl ∈ [open_worst_pnl, open_best_pnl] always.
                "open_cost_usd":  round(s["open_cost"],  2),
                "open_mtm_pnl":   round(s["open_mtm"],   2),
                "open_best_pnl":  round(s["open_best"],  2),
                "open_worst_pnl": round(s["open_worst"], 2),
                # Combined honest estimate: resolved P&L + open mark-to-market.
                # This is the number that addresses "we're just speculating."
                "true_pnl_estimate": round(
                    s["payout"] + s["sells"] - s["gross_cost"] + s["open_mtm"], 2,
                ),
            }
            for b, s in sorted(bucket_stats.items())
        ],
        "hold_hours_distribution": {
            "n": len(holds),
            "p10": round(pct(holds, 0.10), 2),
            "p50": round(pct(holds, 0.50), 2),
            "p90": round(pct(holds, 0.90), 2),
            "max": round(max(holds), 2) if holds else 0.0,
        },
        # Per-city per-bucket P&L slice — see prompt rules for how to use.
        # Sorted by |true_pnl_estimate| desc so the most consequential cells
        # are first.  Capped at 200 to keep payload small for big traders.
        "per_city_bucket_pnl": per_city_bucket_pnl[:200],
    }
