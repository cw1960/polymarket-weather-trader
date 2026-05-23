"""
Resolver: fetch Polymarket outcomes for closed trade signals and compute P&L.

Called automatically at the top of each signal_engine run, and can be run
standalone:
    python scripts/resolver.py

How it works
------------
1. Fetch all trade_signal rows where forecast_date < today and pnl_usd IS NULL.
2. Group signals by (city, forecast_date).
3. For each group, find the Polymarket event via the city's series slug.
4. Scan the event's market prices — the bracket with YES price ≥ 0.99 is the winner.
   (Polymarket moves prices to 0/1 before the official `resolved` flag is set.)
5. Match winning conditionId against our stored conditionIds.
6. Write pnl_usd + resolved_at back to trade_signals.
7. Roll up to ladders: total_pnl_usd, winning_rungs, losing_rungs, status.

Note: the Gamma API's conditionIds query filter does NOT work reliably.
      We resolve by fetching the full event and reading market prices instead.
"""
import json
import time
import logging
import subprocess
import requests
from datetime import date, datetime, timezone
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY
import unicodedata
import re

GAMMA_BASE    = "https://gamma-api.polymarket.com"
REQUEST_DELAY = 0.3

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ── Notification helpers ───────────────────────────────────────────────────────

def _phase2_normalized_size(confidence: float) -> float:
    """Mirror of phase2_engine.phase2_trade_size() at $150 budget / $20 cap."""
    pct = 0.20 if confidence >= 0.95 else 0.15 if confidence >= 0.90 \
          else 0.10 if confidence >= 0.80 else 0.06
    return min(150.0 * pct, 20.0)


def _send_macos_notification(title: str, body: str) -> None:
    """Send a macOS notification. Silent if osascript is unavailable."""
    try:
        script = (
            f'display notification {json.dumps(body)} '
            f'with title {json.dumps(title)} '
            f'sound name "Ping"'
        )
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _build_phase2_notification(resolved_p2: list[dict]) -> tuple[str, str]:
    """
    Build (title, body) for a macOS notification summarising Phase 2 resolutions.

    resolved_p2 items must have keys:
      city, outcome, pnl_usd, recommended_position, confidence, trade_won
    """
    wins   = [r for r in resolved_p2 if r["trade_won"]]
    losses = [r for r in resolved_p2 if not r["trade_won"]]

    batch_norm_pnl = sum(
        r["pnl_usd"] * (_phase2_normalized_size(r["confidence"]) / r["recommended_position"])
        for r in resolved_p2
        if r["recommended_position"] > 0
    )

    title = (
        f"Weather Trader — {len(resolved_p2)} resolved  "
        f"{len(wins)}W/{len(losses)}L  ${batch_norm_pnl:+.2f}"
    )

    lines = []
    for r in sorted(resolved_p2, key=lambda x: -x["pnl_usd"]):
        ns   = _phase2_normalized_size(r["confidence"])
        npnl = r["pnl_usd"] * (ns / r["recommended_position"]) if r["recommended_position"] > 0 else 0
        icon = "✅" if r["trade_won"] else "❌"
        lines.append(f"{icon} {r['city']} [{r['outcome']}]  ${npnl:+.2f}")

    body = "\n".join(lines)
    return title, body


# ── Helpers ───────────────────────────────────────────────────────────────────

def _city_slug(city: str) -> str:
    nfkd = unicodedata.normalize("NFKD", city)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9\-]+", "-", ascii_str.lower().replace(" ", "-")).strip("-")
    return f"{base}-daily-weather"


def _get(url: str, params: dict = None) -> dict | list | None:
    try:
        r = requests.get(url, params=params, timeout=15)
        if not r.ok:
            return None
        return r.json()
    except Exception:
        return None


def _parse_prices(raw) -> list[float]:
    """Parse outcomePrices which may be a JSON string or a list."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, list):
        try:
            return [float(x) for x in raw]
        except Exception:
            return []
    return []


def _find_event_id_for_date(city: str, forecast_date: str,
                             series_cache: dict) -> str | None:
    """
    Return the Polymarket event id for (city, forecast_date).
    Caches series data per city to avoid repeated fetches.
    """
    slug = _city_slug(city)
    if slug not in series_cache:
        data = _get(f"{GAMMA_BASE}/series", params={"slug": slug})
        if not data:
            series_cache[slug] = {}
            return None
        series = data[0] if isinstance(data, list) else data
        # Build a date → event_id map
        date_map = {}
        for ev in series.get("events", []):
            end = ev.get("endDate", "")
            if end:
                d = end[:10]
                date_map[d] = str(ev["id"])
        series_cache[slug] = date_map
    return series_cache.get(slug, {}).get(forecast_date)


def _find_winner(markets: list[dict]) -> tuple[str | None, str | None]:
    """
    Return (conditionId, question) of the winning bracket (YES price >= 0.99),
    or (None, None) if no winner is clear yet.
    """
    for mkt in markets:
        prices = _parse_prices(mkt.get("outcomePrices"))
        if prices and prices[0] >= 0.99:
            return mkt.get("conditionId", ""), mkt.get("question", "")
    return None, None


def _compute_pnl(trade_won: bool, price: float, size_usd: float) -> float:
    """
    Unified P&L for both YES and NO trades.
    - trade_won=True  → payout: size_usd * (1/price - 1)
    - trade_won=False → loss:   -size_usd
    For YES trades: price = yes_price; trade_won = (bracket resolved YES).
    For NO  trades: price = no_price = 1 - yes_price; trade_won = (bracket did NOT resolve YES).
    """
    if trade_won:
        return round(size_usd * (1.0 / price - 1.0), 4)
    return round(-size_usd, 4)


# ── Station delta auto-updater ────────────────────────────────────────────────

# Bracket label patterns — same forms as fetch_markets.py produces
_DELTA_BELOW_RE = re.compile(r"≤(-?\d+)°([CF])")
_DELTA_RANGE_RE = re.compile(r"(-?\d+)-(-?\d+)°([CF])")
_DELTA_ABOVE_RE = re.compile(r"≥(-?\d+)°([CF])")
_DELTA_EXACT_RE = re.compile(r"^(-?\d+)°([CF])$")


def _bracket_midpoint_c(question: str) -> float | None:
    """
    Extract an approximate resolution temperature (°C) from a winning bracket label
    or market question.

    Returns None for tail brackets (≤X / ≥X) where the true temperature is unknown.
    For ranges the midpoint is used; for exact brackets the stated value is used.
    All values are returned in °C regardless of the market's native unit.
    """
    # Try label-style patterns first (compact forms stored in outcome/locked_bracket)
    m = _DELTA_EXACT_RE.match(question.strip())
    if m:
        t, unit = int(m.group(1)), m.group(2).upper()
        return (t - 32) * 5 / 9 if unit == "F" else float(t)

    m = _DELTA_RANGE_RE.search(question)
    if m:
        lo, hi, unit = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        mid = (lo + hi) / 2.0
        return (mid - 32) * 5 / 9 if unit == "F" else mid

    # Tail brackets: can't pin down the actual temperature
    if _DELTA_BELOW_RE.search(question) or _DELTA_ABOVE_RE.search(question):
        return None

    # Try verbose question text (market question fallback)
    m = re.search(r"between (-?\d+)[\s\-–—]+(-?\d+)°([CF])", question, re.IGNORECASE)
    if m:
        lo, hi, unit = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        mid = (lo + hi) / 2.0
        return (mid - 32) * 5 / 9 if unit == "F" else mid

    m = re.search(r"be (-?\d+)°([CF])(?:\s|$|\?|\.)", question, re.IGNORECASE)
    if m:
        t, unit = int(m.group(1)), m.group(2).upper()
        return (t - 32) * 5 / 9 if unit == "F" else float(t)

    return None


def _get_phase2_lock_temp(city: str, forecast_date: str) -> float | None:
    """
    Return the running_max_c at Phase 2 LOCK TIME for this city/date.

    Phase 2 signals store the running_max_c at the moment Phase 2 fired in
    the 'mean_high' column.  This is the correct reference for computing
    station bias because:
      - temp_readings.running_max_c = FINAL daily max (can be higher than lock-time)
      - Phase 2 signal mean_high     = running_max AT THE MOMENT WE TRADED

    Using the final daily max underestimates station bias for "premature lock"
    cities (where temp kept rising after Phase 2 fired) and overstates it for
    "station bias" cities (where temp was stable but the resolution source reads
    differently from the METAR).
    """
    try:
        res = (sb.table("trade_signals")
               .select("mean_high")
               .eq("city", city)
               .eq("forecast_date", forecast_date)
               .eq("signal_phase", "phase2")
               .not_.is_("mean_high", "null")
               .limit(1)
               .execute())
        if res.data and res.data[0].get("mean_high") is not None:
            return float(res.data[0]["mean_high"])
    except Exception:
        pass
    return None


# Resolutions Polymarket disclosed as oracle-bug-affected on 2026-05-18/19.
# Their winning_bracket was the wrong (minimum) bracket due to missing
# Weather Underground data, so we must NOT feed them to the delta_c learner.
# Add (city, forecast_date_iso) pairs here as Polymarket discloses more.
_ORACLE_BUG_RESOLUTIONS: set[tuple[str, str]] = {
    ("Miami",       "2026-05-17"),
    ("Mexico City", "2026-05-17"),
    ("Seoul",       "2026-05-17"),
    ("Hong Kong",   "2026-05-17"),
}

# Hard sanity cap on a single delta_c update. The historical "station vs.
# resolution-source" bias for any city sits well within ±2°C; an observed
# delta larger than this almost certainly indicates a bad resolution
# (oracle bug, voided market, premature lock) rather than a real station
# offset. We log loudly and skip rather than poison the learner.
_DELTA_SANITY_CAP_C: float = 2.0


def _update_city_delta(
    city: str,
    forecast_date: str,
    winner_question: str,
    log: logging.Logger,
) -> None:
    """
    After a market resolves, compute the observed station bias for this city/day
    and update resolution_stations.delta_c using adaptive exponential smoothing.

    Algorithm:
      alpha = max(0.20, 1 / (1 + samples))   ← high weight early, stabilises later
      new_delta = old_delta * (1 - alpha) + observed_delta * alpha

    This means:
      - After 1 observation:  alpha=0.50, new value gets 50% weight
      - After 4 observations: alpha=0.25
      - After 5+:             alpha=0.20 (floor), new value gets 20% weight

    Reference temperature choice:
      ONLY runs when this city had a Phase 2 trade today (stored in
      trade_signals.mean_high = running_max at lock time).

      Phase 1 signals do NOT provide a valid reference temperature because:
        - Phase 1 fires in the morning based on forecast data.
        - The "final running_max" from temp_readings is not a lock-time reading.
        - Comparing bracket midpoint vs. final daily max produces noise, not bias.

      Without a Phase 2 lock temp, the function returns without updating delta.

      The Phase 2 lock-time design correctly separates two failure modes:
        "premature lock" — Phase 2 fired when temp was still rising.
            lock-time running_max < final running_max ≈ resolution temp.
            observed_delta ≈ 0 (no station bias, purely a timing issue).
            → delta correctly stays near 0; fix is more stability, not delta.

        "station bias"   — Phase 2 fired on a stable temperature, but the
            resolution source reads differently from our METAR.
            lock-time running_max ≈ final running_max ≠ resolution temp.
            → observed_delta captures the true systematic bias.
    """
    # 0. Skip resolutions Polymarket disclosed as oracle-bug-affected.
    if (city, forecast_date) in _ORACLE_BUG_RESOLUTIONS:
        log.warning(
            f"  delta update: {city} {forecast_date} — SKIPPED "
            f"(oracle-bug-affected resolution per Polymarket 2026-05-18/19 disclosure)"
        )
        return

    # 1. Infer the resolution station temperature from the winning bracket
    resolution_temp_c = _bracket_midpoint_c(winner_question)
    if resolution_temp_c is None:
        log.debug(f"  delta update: {city} {forecast_date} — tail bracket, skipping")
        return

    # 2. Require Phase 2 lock-time temperature — Phase 1 data is not a valid reference
    phase2_lock_temp = _get_phase2_lock_temp(city, forecast_date)
    if phase2_lock_temp is None:
        log.debug(f"  delta update: {city} {forecast_date} — no Phase 2 lock temp, skipping")
        return

    running_max_c = phase2_lock_temp
    ref_source = "phase2_lock"

    observed_delta = round(resolution_temp_c - running_max_c, 2)

    # 2a. Sanity cap — anything beyond ±2°C is almost certainly a bad
    # resolution (oracle bug, voided market, premature lock), not station
    # bias. The São Paulo +7°C poisoning on 2026-05-19 is the canonical
    # example. Log loudly and skip rather than write a contaminated value.
    if abs(observed_delta) > _DELTA_SANITY_CAP_C:
        log.warning(
            f"  delta update: {city} {forecast_date} — SKIPPED "
            f"(|observed_delta|={abs(observed_delta):.2f}°C > cap {_DELTA_SANITY_CAP_C}°C; "
            f"monitor={running_max_c:.1f}°C resolution≈{resolution_temp_c:.1f}°C). "
            f"If this is real station bias, investigate before zeroing the cap."
        )
        return

    # 3. Fetch current delta_c and sample count
    try:
        rs = (sb.table("resolution_stations")
              .select("delta_c, delta_samples")
              .eq("city", city)
              .limit(1)
              .execute())
        if not rs.data:
            log.debug(f"  delta update: {city} — no resolution_stations row")
            return
        current_delta   = float(rs.data[0].get("delta_c") or 0.0)
        current_samples = int(rs.data[0].get("delta_samples") or 0)
    except Exception as e:
        log.debug(f"  delta update: {city} error reading resolution_stations: {e}")
        return

    # 4. Adaptive smoothing
    alpha     = max(0.20, 1.0 / (1.0 + current_samples))
    new_delta = round(current_delta * (1.0 - alpha) + observed_delta * alpha, 3)
    new_samples = current_samples + 1

    # 5. Write back
    try:
        sb.table("resolution_stations").update({
            "delta_c":       new_delta,
            "delta_samples": new_samples,
        }).eq("city", city).execute()

        log.info(
            f"  📐 delta {city} (src={ref_source}): "
            f"monitor={running_max_c:.1f}°C  resolution≈{resolution_temp_c:.1f}°C  "
            f"observed={observed_delta:+.2f}°C  "
            f"old_delta={current_delta:+.3f}°C → new_delta={new_delta:+.3f}°C "
            f"(n={new_samples}, α={alpha:.2f})"
        )
    except Exception as e:
        log.warning(f"  delta update: {city} write failed: {e}")


# ── Main resolver ─────────────────────────────────────────────────────────────

def resolve_signals(log: logging.Logger | None = None) -> dict:
    """
    Resolve all unresolved signals for past forecast dates.

    Returns {resolved: int, still_open: int, total_pnl: float}
    """
    _log = log or logging.getLogger(__name__)
    # Use UTC date — the Mac runs CST (UTC-6) so after 6 PM local the UTC date
    # is already "tomorrow".  Polymarket settles Asian/European markets well before
    # local midnight, so we must use UTC to catch them the same evening.
    today = datetime.now(timezone.utc).date().isoformat()

    # Filter on pnl_usd IS NULL — 'outcome' column stores the bracket label, not YES/NO
    # Include today (lte) — intraday markets often resolve before midnight.
    # Explicit .limit(50_000) defends against Supabase's silent 1000-row
    # default reply cap.  Without this, a multi-week resolver outage could
    # build a backlog >1000 where the silently-dropped rows are never
    # picked up on subsequent runs either (the cap is order-dependent, so
    # the same rows are repeatedly excluded).  50k handles any realistic
    # backlog while staying well under Supabase's hard max.
    res = (
        sb.table("trade_signals")
        .select("*")
        .lte("forecast_date", today)
        .is_("pnl_usd", "null")
        .limit(50_000)
        .execute()
    )
    signals = res.data or []

    if not signals:
        _log.info("  Resolver: no unresolved signals.")
        return {"resolved": 0, "still_open": 0, "total_pnl": 0.0}

    _log.info(f"  Resolver: {len(signals)} unresolved signals for past dates.")

    # Group by (city, forecast_date)
    groups: dict[tuple, list] = {}
    for sig in signals:
        key = (sig["city"], sig["forecast_date"])
        groups.setdefault(key, []).append(sig)

    series_cache: dict[str, dict] = {}
    resolved_count = 0
    still_open = 0
    total_pnl = 0.0
    ladder_stats: dict[str, dict] = {}
    resolved_p2: list[dict] = []   # Phase 2 resolutions for notification

    for (city, forecast_date), sigs in groups.items():
        # Find the Polymarket event for this city/date
        event_id = _find_event_id_for_date(city, forecast_date, series_cache)
        if not event_id:
            _log.info(f"  Resolver: no event found for {city} {forecast_date}")
            still_open += len(sigs)
            continue

        time.sleep(REQUEST_DELAY)
        ev = _get(f"{GAMMA_BASE}/events/{event_id}")
        if not ev:
            still_open += len(sigs)
            continue

        markets = ev.get("markets", [])
        winner_cid, winner_question = _find_winner(markets)

        if winner_cid is None:
            _log.info(f"  Resolver: {city} {forecast_date} — market not yet resolved")
            still_open += len(sigs)
            continue

        # Auto-update station delta based on today's resolution result
        _update_city_delta(city, forecast_date, winner_question, _log)

        # Resolve each signal in this group
        for sig in sigs:
            our_cid     = sig.get("condition_id", "")
            sig_side    = sig.get("side", "YES")   # "YES" or "NO"
            bracket_won = (our_cid == winner_cid)

            # Determine whether THIS trade won
            trade_won = bracket_won if sig_side == "YES" else not bracket_won

            # Observation rows had no real money deployed — record $0 P&L
            # regardless of whether the bracket won or lost.  This catches
            # downgrades from phase2_engine's cap-final-guard (e.g. Tel Aviv
            # 33°C 2026-05-17, where buy_price 89¢ blew through the 30¢ cap
            # so order_status was forced to 'observation' but
            # recommended_position remained at the original $15 — without
            # this guard the resolver would compute a fictitious -$15 loss).
            if sig.get("order_status") == "observation":
                price    = float(sig.get("fill_price") or sig.get("market_price") or 0)
                size_usd = 0.0
                pnl      = 0.0
            else:
                # Use the actual fill_price (what we paid) for P&L if present;
                # fall back to signal-time market_price for paper trades or rows
                # where fill_price was never set. This matters: a $15 fill at
                # 27¢ pays differently than the same bet at 28¢ if it wins.
                fp_raw   = sig.get("fill_price")
                price    = float(fp_raw) if fp_raw is not None else float(sig["market_price"])

                # Use the ACTUAL filled cost basis when available.  Partial fills
                # (e.g. only $1.50 of a $15 order at low-price extremes) would
                # otherwise be P&L'd as if the full $15 was deployed — that's
                # how Cape Town's pnl_usd ended up at +$42.69 instead of +$3.50.
                fs_raw   = sig.get("filled_size_usd")
                size_usd = (float(fs_raw) if fs_raw is not None
                            else float(sig["recommended_position"]))
                pnl      = _compute_pnl(trade_won, price, size_usd)
            total_pnl += pnl

            # Compute miss distance — degrees between our bet bracket and actual.
            # For YES trades: 0 = correct bracket, 1 = off by one bracket, etc.
            # For tail brackets / unparseable text, leave as None.
            miss_distance_c = None
            try:
                bet_temp_c = _bracket_midpoint_c(sig.get("outcome", "") or "")
                actual_temp_c = _bracket_midpoint_c(winner_question or "")
                if bet_temp_c is not None and actual_temp_c is not None:
                    miss_distance_c = round(abs(bet_temp_c - actual_temp_c), 2)
            except Exception:
                pass

            update_row = {
                "pnl_usd":          pnl,
                "resolved_at":      datetime.now(timezone.utc).isoformat(),
                "winning_bracket":  winner_question,   # winning bracket for all signals in group
                "actual_outcome":   bracket_won,       # True = this signal's bracket resolved YES
            }
            if miss_distance_c is not None:
                update_row["miss_distance_c"] = miss_distance_c

            try:
                sb.table("trade_signals").update(update_row).eq("id", sig["id"]).execute()
            except Exception as e:
                # Column may not exist yet — retry without it
                if "miss_distance_c" in str(e):
                    update_row.pop("miss_distance_c", None)
                    sb.table("trade_signals").update(update_row).eq("id", sig["id"]).execute()
                    _log.warning(
                        f"  trade_signals missing miss_distance_c column — "
                        f"run migrate_miss_distance.sql to enable precision tracking"
                    )
                else:
                    raise

            lid = sig.get("ladder_id")
            if lid:
                entry = ladder_stats.setdefault(lid, {"pnl": 0.0, "wins": 0, "losses": 0})
                entry["pnl"]    += pnl
                entry["wins"]   += (1 if trade_won else 0)
                entry["losses"] += (0 if trade_won else 1)

            # Collect Phase 2 resolutions for notification (skip $0.01 observation trades)
            if sig.get("signal_phase") == "phase2":
                resolved_p2.append({
                    "city":               city,
                    "outcome":            sig.get("outcome", ""),
                    "pnl_usd":            pnl,
                    "recommended_position": size_usd,
                    "confidence":         float(sig.get("confidence") or 0),
                    "trade_won":          trade_won,
                })

            sign  = "✅" if trade_won else "❌"
            label = sig.get("outcome", "")   # bracket label stored here
            _log.info(
                f"  {sign} {sig_side} {city} {forecast_date} [{label}]: "
                f"{'WON' if trade_won else 'LOST'} → ${pnl:+.2f}  "
                f"(${size_usd:.2f} @ {price*100:.1f}c)"
            )
            resolved_count += 1

    # Update ladder summaries
    for lid, stats in ladder_stats.items():
        sb.table("ladders").update({
            "total_pnl_usd": round(stats["pnl"], 2),
            "winning_rungs": stats["wins"],
            "losing_rungs":  stats["losses"],
            "status":        "closed",
        }).eq("id", lid).execute()

    _log.info(
        f"  Resolver: {resolved_count} resolved | {still_open} still open | "
        f"net P&L this batch: ${total_pnl:+.2f}"
    )

    # Resolve any pending exit simulations (shadow mode)
    try:
        from exit_sim import resolve_simulations
        resolve_simulations(log_obj=_log)
    except Exception as _sim_err:
        _log.warning(f"  Exit simulation resolution skipped: {_sim_err}")

    # Send macOS notification for any Phase 2 trades that just resolved
    if resolved_p2:
        title, body = _build_phase2_notification(resolved_p2)
        _send_macos_notification(title, body)
        _log.info(f"  Notification sent: {len(resolved_p2)} Phase 2 trades")

    return {"resolved": resolved_count, "still_open": still_open, "total_pnl": round(total_pnl, 2)}


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s UTC | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    result = resolve_signals()
    print(f"\nDone. Resolved={result['resolved']}  StillOpen={result['still_open']}  "
          f"NetPnL=${result['total_pnl']:+.2f}")
