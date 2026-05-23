"""
Post-Lock Exit Simulation (Shadow Mode)
=========================================
Runs alongside temp_monitor to detect:
  1. BUST events — running_max crosses the upper boundary of our bet bracket
     (undershoot — temp rose past us). Capture sell + switch opportunity.
  2. LATE_DECAY events — late in the day, running_max remains below our bracket
     (overshoot — delta was too aggressive). Capture exit-only opportunity.

For each event, snapshots:
  - Current YES price on the busted/original bracket (hypothetical sell price)
  - Current YES price on the corrected bracket (hypothetical switch price)

No real trades placed. Data only. Decision on going live happens later based
on accumulated forward results.
"""
import re
import logging
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, CITY_UNITS, CITY_TIMEZONES

log = logging.getLogger(__name__)
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

STAKE = 45.0
LATE_DECAY_LOCAL_HOUR = 19   # only flag overshoot decays after 7 PM local


def _parse_bracket_nums(label: str) -> list[int]:
    """
    Extract integer temperatures from a bracket label like '15°C', '70-71°F', '≥32°C'.
    Handles the hyphen-as-range case correctly (not as negative sign).
    """
    if not label:
        return []
    # Match optional minus only if at start or after non-digit (treats "70-71" as 70 and 71)
    nums = re.findall(r"(?<!\d)-?\d+", label)
    return [int(n) for n in nums]


def _bracket_temp(label: str) -> int | None:
    """Lowest integer in the bracket label."""
    nums = _parse_bracket_nums(label)
    return nums[0] if nums else None


def _bracket_upper_c(bet_bracket: str, unit: str) -> float | None:
    """Return the upper boundary of a bracket in °C."""
    nums = _parse_bracket_nums(bet_bracket)
    if not nums:
        return None
    n = nums[0]
    if "≥" in bet_bracket or ">=" in bet_bracket:
        return 9000.0
    if "≤" in bet_bracket or "<=" in bet_bracket:
        return ((n + 0.5) - 32) * 5 / 9 if unit == "F" else (n + 0.5)
    if len(nums) >= 2:
        hi = nums[1]
        return ((hi + 0.5) - 32) * 5 / 9 if unit == "F" else (hi + 0.5)
    return ((n + 0.5) - 32) * 5 / 9 if unit == "F" else (n + 0.5)


def _fetch_current_yes(city: str, forecast_date: str, target_label: str) -> float | None:
    """Re-use phase2_engine's find_market_for_bracket to get current YES price."""
    try:
        from phase2_engine import find_market_for_bracket
        result = find_market_for_bracket(city, forecast_date, target_label)
        return float(result["yes_price"]) if result else None
    except Exception as e:
        log.debug(f"  [Exit Sim] price fetch failed for {city}/{target_label}: {e}")
        return None


def check_for_exit_events(city: str, forecast_date: str, running_max_c: float) -> None:
    """
    Called by temp_monitor for each city after the per-cycle update.
    Detects bust + late_decay events and records simulation entries.
    """
    # 1. Find today's real-money phase2 signal for this city (if any)
    try:
        sig_res = (sb.table("trade_signals")
                   .select("id, outcome, market_price, recommended_position")
                   .eq("city", city)
                   .eq("forecast_date", forecast_date)
                   .eq("signal_phase", "phase2")
                   .gt("recommended_position", 1)   # real-money only
                   .limit(1)
                   .execute())
        if not sig_res.data:
            return
        signal = sig_res.data[0]
    except Exception:
        return

    signal_id = signal["id"]
    bet_bracket = signal["outcome"]
    lock_price = float(signal["market_price"])
    unit = CITY_UNITS.get(city, "C")

    bet_temp_int = _bracket_temp(bet_bracket)
    upper_c = _bracket_upper_c(bet_bracket, unit)
    if upper_c is None or bet_temp_int is None:
        return

    # 2. BUST detection: running_max exceeded the bracket upper boundary
    bust_detected = running_max_c >= upper_c + 0.05  # small margin to avoid noise

    # Check if we've already recorded a BUST event for this signal
    try:
        existing = (sb.table("exit_simulation")
                    .select("id, detection_type")
                    .eq("signal_id", signal_id)
                    .execute())
        has_bust = any(r["detection_type"] == "bust" for r in (existing.data or []))
        has_decay = any(r["detection_type"] == "late_decay" for r in (existing.data or []))
    except Exception:
        return

    if bust_detected and not has_bust:
        # New bust event — snapshot prices and record
        new_bracket_temp = bet_temp_int + 1
        new_bracket_label = (f"{new_bracket_temp}°{unit}" if unit == "C"
                             else f"{new_bracket_temp}-{new_bracket_temp+1}°F")

        busted_price = _fetch_current_yes(city, forecast_date, bet_bracket)
        new_price = _fetch_current_yes(city, forecast_date, new_bracket_label)

        try:
            sb.table("exit_simulation").insert({
                "signal_id":       signal_id,
                "city":            city,
                "forecast_date":   forecast_date,
                "detection_type":  "bust",
                "bet_bracket":     bet_bracket,
                "bet_lock_price":  lock_price,
                "bet_running_max": round(running_max_c, 2),
                "new_bracket":     new_bracket_label,
                "busted_yes_price": busted_price,
                "new_yes_price":   new_price,
            }).execute()
            log.info(
                f"  [Exit Sim] {city} BUST detected — bet={bet_bracket} max={running_max_c:.1f}°C  "
                f"busted_sell={busted_price*100:.1f}¢ "
                f"new_entry={new_price*100:.1f}¢ "
                if busted_price is not None and new_price is not None
                else f"  [Exit Sim] {city} BUST detected (price snapshot incomplete)"
            )
        except Exception as e:
            log.warning(f"  [Exit Sim] {city} bust insert failed: {e}")
        return

    # 3. LATE_DECAY detection: late in day, running_max below bracket
    tz = ZoneInfo(CITY_TIMEZONES.get(city, "UTC"))
    local_hour = datetime.now(tz).hour
    bet_temp_c = bet_temp_int if unit == "C" else (bet_temp_int - 32) * 5 / 9

    if (local_hour >= LATE_DECAY_LOCAL_HOUR
            and not has_decay
            and running_max_c + 1.0 < bet_temp_c):
        busted_price = _fetch_current_yes(city, forecast_date, bet_bracket)
        try:
            sb.table("exit_simulation").insert({
                "signal_id":       signal_id,
                "city":            city,
                "forecast_date":   forecast_date,
                "detection_type":  "late_decay",
                "bet_bracket":     bet_bracket,
                "bet_lock_price":  lock_price,
                "bet_running_max": round(running_max_c, 2),
                "busted_yes_price": busted_price,
            }).execute()
            log.info(
                f"  [Exit Sim] {city} LATE_DECAY — bet={bet_bracket} max={running_max_c:.1f}°C "
                f"sell_price={busted_price*100:.1f}¢"
                if busted_price is not None
                else f"  [Exit Sim] {city} LATE_DECAY (price unavailable)"
            )
        except Exception as e:
            log.warning(f"  [Exit Sim] {city} late_decay insert failed: {e}")


def resolve_simulations(log_obj: logging.Logger | None = None) -> None:
    """
    Called by resolver after Phase 2 signals resolve. For each unresolved
    exit_simulation row, compute the hypothetical P&L of each strategy.
    """
    _log = log_obj or log
    try:
        unresolved = (sb.table("exit_simulation")
                      .select("*")
                      .is_("actual_winning_bracket", "null")
                      .limit(500)
                      .execute())
    except Exception as e:
        _log.warning(f"  [Exit Sim] resolution query failed: {e}")
        return

    for sim in (unresolved.data or []):
        # Look up the winning bracket from the original signal
        try:
            sig = (sb.table("trade_signals")
                   .select("winning_bracket, pnl_usd, actual_outcome")
                   .eq("id", sim["signal_id"])
                   .single()
                   .execute()).data
            if not sig or not sig.get("winning_bracket"):
                continue   # still unresolved
        except Exception:
            continue

        winning_question = sig["winning_bracket"]
        win_temp = _bracket_temp(winning_question)
        bet_temp = _bracket_temp(sim["bet_bracket"])
        if win_temp is None or bet_temp is None:
            continue

        bet_won = (str(sig.get("actual_outcome", "")).lower() == "true")
        new_bracket_temp = _bracket_temp(sim.get("new_bracket") or "")
        new_won = (new_bracket_temp == win_temp) if new_bracket_temp is not None else False

        bet_lock_price = float(sim["bet_lock_price"]) if sim["bet_lock_price"] else 0.5
        busted_yes = float(sim["busted_yes_price"]) if sim.get("busted_yes_price") else 0.0
        new_yes = float(sim["new_yes_price"]) if sim.get("new_yes_price") else None

        # Original shares = STAKE / bet_lock_price
        shares = STAKE / bet_lock_price if bet_lock_price > 0 else 0

        # === Hypothetical strategies ===
        # HOLD: bet won → STAKE*(1/price - 1); lost → -STAKE
        hold_pnl = float(sig.get("pnl_usd") or 0)

        # SELL ONLY: -STAKE + (shares × busted_yes)
        sell_only_pnl = round(-STAKE + shares * busted_yes, 4)

        # SWITCH FRESH (let original die, buy new with fresh $45)
        if new_yes and new_yes > 0:
            switch_pnl = STAKE * (1 / new_yes - 1) if new_won else -STAKE
            switch_fresh_pnl = round((-STAKE if not bet_won else hold_pnl) + switch_pnl, 4)
        else:
            switch_fresh_pnl = None

        # SELL + SWITCH (proceeds): sell, redeploy proceeds on new
        sell_switch_proceeds_pnl = None
        if new_yes and new_yes > 0:
            proceeds = shares * busted_yes
            new_shares = proceeds / new_yes
            new_pnl = new_shares * 1.0 if new_won else 0
            sell_switch_proceeds_pnl = round(-STAKE + new_pnl, 4)

        # SELL + SWITCH (fresh $45 added)
        sell_switch_fresh_pnl = None
        if new_yes and new_yes > 0:
            proceeds = shares * busted_yes
            new_pnl_fresh = STAKE * (1 / new_yes - 1) if new_won else -STAKE
            sell_switch_fresh_pnl = round(-STAKE + proceeds + new_pnl_fresh, 4)

        # Write back
        try:
            sb.table("exit_simulation").update({
                "actual_winning_bracket":     winning_question,
                "bet_won":                    bet_won,
                "new_won":                    new_won,
                "hold_pnl":                   round(hold_pnl, 4),
                "sell_only_pnl":              sell_only_pnl,
                "switch_fresh_pnl":           switch_fresh_pnl,
                "sell_switch_proceeds_pnl":   sell_switch_proceeds_pnl,
                "sell_switch_fresh_pnl":      sell_switch_fresh_pnl,
            }).eq("id", sim["id"]).execute()
            _log.info(
                f"  [Exit Sim] resolved {sim['city']} {sim['forecast_date']} "
                f"({sim['detection_type']}): hold={hold_pnl:+.2f} sell_only={sell_only_pnl:+.2f} "
                f"sell_switch_fresh={sell_switch_fresh_pnl!s}"
            )
        except Exception as e:
            _log.warning(f"  [Exit Sim] update failed for {sim['id']}: {e}")
