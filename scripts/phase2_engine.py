"""
Phase 2 Signal Engine — Bracket Confirmation Trades
=====================================================
Called by temp_monitor.py when a city's daily-high bracket is confirmed locked.

Strategy:
  Once the running daily maximum has locked a specific temperature bracket
  with >= PHASE2_MIN_CONFIDENCE and it is past 2 PM local city time,
  buy the corresponding YES market on Polymarket if:
    - The YES price is still below PHASE2_MAX_YES_PRICE (85¢)
    - The Phase 2 daily budget has not been exhausted
    - This city has not already had a Phase 2 trade today

Sizing logic:
  Phase 2 budget = bankroll × DAILY_BANKROLL_PCT × PHASE2_BUDGET_PCT
  Per-trade size scales with confidence:
    conf 0.70–0.79 →  6% of Phase 2 daily budget
    conf 0.80–0.89 → 10% of Phase 2 daily budget
    conf 0.90–0.94 → 15% of Phase 2 daily budget
    conf ≥ 0.95    → 20% of Phase 2 daily budget
  Maximum per single trade: 20% of Phase 2 daily budget
  (ensures ≥5 trades before exhausting budget; prevents EU cities crowding out US)

Bankroll:
  Stored in system_config table. Updated by the end-of-day reconciler.
  Daily budget = bankroll × DAILY_BANKROLL_PCT.
  Deployed today (Phase 1 + Phase 2) queried from trade_signals.created_at.

Running standalone (for testing):
  python scripts/phase2_engine.py --city Tokyo --date 2026-05-01 --bracket "≥21°C" --max-c 21.3 --confidence 0.88
"""
import time
import logging
import argparse
import requests
import unicodedata
import re
from datetime import date, datetime, timezone

from supabase import create_client
from config import (
    SUPABASE_URL, SUPABASE_KEY, CITY_UNITS, CITY_TIMEZONES,
    DAILY_BANKROLL_PCT, PHASE2_BUDGET_PCT,
    PHASE2_MAX_YES_PRICE, PHASE2_MIN_CONFIDENCE, PHASE2_MIN_MODEL_PROB,
    DEFAULT_BANKROLL_USD, PHASE2_FIXED_DAILY_USD, PHASE2_MAX_TRADE_USD,
    PHASE2_CALIBRATED_TRADE_USD, PHASE2_CALIBRATION_MIN_SAMPLES,
    PHASE2_MAX_CALIBRATED_PRICE,
    NO_SWEEP_SAFETY_MARGIN_F, NO_SWEEP_SAFETY_MARGIN_C,
    NO_SWEEP_MIN_LOCAL_HOUR, NO_SWEEP_MAX_YES_PRICE,
    NO_SWEEP_MIN_YES_PRICE, NO_SWEEP_CAP_PER_CITY_USD,
    NO_SWEEP_MAX_PER_BRACKET,
)
# Edge-gate + sizing-schedule + guardrails introduced 2026-05-19.
# The static PHASE2_MAX_CALIBRATED_PRICE price cap is no longer used.
from sizing import size_for_yes_lock, size_for_no_sweep, no_sweep_max_brackets_per_city
from guardrails import check_trade_allowed

log = logging.getLogger(__name__)
sb  = create_client(SUPABASE_URL, SUPABASE_KEY)

GAMMA_BASE    = "https://gamma-api.polymarket.com"
REQUEST_DELAY = 0.3


# ── Bankroll helpers ──────────────────────────────────────────────────────────

def get_config(key: str, default: float) -> float:
    """Read a float value from system_config table."""
    try:
        res = sb.table("system_config").select("value").eq("key", key).single().execute()
        return float(res.data["value"]) if res.data else default
    except Exception:
        return default


def set_config(key: str, value: float) -> None:
    sb.table("system_config").upsert(
        {"key": key, "value": str(round(value, 4)),
         "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="key",
    ).execute()


def get_bankroll() -> float:
    return get_config("bankroll_usd", DEFAULT_BANKROLL_USD)


def get_daily_budget() -> tuple[float, float, float]:
    """
    Returns (total_daily, phase1_budget, phase2_budget).

    Phase 1 is now observation-only — no capital deployed. phase1_budget = 0.
    Phase 2 uses a fixed daily dollar amount (PHASE2_FIXED_DAILY_USD) rather
    than a bankroll percentage, so it doesn't balloon as the bankroll grows.
    The fixed amount is stored in system_config as 'phase2_fixed_daily_usd'.
    """
    phase2 = get_config("phase2_fixed_daily_usd", PHASE2_FIXED_DAILY_USD)
    return phase2, 0.0, phase2


def get_today_deployed_phase2() -> tuple[float, list[dict]]:
    """
    Sum of Phase 2 capital *actually deployed* today (UTC calendar day).

    Important: failed orders never reached the exchange and must NOT
    consume budget.  Otherwise a string of order-placement failures
    would falsely choke off subsequent good trades.  Observation rows
    (recommended_position <= 1) are also excluded.

    Returns (total_deployed, list of {city, size} dicts for logging).
    """
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    try:
        res = (sb.table("trade_signals")
               .select("city, recommended_position, order_status")
               .eq("signal_phase", "phase2")
               .gt("recommended_position", 1)
               .gte("created_at", today_start)
               .execute())
        rows = [r for r in (res.data or [])
                if r.get("order_status") not in (None, "failed")]
        total = sum(float(r["recommended_position"]) for r in rows)
        breakdown = [{"city": r["city"], "size": float(r["recommended_position"])} for r in rows]
        return total, breakdown
    except Exception:
        return 0.0, []


def update_bankroll(net_pnl: float) -> float:
    """Add net_pnl to bankroll. Returns new bankroll. Call after daily resolution."""
    from config import LIVE_TRADING
    current = get_bankroll()
    new_val = round(current + net_pnl, 2)
    set_config("bankroll_usd", new_val)
    log.info(f"  Bankroll updated: ${current:.2f} → ${new_val:.2f} ({net_pnl:+.2f})")

    # Write a daily snapshot so the dashboard can read the authoritative balance.
    # Delete-then-insert avoids needing a DB unique constraint on snapshot_date.
    try:
        today = date.today().isoformat()
        sb.table("bankroll_snapshots").delete().eq("snapshot_date", today).execute()
        sb.table("bankroll_snapshots").insert({
            "snapshot_date":    today,
            "total_value":      new_val,
            "cash":             new_val,   # all cash — positions closed at resolution
            "daily_pnl":        round(net_pnl, 4),
            "active_positions": 0,
            "is_paper":         not LIVE_TRADING,
        }).execute()
        log.info(f"  Bankroll snapshot written for {today}: ${new_val:.2f}")
    except Exception as e:
        log.warning(f"  bankroll_snapshots write failed: {e}")

    return new_val


def phase2_trade_size(confidence: float, phase2_budget: float) -> float:
    """
    Scale trade size by confidence level.
    Returns dollar amount to risk on this Phase 2 trade.

    Budget is now fixed at $150/day. Percentage tiers scale with the budget
    but are additionally capped at PHASE2_MAX_TRADE_USD ($20) so no single
    trade consumes a disproportionate share.

    At $150 budget the effective per-trade sizes are:
      conf ≥0.95 → min($150×20%, $20) = $20
      conf ≥0.90 → min($150×15%, $20) = $20
      conf ≥0.80 → min($150×10%, $20) = $15
      conf ≥0.70 → min($150×06%, $20) = $9
    This ensures ≥7 trades before budget exhaustion, preventing early EU
    cities from crowding out later US cities.
    """
    if confidence >= 0.95:
        pct = 0.20
    elif confidence >= 0.90:
        pct = 0.15
    elif confidence >= 0.80:
        pct = 0.10
    else:
        pct = 0.06
    raw = phase2_budget * pct
    # Hard dollar cap (prevents over-sizing on large budgets) and floor at $1.00
    capped = min(raw, PHASE2_MAX_TRADE_USD)
    return round(max(capped, 1.00), 2)


# ── Polymarket market lookup ──────────────────────────────────────────────────

# Per-run cache: (city, forecast_date) → (markets list, event_slug)
# Prevents duplicate Gamma API calls when find_market_for_bracket and
# _execute_no_sweep are both called for the same city on the same run.
_event_cache: dict[tuple[str, str], tuple[list[dict], str]] = {}


def _city_slug(city: str) -> str:
    nfkd = unicodedata.normalize("NFKD", city)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9\-]+", "-", ascii_str.lower().replace(" ", "-")).strip("-")
    return f"{base}-daily-weather"


def _get(url: str, params: dict | None = None) -> dict | list | None:
    try:
        r = requests.get(url, params=params, timeout=15)
        return r.json() if r.ok else None
    except Exception:
        return None


def _parse_prices(raw) -> list[float]:
    import json
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


def _fetch_event_markets(city: str, forecast_date: str) -> tuple[list[dict], str]:
    """
    Return (markets, event_slug) for a city's Polymarket event on forecast_date.
    Results are cached for the lifetime of the process so Phase 2 YES and NO
    sweep share a single API round-trip per city per day.
    """
    key = (city, forecast_date)
    if key in _event_cache:
        return _event_cache[key]

    slug = _city_slug(city)
    time.sleep(REQUEST_DELAY)
    series = _get(f"{GAMMA_BASE}/series", params={"slug": slug})
    if not series:
        log.warning(f"  {city}: could not fetch series from Gamma")
        _event_cache[key] = ([], "")
        return [], ""

    series_data = series[0] if isinstance(series, list) else series
    event_id = None
    for ev in series_data.get("events", []):
        if ev.get("endDate", "")[:10] == forecast_date:
            event_id = str(ev["id"])
            break

    if not event_id:
        log.warning(f"  {city}: no Polymarket event found for {forecast_date}")
        _event_cache[key] = ([], "")
        return [], ""

    time.sleep(REQUEST_DELAY)
    event = _get(f"{GAMMA_BASE}/events/{event_id}")
    if not event:
        _event_cache[key] = ([], "")
        return [], ""

    markets   = event.get("markets", [])
    slug_val  = series_data.get("slug", "")
    _event_cache[key] = (markets, slug_val)
    return markets, slug_val


def _bracket_matches_question(bracket_clean: str, question: str) -> bool:
    """
    Return True if a bracket label matches a Polymarket question string.

    Handles two cases:
    1. Direct substring — works for middle brackets ("22°c", "70–73°f", etc.)
    2. Tail brackets where Polymarket uses natural language instead of ≥/≤:
         "≥74°f"  →  question contains "74" AND ("or higher" / "or above" / …)
         "≤32°f"  →  question contains "32" AND ("or lower"  / "or below"  / …)
    """
    # Case 1: direct substring match
    if bracket_clean in question:
        return True

    # Case 2: tail brackets with ≥ / >= symbol
    if bracket_clean.startswith("≥") or bracket_clean.startswith(">="):
        num_match = re.search(r"(\d+(?:\.\d+)?)", bracket_clean)
        if num_match:
            num = num_match.group(1)
            if num in question and any(
                phrase in question
                for phrase in ("or higher", "or above", "and above", "and higher",
                               "& above", "& higher", "+ above", "or more")
            ):
                return True

    # Case 3: tail brackets with ≤ / <= symbol
    if bracket_clean.startswith("≤") or bracket_clean.startswith("<="):
        num_match = re.search(r"(\d+(?:\.\d+)?)", bracket_clean)
        if num_match:
            num = num_match.group(1)
            if num in question and any(
                phrase in question
                for phrase in ("or lower", "or below", "and below", "and lower",
                               "& below", "& lower", "or less")
            ):
                return True

    return False


def _extract_bracket_temp(label: str) -> int | None:
    """Extract the integer temperature from a bracket label like '30°C', '≥32°C', '70-71°F'."""
    nums = re.findall(r"-?\d+", label or "")
    if not nums:
        return None
    # For ranges (e.g. '70-71°F'), use the lower bound
    return int(nums[0])


def find_market_for_bracket(city: str, forecast_date: str, locked_bracket: str) -> dict | None:
    """
    Return the Polymarket market whose question matches the locked bracket label.
    Uses the per-run event cache — no extra API call if _fetch_event_markets was
    already called for this city/date.

    Two-pass matching:
      1. Exact text match via _bracket_matches_question (preferred)
      2. Numeric-nearest fallback when no exact match exists, e.g. when our
         locked bracket is "30°C" but Polymarket only lists 31°C+ for that
         tropical city. The fallback selects the available bracket whose
         temperature is closest to ours, capping the distance at 1°C/2°F so
         we never silently bet on a far-off bracket.
    """
    markets, event_slug = _fetch_event_markets(city, forecast_date)
    if not markets:
        return None

    bracket_clean = locked_bracket.strip().lower()

    # ── Pass 1: exact text match ─────────────────────────────────────────────
    candidates = []
    for mkt in markets:
        question = mkt.get("question", "").lower()
        if _bracket_matches_question(bracket_clean, question):
            prices = _parse_prices(mkt.get("outcomePrices"))
            yes_price = prices[0] if prices else None
            if yes_price is not None:
                candidates.append((round(yes_price, 4), mkt))

    if candidates:
        yes_price, mkt = max(candidates, key=lambda x: x[0])
        return {
            "condition_id": mkt.get("conditionId", ""),
            "question":     mkt.get("question", ""),
            "yes_price":    yes_price,
            "no_price":     round(1 - yes_price, 4),
            "event_slug":   event_slug,
        }

    # ── Pass 2: numeric-nearest fallback ─────────────────────────────────────
    # Polymarket may not list our exact bracket (e.g. tropical cities skip
    # cool brackets). Find the nearest available bracket within 1 degree.
    locked_temp = _extract_bracket_temp(locked_bracket)
    if locked_temp is None:
        log.warning(f"  {city}: no market found and bracket '{locked_bracket}' has no parseable temperature")
        return None

    unit = CITY_UNITS.get(city, "C")
    max_distance = 2 if unit == "F" else 1   # tolerate 1°C / 2°F off

    nearest = None
    nearest_dist = max_distance + 1
    for mkt in markets:
        question = mkt.get("question", "")
        # Parse the temp from the question text directly
        nums = re.findall(r"-?\d+", question)
        if not nums:
            continue
        # The first number after "be" is the bracket temperature
        m = re.search(r"\bbe\s+(-?\d+)", question, re.IGNORECASE)
        if m:
            mkt_temp = int(m.group(1))
        else:
            mkt_temp = int(nums[0])

        dist = abs(mkt_temp - locked_temp)
        if dist <= max_distance and dist < nearest_dist:
            prices = _parse_prices(mkt.get("outcomePrices"))
            yes_price = prices[0] if prices else None
            if yes_price is not None:
                nearest = (round(yes_price, 4), mkt, mkt_temp)
                nearest_dist = dist

    if nearest is None:
        log.warning(
            f"  {city}: no market found for bracket '{locked_bracket}' "
            f"(neither exact match nor within {max_distance}° fallback)"
        )
        return None

    yes_price, mkt, mkt_temp = nearest
    log.info(
        f"  {city}: exact match for '{locked_bracket}' failed — "
        f"using nearest bracket {mkt_temp}°{unit} (distance: {nearest_dist}°)"
    )
    return {
        "condition_id": mkt.get("conditionId", ""),
        "question":     mkt.get("question", ""),
        "yes_price":    yes_price,
        "no_price":     round(1 - yes_price, 4),
        "event_slug":   event_slug,
    }


# ── Phase 2 signal execution ──────────────────────────────────────────────────

def already_swept_today(city: str, forecast_date: str) -> bool:
    """
    DEPRECATED 2026-05-21. The day-level block was overly conservative — it
    prevented the bot from acting on better intraday information later in the
    day. Replaced with per-bracket dedup via brackets_swept_today() below.

    Kept for backward compatibility only. Returns False so callers don't
    inadvertently still gate on this.
    """
    return False


def brackets_swept_today(city: str, forecast_date: str) -> set[str]:
    """
    Return the SET of bracket labels we've already placed NO trades on for
    this city today. Used to prevent firing the SAME bracket twice while
    still permitting fresh brackets to be added through the day as temps move.

    Failed rows don't count — those never reached the exchange. Failed = retry-eligible.
    """
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    try:
        res = (sb.table("trade_signals")
               .select("outcome, order_status")
               .eq("city", city)
               .eq("forecast_date", forecast_date)
               .eq("signal_phase", "phase2_sweep")
               .eq("side", "NO")
               .gte("created_at", today_start)
               .execute())
    except Exception as e:
        log.warning(f"  [NO Sweep] brackets_swept_today query failed for {city}: {e}")
        return set()
    return {
        r["outcome"]
        for r in (res.data or [])
        if r.get("outcome") and r.get("order_status") != "failed"
    }


def _execute_no_sweep(
    city:          str,
    forecast_date: str,
    running_max_c: float,
    delta_c:       float,
    dry_run:       bool = False,
) -> list[dict]:
    """
    Buy NO on every bracket that is physically impossible given the confirmed
    running maximum temperature.

    A bracket is eligible when its upper bound sits at least
    NO_SWEEP_SAFETY_MARGIN below the delta-adjusted running_max — far enough
    that even a worst-case delta error cannot put us in that bracket.

    Total outlay is capped at NO_SWEEP_CAP_PER_CITY_USD spread evenly across
    all eligible brackets (each bracket also capped at NO_SWEEP_MAX_PER_BRACKET).

    Returns a list of {bracket, size, no_price} dicts for logging.
    """
    import json as _json
    from zoneinfo import ZoneInfo

    # ── Guardrails (paper-trade mode when blocked) ──────────────────────────
    # When a guardrail blocks, we still compute the same candidate set and
    # write $0.01 observation rows for every chosen bracket. That lets us
    # paper-trade the new gate end-to-end (NO sweep included) and
    # accumulate calibration data without any real-money exposure.
    decision = check_trade_allowed()
    paper_only = not decision.allowed
    if paper_only:
        log.info(f"  [NO Sweep] 📝 {city}: guardrail [{decision.guardrail}] blocks ({decision.reason}) — paper-trade mode")

    # ── time-of-day gate ────────────────────────────────────────────────────
    tz_name    = CITY_TIMEZONES.get(city, "UTC")
    local_now  = datetime.now(ZoneInfo(tz_name))
    local_hour = local_now.hour
    if local_hour < NO_SWEEP_MIN_LOCAL_HOUR:
        log.info(f"  [NO Sweep] {city}: too early (local {local_hour}h < {NO_SWEEP_MIN_LOCAL_HOUR}h) — skip")
        return []

    # ── per-bracket dedup (replaces 2026-05-20's day-level block) ──────────
    # We allow MULTIPLE sweep cycles per day so the bot can act on fresh
    # intraday information. The constraint is per-bracket: never fire NO on
    # the same (city, date, bracket) pair twice. Brackets already-traded
    # today get skipped during candidate selection below.
    already_traded_brackets = brackets_swept_today(city, forecast_date)
    if already_traded_brackets:
        log.info(f"  [NO Sweep] {city}: {len(already_traded_brackets)} bracket(s) already swept today — those will be excluded")

    # ── load buckets from ladder row ────────────────────────────────────────
    buckets: list[dict] = []
    try:
        lr = (sb.table("ladders")
              .select("buckets_json")
              .eq("city", city)
              .eq("forecast_date", forecast_date)
              .eq("status", "open")
              .limit(1)
              .execute())
        raw = lr.data[0].get("buckets_json") if lr.data else None
        if raw:
            buckets = _json.loads(raw)
    except Exception as e:
        log.warning(f"  [NO Sweep] {city}: bucket load error — {e}")

    if not buckets:
        log.info(f"  [NO Sweep] {city}: no bucket data available — skip")
        return []

    # ── load post-fix ensemble members for member-count probabilities ───────
    # The new edge gate needs model_prob_yes for EACH bucket. We compute it
    # by counting how many ensemble members fall inside the bucket's [low,
    # high] range. Members are already bias-corrected at fetch time
    # (forecast_bias.py + fetch_forecasts.py), so no further shift needed.
    members_c: list[float] = []
    try:
        ef = (sb.table("ensemble_forecasts")
              .select("raw_members,ecmwf_members")
              .eq("city", city).eq("forecast_date", forecast_date)
              .order("created_at", desc=True).limit(1).execute())
        if ef.data:
            members_c = [float(m) for m in (ef.data[0].get("raw_members") or []) if m is not None] \
                      + [float(m) for m in (ef.data[0].get("ecmwf_members") or []) if m is not None]
    except Exception as e:
        log.warning(f"  [NO Sweep] {city}: ensemble fetch failed — {e}")
    if not members_c:
        log.info(f"  [NO Sweep] {city}: no ensemble members available — skip")
        return []

    # ── INTRADAY CONDITIONING (added 2026-05-21 per senior-dev review) ────
    # Morning ensemble members are stale by afternoon. The bot just lost 4/4
    # paper trades on 5/21 because the model bet NO on brackets the market
    # had already converged onto (London 24°C, Madrid 32°C, Amsterdam 19°C,
    # Milan 27°C — all 99%+ YES by trade time).
    #
    # The fix: condition the member distribution on what we've already
    # observed. The final daily high CANNOT be less than the current
    # running_max — that temperature has already occurred. So we filter out
    # all ensemble members below running_max as physically impossible
    # conditional on observation, then count probabilities in the
    # conditional distribution.
    #
    # Effects per bracket:
    #   • bracket above running_max: prob depends on how many filtered
    #     members reach that range (a real forecast question).
    #   • bracket containing running_max: prob_yes large (current temp is
    #     already in this bracket; bracket wins unless temp climbs higher).
    #   • bracket below running_max: prob_yes = 0 (temp already past it).
    #
    # Forecast-failure case: if every member is below running_max (the
    # forecast was completely off), we SKIP the city — we have no reliable
    # distribution to bet against. This is the safety the senior dev asked
    # for.
    running_max_obs: float | None = None
    try:
        tr = (sb.table("temp_readings")
              .select("running_max_c, observed_at")
              .eq("city", city).eq("reading_date", forecast_date)
              .limit(1).execute())
        if tr.data and tr.data[0].get("running_max_c") is not None:
            running_max_obs = float(tr.data[0]["running_max_c"])
    except Exception as e:
        log.warning(f"  [NO Sweep] {city}: temp_readings fetch failed — {e}")

    # Helper for honest display: Wunderground publishes whole-°F (or whole-°C
    # for non-US cities) values. Showing fractional °C in logs is fake
    # precision created by our internal F→C conversion.
    def _native_temp(c_value: float) -> str:
        u = CITY_UNITS.get(city, "C")
        if u == "F":
            return f"{round(c_value * 9 / 5 + 32):d}°F"
        return f"{round(c_value):d}°C"

    # Historical note (2026-05-22): an INTRADAY_SAFETY_BUFFER_C was briefly
    # introduced to handle a 1°C-too-high reading drift, then removed once
    # the root cause was identified and fixed in wunderground.py (switched
    # running_max source from gridded calendarDayTemperatureMax to station
    # hourly-obs max). The buffer is no longer needed.

    if running_max_obs is not None:
        filtered_members = [m for m in members_c if m >= running_max_obs]
        if len(filtered_members) == 0:
            log.warning(
                f"  [NO Sweep] {city}: FORECAST FAILURE — running_max={_native_temp(running_max_obs)} "
                f"exceeds every ensemble member (max member={_native_temp(max(members_c))}). "
                f"No reliable distribution; skipping."
            )
            return []
        if len(filtered_members) < 8:
            log.warning(
                f"  [NO Sweep] {city}: only {len(filtered_members)}/{len(members_c)} members "
                f"survive intraday filter (running_max={_native_temp(running_max_obs)}); "
                f"distribution too sparse — skipping."
            )
            return []
        members_for_prob = filtered_members
        log.info(
            f"  [NO Sweep] {city}: intraday-conditioned — running_max={_native_temp(running_max_obs)}, "
            f"using {len(filtered_members)}/{len(members_c)} ensemble members"
        )
    else:
        # No observation yet (early in city's local day) — use morning forecast as-is.
        members_for_prob = members_c
        log.info(f"  [NO Sweep] {city}: no running_max observed yet, using full ensemble")

    # Helper: bucket bounds are stored in NATIVE unit (°F for US cities, °C
    # for the rest). Members are in °C. Convert bounds to °C for counting.
    unit = CITY_UNITS.get(city, "C")
    def _bounds_c(b: dict) -> tuple[float, float]:
        low_n  = float(b.get("low",  -9999.0))
        high_n = float(b.get("high",  9999.0))
        if b.get("unit", unit) == "F":
            return ((low_n  - 32.0) * 5.0 / 9.0,
                    (high_n - 32.0) * 5.0 / 9.0)
        return (low_n, high_n)

    n_mem = len(members_for_prob)

    def _prob_yes(b: dict) -> float:
        lo, hi = _bounds_c(b)
        return sum(1 for m in members_for_prob if lo <= m <= hi) / n_mem

    # ── fetch event markets ─────────────────────────────────────────────────
    markets, event_slug = _fetch_event_markets(city, forecast_date)
    if not markets:
        log.warning(f"  [NO Sweep] {city}: no event markets — skip")
        return []

    # ── load gate parameters from system_config ─────────────────────────────
    try:
        _min_edge = float((sb.table("system_config").select("value")
                           .eq("key","phase2_min_edge").maybe_single().execute()).data["value"])
    except Exception:
        _min_edge = 0.08
    try:
        _min_prob = float((sb.table("system_config").select("value")
                           .eq("key","phase2_min_model_prob_gate").maybe_single().execute()).data["value"])
    except Exception:
        _min_prob = 0.55

    # ── sizing parameters from sizing_schedule ──────────────────────────────
    try:
        bankroll_for_sizing = float((sb.table("system_config").select("value")
                                     .eq("key","bankroll_usd").maybe_single().execute()).data["value"])
    except Exception:
        bankroll_for_sizing = DEFAULT_BANKROLL_USD
    max_brackets = no_sweep_max_brackets_per_city()

    # ── score every bucket by edge_no, and log every evaluation ─────────────
    # bracket_evaluations is the full-universe log (every bracket we looked
    # at this cycle, gate-passed or not).  Built for the senior-dev-requested
    # selection-bias analysis. Schema: scripts/migrate_bracket_evaluations.sql
    candidates: list[dict] = []
    eval_rows: list[dict] = []   # batched insert at end
    for bucket in buckets:
        label = bucket.get("label", "")
        if not label:
            continue
        # Per-bracket dedup: skip brackets we already traded today (preserves
        # the property that the same NO bet never fires twice while still
        # letting new brackets be added through the day).
        if label in already_traded_brackets:
            continue
        bracket_clean = label.strip().lower()
        match = None
        for mkt in markets:
            if _bracket_matches_question(bracket_clean, mkt.get("question", "").lower()):
                prices = _parse_prices(mkt.get("outcomePrices"))
                yp = prices[0] if prices else None
                if yp is not None:
                    match = (yp, mkt)
                    break
        prob_yes = _prob_yes(bucket)
        prob_no  = 1.0 - prob_yes
        bounds_c = _bounds_c(bucket)
        # Note: an INTRADAY_SAFETY_BUFFER_C "too close to call" skip was
        # added then removed on 2026-05-22. With the precision fix in
        # wunderground.py (switching running_max source from gridded
        # calendarDayTemperatureMax to station-level hourly observations),
        # the reading now matches WU's resolution to ~0.1°C, so the buffer
        # is no longer needed — and was costing us wins on brackets the
        # bot correctly identified as just-dead.
        if match is None:
            # Log evaluation even when no market match (e.g. closed bracket)
            eval_rows.append({
                "cycle":           "phase2_sweep",
                "city":            city,
                "forecast_date":   forecast_date,
                "bracket_label":   label,
                "bracket_low_c":   round(bounds_c[0], 3) if bounds_c[0] > -8000 else None,
                "bracket_high_c":  round(bounds_c[1], 3) if bounds_c[1] <  8000 else None,
                "model_prob_yes":  round(prob_yes, 4),
                "model_prob_no":   round(prob_no, 4),
                "pass_min_prob":   prob_no >= _min_prob,
                "pass_edge":       None,
                "gate_passed":     False,
                "size_usd":        0.0,
                "guardrail_block": "no_market",
            })
            continue
        yes_price = match[0]
        no_price  = round(1 - yes_price, 4)
        edge_no   = prob_no - no_price
        pass_min  = prob_no >= _min_prob
        pass_edge = edge_no >= _min_edge
        gate_pass = pass_min and pass_edge
        eval_row = {
            "cycle":           "phase2_sweep",
            "city":            city,
            "forecast_date":   forecast_date,
            "condition_id":    match[1].get("conditionId", ""),
            "market_id":       match[1].get("conditionId", ""),
            "bracket_label":   label,
            "bracket_low_c":   round(bounds_c[0], 3) if bounds_c[0] > -8000 else None,
            "bracket_high_c":  round(bounds_c[1], 3) if bounds_c[1] <  8000 else None,
            "yes_price":       round(yes_price, 4),
            "no_price":        no_price,
            "model_prob_yes":  round(prob_yes, 4),
            "model_prob_no":   round(prob_no, 4),
            "edge_yes":        round(prob_yes - yes_price, 4),
            "edge_no":         round(edge_no, 4),
            "pass_min_prob":   pass_min,
            "pass_edge":       pass_edge,
            "gate_passed":     gate_pass,
            "size_usd":        0.0,
            "guardrail_block": decision.guardrail if paper_only else None,
        }
        eval_rows.append(eval_row)
        if not gate_pass:
            continue
        candidates.append({
            "bucket":    bucket,
            "label":     label,
            "mkt":       match[1],
            "yes_price": yes_price,
            "no_price":  no_price,
            "prob_yes":  prob_yes,
            "prob_no":   prob_no,
            "edge_no":   edge_no,
            "_eval_row": eval_row,   # for ranked_position fill-in below
        })

    if not candidates:
        log.info(f"  [NO Sweep] {city}: 0 brackets cleared edge gate "
                 f"(min_edge={_min_edge*100:.0f}pp, min_prob_no={_min_prob:.2f}; "
                 f"n_buckets={len(buckets)}, members={n_mem})")
        # Still flush the eval log so we have universe coverage.
        if eval_rows and not dry_run:
            try:
                sb.table("bracket_evaluations").insert(eval_rows).execute()
            except Exception as _le:
                log.debug(f"  [NO Sweep] bracket_evaluations insert failed: {_le}")
        return []

    # ── pick top N by edge, place trades ────────────────────────────────────
    candidates.sort(key=lambda x: x["edge_no"], reverse=True)
    chosen   = candidates[:max_brackets]
    # Annotate the eval rows with their ranked position so the analysis
    # downstream can distinguish "gate-pass but not selected because N<top"
    # from "gate-pass and selected".
    for rank, c in enumerate(chosen, start=1):
        c["_eval_row"]["ranked_position"] = rank
        c["_eval_row"]["side_selected"]   = "NO"
    results  = []
    total    = 0.0
    for c in chosen:
        label        = c["label"]
        mkt          = c["mkt"]
        yes_price    = c["yes_price"]
        no_price     = c["no_price"]
        prob_no      = c["prob_no"]
        edge_no      = c["edge_no"]
        # Paper-trade mode forces $0.01 observation; real-money sizing only
        # runs when every guardrail clears.
        if paper_only:
            size = 0.01
        else:
            size = round(size_for_no_sweep(prob_no, no_price, bankroll_for_sizing), 2)
        condition_id = mkt.get("conditionId", "")

        log.info(
            f"  [NO Sweep] {'📝 paper' if paper_only else '✓ live'} {city} [{label}]  "
            f"NO @ {no_price*100:.1f}¢ (model={prob_no:.2f}, edge=+{edge_no*100:.1f}pp) → ${size:.2f}"
        )

        if not dry_run:
            signal = {
                "city":                  city,
                "forecast_date":         forecast_date,
                "market_id":             condition_id,
                "condition_id":          condition_id,
                "outcome":               label,
                "side":                  "NO",
                "market_price":          no_price,
                "model_probability":     round(prob_no, 4),
                "corrected_probability": round(prob_no, 4),
                "edge":                  round(edge_no, 4),
                "delta_mean":            0.0,
                "delta_std":             0.0,
                "confidence":            round(prob_no, 4),
                "recommended_position":  size,
                "mean_high":             running_max_c,
                "std_high":              0.0,
                "signal_time":           datetime.now(timezone.utc).isoformat(),
                "traded":                False,
                "market_question":       mkt.get("question", ""),
                "event_slug":            event_slug,
                "signal_phase":          "phase2_sweep",
                "rung_type":             "no_sweep",
                "distance_sigma":        0.0,
            }
            res       = sb.table("trade_signals").insert(signal).execute()
            signal_id = res.data[0]["id"] if res.data else None
            # Wire the trade_signals row id back into the eval row so the
            # full-universe log can be joined to actual fills/PnL later.
            c["_eval_row"]["signal_id"] = signal_id
            c["_eval_row"]["size_usd"]  = size

            if not paper_only:
                try:
                    from executor import place_order
                    place_order(
                        condition_id=condition_id,
                        side="NO",
                        signal_price=no_price,
                        size_usd=size,
                        signal_id=signal_id,
                        phase="phase2_sweep",
                    )
                except Exception as e:
                    log.warning(f"  [NO Sweep] executor error for {city} [{label}]: {e}")

        total += size
        results.append({"bracket": label, "size": size, "no_price": no_price, "prob_no": prob_no, "edge_no": edge_no, "paper_only": paper_only})

    log.info(f"  [NO Sweep] {city}: {len(results)} trade(s) — ${total:.2f} total "
             f"(picked top {len(chosen)} of {len(candidates)} candidates)")
    # Flush all the eval rows (chosen + not chosen + no-market + below-gate)
    if eval_rows and not dry_run:
        try:
            sb.table("bracket_evaluations").insert(eval_rows).execute()
            log.debug(f"  [NO Sweep] logged {len(eval_rows)} bracket_evaluations rows")
        except Exception as _le:
            log.warning(f"  [NO Sweep] bracket_evaluations insert failed: {_le}")
    return results


def already_traded_phase2_today(city: str, forecast_date: str) -> bool:
    """
    DEPRECATED 2026-05-21. Day-level YES-lock block was overly conservative —
    if temp climbs past the original locked bracket, the original YES is dead
    and a new YES lock on the new bracket can recover the loss.

    Replaced with brackets_yes_locked_today() for per-bracket dedup.
    Returns False so any legacy caller doesn't accidentally still gate here.
    """
    return False


def brackets_yes_locked_today(city: str, forecast_date: str) -> set[str]:
    """
    Return the SET of bracket labels we've already placed real-money YES
    locks on for this city today. Per-bracket dedup — we never re-lock the
    SAME bracket, but allow a new lock on a DIFFERENT bracket (e.g., when
    temp climbed past the old one).
    """
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    try:
        res = (sb.table("trade_signals")
               .select("outcome, order_status")
               .eq("city", city)
               .eq("forecast_date", forecast_date)
               .eq("signal_phase", "phase2")
               .eq("side", "YES")
               .gt("recommended_position", 1)   # real-money only — observations don't count
               .gte("created_at", today_start)
               .execute())
    except Exception as e:
        log.warning(f"  [Phase 2] brackets_yes_locked_today query failed for {city}: {e}")
        return set()
    return {
        r["outcome"]
        for r in (res.data or [])
        if r.get("outcome") and r.get("order_status") != "failed"
    }


def already_observed_today(city: str, forecast_date: str) -> bool:
    """Return True if we already wrote a $0.01 observation signal for this city today."""
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    res = (sb.table("trade_signals")
           .select("id")
           .eq("city", city)
           .eq("forecast_date", forecast_date)
           .eq("signal_phase", "phase2")
           .lte("recommended_position", 1)   # observation only
           .gte("created_at", today_start)
           .limit(1)
           .execute())
    return bool(res.data)


def mark_phase2_triggered(city: str, today: str) -> None:
    """Mark the temp_readings row as phase2_triggered so monitor won't re-fire."""
    sb.table("temp_readings").update({"phase2_triggered": True}).eq("city", city).eq("reading_date", today).execute()


def get_phase1_model_prob(city: str, forecast_date: str, bracket: str) -> float | None:
    """
    Look up the Phase 1 morning forecast's model_probability for the locked bracket.

    Returns the model_probability if a matching Phase 1 signal exists, else None.
    None means we have no morning forecast for this bracket — caller decides policy.

    This is the corrected probability that the morning model assigned to this
    temperature bracket, incorporating ensemble members and delta calibration.
    It is stored in trade_signals.model_probability for phase1 rows.
    """
    try:
        res = (
            sb.table("trade_signals")
            .select("model_probability, edge")
            .eq("city", city)
            .eq("forecast_date", forecast_date)
            .eq("signal_phase", "phase1")
            .eq("outcome", bracket)
            .limit(1)
            .execute()
        )
        if res.data:
            prob = res.data[0].get("model_probability")
            return float(prob) if prob is not None else None
    except Exception as e:
        log.warning(f"  Phase 1 model_prob lookup failed for {city}/{bracket}: {e}")
    return None


def execute_phase2(
    city: str,
    forecast_date: str,
    locked_bracket: str,
    running_max_c: float,
    confidence: float,
    dry_run: bool = False,
) -> dict:
    """
    Main Phase 2 entry point. Called by temp_monitor when a bracket is locked.
    Returns result dict.
    """
    today = date.today().isoformat()
    log.info(f"\n  [Phase 2] {city} | bracket={locked_bracket} | max={running_max_c:.1f}°C | conf={confidence:.2f}")

    # Per-bracket dedup (2026-05-21): allow multiple YES locks per day on
    # DIFFERENT brackets, but never re-lock the SAME bracket. If temp climbed
    # past the original lock, a new lock on the new bracket can recover.
    locked_brackets = brackets_yes_locked_today(city, forecast_date)
    if locked_bracket in locked_brackets:
        log.info(f"  [Phase 2] {city}: bracket '{locked_bracket}' already locked today — skip")
        return {"city": city, "status": "already_locked_this_bracket"}
    if locked_brackets:
        log.info(f"  [Phase 2] {city}: {len(locked_brackets)} other bracket(s) already locked today, allowing new lock on '{locked_bracket}'")

    # Check budget
    _, _, phase2_budget = get_daily_budget()
    deployed_today, breakdown = get_today_deployed_phase2()
    remaining = phase2_budget - deployed_today

    if remaining < 1.00:
        breakdown_str = ", ".join(f"{b['city']} ${b['size']:.2f}" for b in breakdown) or "none"
        log.info(
            f"  [Phase 2] {city}: Phase 2 budget exhausted "
            f"(${deployed_today:.2f} / ${phase2_budget:.2f} deployed — {breakdown_str})"
        )
        return {"city": city, "status": "budget_exhausted"}

    # ── Morning model probability gate ───────────────────────────────────────
    # Check what the Phase 1 morning model said about this bracket. If the model
    # gave it low probability in the morning, our METAR reading is likely biased
    # vs. the resolution station — skip the trade.
    # PHASE2_MIN_MODEL_PROB = 0.0 disables this gate entirely.
    phase1_model_prob = get_phase1_model_prob(city, forecast_date, locked_bracket)
    if phase1_model_prob is not None and PHASE2_MIN_MODEL_PROB > 0:
        if phase1_model_prob < PHASE2_MIN_MODEL_PROB:
            log.info(
                f"  [Phase 2] {city}: Phase 1 model_probability {phase1_model_prob:.3f} "
                f"< {PHASE2_MIN_MODEL_PROB} for bracket '{locked_bracket}' — "
                f"morning model didn't favor this bracket, skipping"
            )
            mark_phase2_triggered(city, today)
            return {"city": city, "status": "low_model_prob", "phase1_prob": phase1_model_prob}
        log.info(
            f"  [Phase 2] {city}: Phase 1 model_probability={phase1_model_prob:.3f} ✓ "
            f"(>= {PHASE2_MIN_MODEL_PROB})"
        )
    elif phase1_model_prob is None:
        log.info(
            f"  [Phase 2] {city}: no Phase 1 signal found for bracket '{locked_bracket}' "
            f"— proceeding without morning model gate"
        )

    # ── Fetch delta calibration data (needed for sizing before DB write) ────────
    # Use the same hierarchical Bayesian estimator as temp_monitor so YES
    # bracket lookup and NO sweep apply identical effective deltas.
    raw_delta_c = 0.0
    delta_samples = 0
    try:
        rs = (sb.table("resolution_stations")
              .select("delta_c,delta_samples")
              .eq("city", city)
              .limit(1)
              .execute())
        if rs.data:
            raw_delta_c   = float(rs.data[0].get("delta_c") or 0.0)
            delta_samples = int(rs.data[0].get("delta_samples") or 0)
    except Exception:
        pass

    try:
        from temp_monitor import _get_city_delta
        delta_c = _get_city_delta(city)
    except Exception:
        delta_c = 0.0

    # Find the Polymarket market for this bracket
    market = find_market_for_bracket(city, forecast_date, locked_bracket)
    if not market:
        return {"city": city, "status": "no_market"}

    yes_price = market["yes_price"]

    # Market already fully resolved (price = 0 or 1) — nothing to trade.
    # Mark as triggered so the monitor stops retrying every 5 minutes.
    if yes_price >= 0.995:
        log.info(
            f"  [Phase 2] {city}: bracket '{locked_bracket}' already fully resolved "
            f"@ {yes_price*100:.1f}¢ — market closed"
        )
        mark_phase2_triggered(city, today)
        return {"city": city, "status": "already_resolved", "yes_price": yes_price}
    if yes_price <= 0.005:
        log.info(
            f"  [Phase 2] {city}: bracket '{locked_bracket}' resolved NO "
            f"@ {yes_price*100:.1f}¢ — wrong bracket, skipping"
        )
        mark_phase2_triggered(city, today)
        return {"city": city, "status": "resolved_no", "yes_price": yes_price}

    # Dynamic price ceiling: buy up to min(confidence, 0.98).
    # At conf=0.98 we'll buy up to 98¢; at conf=0.70 only up to 70¢.
    # Mark as triggered — once the market is above our cap it won't come back down.
    dynamic_max = round(min(confidence, 0.98), 4)
    if yes_price >= dynamic_max:
        log.info(
            f"  [Phase 2] {city}: YES price {yes_price*100:.1f}¢ >= "
            f"dynamic cap {dynamic_max*100:.1f}¢ (conf={confidence:.2f}) — market already moved"
        )
        mark_phase2_triggered(city, today)
        return {"city": city, "status": "price_too_high", "yes_price": yes_price}

    # Minimum price floor: if market prices this below 5¢ it almost certainly
    # means we matched the wrong bracket (resolution station reads a different temp).
    # Mark as triggered — wrong bracket won't self-correct within the same day.
    MIN_YES_PRICE = 0.05
    if yes_price < MIN_YES_PRICE:
        log.info(
            f"  [Phase 2] {city}: YES price {yes_price*100:.2f}¢ below floor "
            f"{MIN_YES_PRICE*100:.0f}¢ — likely bracket mismatch, skipping"
        )
        mark_phase2_triggered(city, today)
        return {"city": city, "status": "bracket_mismatch", "yes_price": yes_price}

    # ── Decision pipeline (2026-05-19 redesign) ─────────────────────────────
    # Replaces the old price-cap rule (PHASE2_MAX_CALIBRATED_PRICE) with:
    #   1. Guardrails (phase2_paused, bankroll floor, daily loss, 3-day win rate)
    #   2. YES-locks-enabled flag (default off — week 1 NO-only)
    #   3. Calibration gate (still need delta_samples ≥ N before risking money)
    #   4. Edge gate: confidence (proxy for model prob) - yes_price ≥ 8pp
    #   5. Min model prob: confidence ≥ 0.55
    #   6. Sizing comes from sizing_schedule table (week 1 = flat $3 YES)
    # Each failed gate produces a $0.01 observation row so the calibration
    # pipeline keeps running.
    is_calibrated = delta_samples >= PHASE2_CALIBRATION_MIN_SAMPLES

    # 1. Guardrails
    decision = check_trade_allowed()
    if not decision.allowed:
        log.warning(f"  [Phase 2] 🚧 {city}: guardrail [{decision.guardrail}] blocks: {decision.reason} — observation only")
        size = 0.01
    else:
        # 2. YES-locks-enabled flag
        try:
            _yle = (sb.table("system_config").select("value")
                    .eq("key", "phase2_yes_locks_enabled").maybe_single().execute())
            yes_locks_enabled = bool(_yle.data and str(_yle.data.get("value")) == "1")
        except Exception:
            yes_locks_enabled = False
        if not yes_locks_enabled:
            log.info(f"  [Phase 2] {city}: phase2_yes_locks_enabled=0 — observation only (YES side disabled this phase)")
            size = 0.01
        elif not is_calibrated:
            # 3. Calibration gate
            log.info(
                f"  [Phase 2] {city}: UNCALIBRATED observation "
                f"(n={delta_samples} < {PHASE2_CALIBRATION_MIN_SAMPLES})"
            )
            size = 0.01
        else:
            # 4 + 5. Edge gate + min model prob.
            # We use `confidence` as the model-prob proxy: it's the
            # lock-confidence score (stability + plateau + sky cond + trend).
            # The existing PHASE2_MIN_CONFIDENCE=0.80 gate is upstream of
            # here, so the 0.55 floor is essentially a guard against future
            # confidence-floor lowering, not the binding constraint today.
            try:
                _min_edge = float((sb.table("system_config").select("value")
                                   .eq("key","phase2_min_edge").maybe_single().execute()).data["value"])
            except Exception:
                _min_edge = 0.08
            try:
                _min_prob = float((sb.table("system_config").select("value")
                                   .eq("key","phase2_min_model_prob_gate").maybe_single().execute()).data["value"])
            except Exception:
                _min_prob = 0.55

            edge = confidence - yes_price
            if confidence < _min_prob:
                log.info(
                    f"  [Phase 2] {city}: model_prob (conf={confidence:.2f}) "
                    f"below floor {_min_prob:.2f} — observation only"
                )
                size = 0.01
            elif edge < _min_edge:
                log.info(
                    f"  [Phase 2] {city}: edge={edge*100:.1f}pp "
                    f"< floor {_min_edge*100:.1f}pp (conf={confidence:.2f}, YES={yes_price*100:.1f}¢) — observation only"
                )
                size = 0.01
            else:
                # 6. Sizing from the schedule.
                try:
                    bankroll_for_sizing = float((sb.table("system_config").select("value")
                                                 .eq("key","bankroll_usd").maybe_single().execute()).data["value"])
                except Exception:
                    bankroll_for_sizing = DEFAULT_BANKROLL_USD
                size = round(size_for_yes_lock(
                    model_prob=confidence,
                    market_price=yes_price,
                    bankroll=bankroll_for_sizing,
                ), 2)
                log.info(
                    f"  [Phase 2] {city}: CALIBRATED+EDGE-GATED trade "
                    f"(n={delta_samples}, conf={confidence:.2f}, YES={yes_price*100:.1f}¢, "
                    f"edge={edge*100:.1f}pp) → ${size:.2f}"
                )

    size = round(min(size, remaining), 2)

    # If this would be an observation trade and we already observed today, skip silently
    # (we'll re-evaluate next cycle in case prices change and become real-money eligible).
    if size <= 1 and already_observed_today(city, forecast_date):
        log.info(f"  [Phase 2] {city}: already observed today; skipping duplicate observation")
        return {"city": city, "status": "already_observed"}

    potential_payout = round(size * (1.0 / yes_price - 1.0), 2)
    log.info(
        f"  [Phase 2] ✅ {city} [{locked_bracket}] YES @ {yes_price*100:.1f}¢ "
        f"| size=${size:.2f} | potential payout=${potential_payout:.2f} "
        f"| conf={confidence:.2f} | remaining budget=${remaining:.2f}"
    )

    # ── Bracket blacklist gate (DISABLED 2026-05-19 per operator request) ──
    # The bracket_blacklist gate previously consulted Weatherstappen's NO
    # positions to suppress YES locks on contested brackets. Removed because
    # the trader-tracking strategy was deemed not aligned with the new edge-
    # gate-based decision rule. The bracket_blacklist table and
    # sync_bracket_blacklist.py cron are kept for now (two-week soft-delete
    # window); if not re-enabled by 2026-06-02, delete both.
    pass

    if dry_run:
        log.info(f"  [Phase 2] DRY RUN — no DB write")
        return {"city": city, "status": "dry_run", "size": size, "yes_price": yes_price}

    # Write to trade_signals — must satisfy all NOT NULL columns
    # model_probability = Phase 1 morning forecast probability for this bracket
    #   (the model's actual win-probability estimate, not lock certainty).
    #   Falls back to lock confidence if no Phase 1 signal exists.
    # confidence        = bracket-lock certainty (time + stability, 0.80-0.97)
    true_model_prob = phase1_model_prob if phase1_model_prob is not None else confidence
    signal = {
        "city":                  city,
        "forecast_date":         forecast_date,
        "market_id":             market.get("condition_id", ""),
        "condition_id":          market["condition_id"],
        "outcome":               locked_bracket,
        "side":                  "YES",
        "market_price":          yes_price,
        "model_probability":     true_model_prob,     # Phase 1 morning model probability
        "corrected_probability": true_model_prob,
        "edge":                  round(true_model_prob - yes_price, 4),
        "delta_mean":            0.0,
        "delta_std":             0.0,
        "confidence":            confidence,           # bracket-lock certainty (time+stability)
        "recommended_position":  size,
        "mean_high":             running_max_c,
        "std_high":              0.0,
        "signal_time":           datetime.now(timezone.utc).isoformat(),
        "traded":                False,
        "market_question":       market.get("question", ""),
        "event_slug":            market.get("event_slug", ""),
        "signal_phase":          "phase2",
        "rung_type":             "phase2",
        "distance_sigma":        0.0,
    }
    res = sb.table("trade_signals").insert(signal).execute()
    signal_id = res.data[0]["id"] if res.data else None

    # Place the order (paper or live depending on LIVE_TRADING in config)
    try:
        from executor import place_order
        place_order(
            condition_id=market["condition_id"],
            side="YES",
            signal_price=yes_price,
            size_usd=size,
            signal_id=signal_id,
            phase="phase2",
        )
    except Exception as e:
        log.warning(f"  [Phase 2] executor.place_order error for {city}: {e}")

    # Mark temp_readings as triggered ONLY for real-money trades.
    # Observation ($0.01) trades let the monitor keep watching so the city
    # can re-evaluate for a real trade if the bracket price drops later in the day.
    if size > 1:
        mark_phase2_triggered(city, today)

    # ── NO sweep: buy NO on brackets physically below confirmed running_max ──
    # Runs immediately after the main YES trade so the event markets are cached.
    # delta_c was already fetched above for the sizing decision — reuse it here.
    sweep_results = _execute_no_sweep(
        city=city,
        forecast_date=forecast_date,
        running_max_c=running_max_c,
        delta_c=delta_c,
        dry_run=dry_run,
    )

    return {
        "city":         city,
        "status":       "executed",
        "size":         size,
        "yes_price":    yes_price,
        "bracket":      locked_bracket,
        "payout_if_win": potential_payout,
        "no_sweep":     len(sweep_results),
    }


# ── End-of-day bankroll reconciliation ───────────────────────────────────────

def reconcile_bankroll() -> float:
    """
    Compute bankroll. Two modes:

    LIVE mode (live_start_date in system_config):
      bankroll = live_starting_bankroll + sum(pnl_usd for FILLED trades since live_start_date)
      Paper/observation trades are excluded — they continue feeding calibration but
      do not affect real-money tracking.

    PAPER mode (no live_start_date):
      bankroll = DEFAULT_BANKROLL_USD + sum(pnl_usd for all resolved trades)
      Legacy behavior for paper trading.

    Idempotent: safe to run multiple times per day without double-counting.
    """
    from config import MIN_BANKROLL_USD, DEFAULT_BANKROLL_USD
    today = date.today().isoformat()

    # ── Pause guard ──────────────────────────────────────────────────────────
    # Set system_config.bankroll_reconcile_paused='1' when the on-platform cash
    # balance is in an inconsistent state that the bot cannot infer from
    # trade_signals — e.g. Polymarket's 2026-05-18 mass-archival of weather
    # markets, where ~$430 of filled buys vanished into a refund queue without
    # producing a winning_bracket. While paused, the reconcile is a no-op:
    # bankroll_usd stays at whatever was manually set, and no snapshot row is
    # written for today. Unset the flag once the actual cash balance can be
    # explained by (live_starting_bankroll + cumulative resolved P&L) again.
    try:
        rp = sb.table("system_config").select("value").eq("key", "bankroll_reconcile_paused").single().execute()
        if rp.data and str(rp.data.get("value")) == "1":
            current = get_bankroll()
            log.info(
                f"  reconcile_bankroll: PAUSED via system_config.bankroll_reconcile_paused=1 "
                f"(bankroll_usd left at ${current:.2f}, no snapshot written for {today})"
            )
            return current
    except Exception:
        pass

    # Check for live mode markers
    live_start_date = None
    live_starting_bankroll = None
    try:
        ls_res = sb.table("system_config").select("value").eq("key", "live_start_date").single().execute()
        if ls_res.data and ls_res.data.get("value"):
            live_start_date = ls_res.data["value"]
        lb_res = sb.table("system_config").select("value").eq("key", "live_starting_bankroll").single().execute()
        if lb_res.data and lb_res.data.get("value"):
            live_starting_bankroll = float(lb_res.data["value"])
    except Exception:
        pass

    if live_start_date and live_starting_bankroll is not None:
        # LIVE mode: only count filled real-money trades since live_start_date.
        # Explicit .limit(50_000) prevents Supabase's silent 1000-row default
        # cap from corrupting the cumulative P&L sum once the live history
        # exceeds 1000 trades.  50k handles ~25 years of trading at our pace.
        live_res = (sb.table("trade_signals")
                    .select("pnl_usd, order_status, forecast_date")
                    .not_.is_("pnl_usd", "null")
                    .gte("forecast_date", live_start_date)
                    .in_("order_status", ["filled"])
                    .not_.eq("winning_bracket", "VOIDED")
                    .limit(50_000)
                    .execute())
        cumulative_pnl = sum(float(r["pnl_usd"]) for r in live_res.data)
        absolute_bankroll = round(live_starting_bankroll + cumulative_pnl, 2)
        log.info(
            f"  LIVE mode: bankroll = ${live_starting_bankroll:.2f} starting + "
            f"${cumulative_pnl:+.2f} live P&L ({len(live_res.data)} filled trades since {live_start_date})"
        )
    else:
        # PAPER mode (legacy).  Same explicit .limit(50_000) as the LIVE
        # branch — defends against Supabase's silent 1000-row reply cap.
        all_res = (sb.table("trade_signals")
                   .select("pnl_usd")
                   .not_.is_("pnl_usd", "null")
                   .not_.eq("winning_bracket", "VOIDED")
                   .limit(50_000)
                   .execute())
        cumulative_pnl = sum(float(r["pnl_usd"]) for r in all_res.data)
        absolute_bankroll = round(DEFAULT_BANKROLL_USD + cumulative_pnl, 2)

    # Today's P&L for the snapshot (resolved_at within last 36h)
    from datetime import timedelta
    window_start = (datetime.now(timezone.utc) - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    today_res = (sb.table("trade_signals")
                 .select("pnl_usd")
                 .gte("resolved_at", window_start)
                 .not_.is_("pnl_usd", "null")
                 .not_.eq("winning_bracket", "VOIDED")
                 .execute())
    today_pnl = sum(float(r["pnl_usd"]) for r in today_res.data)

    current = get_bankroll()
    set_config("bankroll_usd", absolute_bankroll)
    log.info(f"  Bankroll updated: ${current:.2f} → ${absolute_bankroll:.2f} (cumulative P&L: ${cumulative_pnl:+.2f})")

    # Write daily snapshot (delete-then-insert avoids needing a unique constraint)
    from config import LIVE_TRADING
    try:
        sb.table("bankroll_snapshots").delete().eq("snapshot_date", today).execute()
        sb.table("bankroll_snapshots").insert({
            "snapshot_date":    today,
            "total_value":      absolute_bankroll,
            "cash":             absolute_bankroll,
            "daily_pnl":        round(today_pnl, 4),
            "active_positions": 0,
            "is_paper":         not LIVE_TRADING,
        }).execute()
        log.info(f"  Bankroll snapshot written for {today}: ${absolute_bankroll:.2f}")
    except Exception as e:
        log.warning(f"  bankroll_snapshots write failed: {e}")

    new_bankroll = absolute_bankroll

    # Safety floor check (MIN_BANKROLL_USD already imported above)
    if new_bankroll < MIN_BANKROLL_USD:
        log.warning(
            f"  ⚠️  Bankroll ${new_bankroll:.2f} below floor ${MIN_BANKROLL_USD:.2f} "
            f"— consider pausing trading"
        )

    return new_bankroll


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s UTC | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Phase 2 bracket confirmation engine")
    sub = parser.add_subparsers(dest="cmd")

    # execute subcommand
    ex = sub.add_parser("execute", help="Execute a Phase 2 trade for a locked bracket")
    ex.add_argument("--city",       required=True)
    ex.add_argument("--date",       default=date.today().isoformat())
    ex.add_argument("--bracket",    required=True, help="Locked bracket label e.g. '24°C'")
    ex.add_argument("--max-c",      type=float, required=True, help="Running max temp in °C")
    ex.add_argument("--confidence", type=float, default=0.80)
    ex.add_argument("--dry-run",    action="store_true")

    # reconcile subcommand
    sub.add_parser("reconcile", help="Update bankroll from today's resolved P&L")

    # status subcommand
    sub.add_parser("status", help="Show current bankroll and daily budget")

    args = parser.parse_args()

    if args.cmd == "execute":
        result = execute_phase2(
            city=args.city, forecast_date=args.date,
            locked_bracket=args.bracket, running_max_c=args.max_c,
            confidence=args.confidence, dry_run=args.dry_run,
        )
        print(result)

    elif args.cmd == "reconcile":
        new_br = reconcile_bankroll()
        print(f"Bankroll updated to ${new_br:.2f}")

    elif args.cmd == "status":
        bankroll = get_bankroll()
        total, p1, p2 = get_daily_budget()
        deployed_p2, breakdown = get_today_deployed_phase2()
        print(f"\nBankroll:          ${bankroll:.2f}")
        print(f"Daily cap (10%):   ${total:.2f}")
        print(f"  Phase 1 budget:  ${p1:.2f}")
        print(f"  Phase 2 budget:  ${p2:.2f}")
        print(f"Phase 2 deployed:  ${deployed_p2:.2f}")
        print(f"Phase 2 remaining: ${p2 - deployed_p2:.2f}")
        if breakdown:
            print("\nPhase 2 breakdown:")
            for b in breakdown:
                print(f"  {b['city']:<20} ${b['size']:.2f}")

    else:
        parser.print_help()
