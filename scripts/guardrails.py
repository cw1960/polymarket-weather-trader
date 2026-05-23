"""
guardrails.py — pre-trade safety checks.

Four guardrails, all controlled via system_config rows so they can be tuned
without a code deploy:

  1. phase2_paused                — master kill switch (existing)
  2. min_bankroll_usd_trading     — auto-pause if bankroll falls below this
  3. max_daily_loss_pct           — halt for the rest of TODAY if losses exceed this fraction of bankroll
  4. min_3day_win_rate            — auto-pause if rolling 3-day Phase 2 win rate dips below this

A trade is allowed if and only if all four pass.  Each time a guardrail
fires (transitions from clear to blocking) we write a row to
guardrail_events for audit.

Used by phase2_engine.py before placing any non-observation trade.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from supabase import create_client

_log = logging.getLogger(__name__)

_url = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY", "")
_sb = create_client(_url, _key) if (_url and _key) else None


@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    reason: str          # "" if allowed, otherwise human-readable
    guardrail: str       # "" if allowed, otherwise machine-readable name


def _get_config(key: str) -> str | None:
    try:
        r = (_sb.table("system_config").select("value")
             .eq("key", key).maybe_single().execute())
        return r.data.get("value") if r.data else None
    except Exception:
        return None


def _get_float(key: str, default: float) -> float:
    v = _get_config(key)
    try:
        return float(v) if v is not None and v != "" else default
    except Exception:
        return default


def _log_guardrail_event(name: str, details: dict) -> None:
    try:
        _sb.table("guardrail_events").insert({
            "guardrail":    name,
            "details_json": details,
        }).execute()
        _sb.table("system_config").upsert({
            "key":   "auto_pause_reason",
            "value": f"{name}: {details}",
        }).execute()
    except Exception as e:
        _log.debug(f"guardrails: failed to write audit row: {e}")


# ── Guardrail 1: global pause flag (existing) ─────────────────────────────

def _g_phase2_paused() -> GuardrailDecision:
    if _get_config("phase2_paused") == "1":
        return GuardrailDecision(False, "phase2_paused=1 — global kill switch", "phase2_paused")
    return GuardrailDecision(True, "", "")


# ── Guardrail 2: bankroll floor ────────────────────────────────────────────

def _g_bankroll_floor() -> GuardrailDecision:
    bankroll = _get_float("bankroll_usd", 0.0)
    floor    = _get_float("min_bankroll_usd_trading", 1500.0)
    if bankroll < floor:
        msg = f"bankroll ${bankroll:.2f} < floor ${floor:.2f}"
        return GuardrailDecision(False, msg, "bankroll_floor")
    return GuardrailDecision(True, "", "")


# ── Guardrail 3: daily loss limit ──────────────────────────────────────────

def _g_daily_loss_limit() -> GuardrailDecision:
    """If today's resolved Phase 2 P&L is below -max_daily_loss_pct * bankroll,
    halt trading for the rest of today."""
    today_iso        = date.today().isoformat()
    paused_today     = _get_config("today_loss_paused_date")
    if paused_today == today_iso:
        return GuardrailDecision(False, f"daily loss limit already fired today ({today_iso})", "daily_loss")

    bankroll = _get_float("bankroll_usd", 0.0)
    pct      = _get_float("max_daily_loss_pct", 0.08)
    if bankroll <= 0:
        return GuardrailDecision(True, "", "")   # bankroll-floor catches this

    # Today's resolved P&L for Phase 2 (filled only).
    try:
        window_start = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        r = (_sb.table("trade_signals")
             .select("pnl_usd, signal_phase, order_status, resolved_at")
             .gte("resolved_at", window_start)
             .in_("signal_phase", ["phase2", "phase2_sweep"])
             .eq("order_status", "filled")
             .not_.is_("pnl_usd", "null")
             .limit(5000)
             .execute())
        today_pnl = sum(float(x["pnl_usd"]) for x in (r.data or []))
    except Exception as e:
        _log.debug(f"guardrails: daily P&L query failed ({e}); allowing")
        return GuardrailDecision(True, "", "")

    loss_limit_usd = -pct * bankroll
    if today_pnl <= loss_limit_usd:
        msg = f"today's P&L ${today_pnl:+.2f} ≤ limit ${loss_limit_usd:+.2f} (= -{pct*100:.0f}% of ${bankroll:.0f})"
        _log_guardrail_event("daily_loss", {
            "today_pnl": today_pnl, "loss_limit_usd": loss_limit_usd,
            "bankroll":  bankroll,  "pct":            pct,
        })
        try:
            _sb.table("system_config").upsert({
                "key":   "today_loss_paused_date",
                "value": today_iso,
            }).execute()
        except Exception:
            pass
        return GuardrailDecision(False, msg, "daily_loss")
    return GuardrailDecision(True, "", "")


# ── Guardrail 4b (NEW 2026-05-21): EV-per-dollar over rolling 50 trades ───
#
# Per the senior-dev review: win rate is the wrong primary signal. A 70%
# win rate at 90¢ entries is mediocre; a 55% rate at 30¢ entries is great.
# This guardrail measures the actual profit metric.
#
# Pause if last-50 NO-sweep resolved trades have realized EV per dollar
# below min_ev_per_dollar_50trade (default -2%). Requires at least
# min_ev_resolved_trades (default 30) before it can fire — small samples
# are noise.
#
# Win-rate guardrail (G4) is kept as a secondary safety; both fire on the
# same conditions in practice if the strategy collapses.

def _g_ev_per_dollar() -> GuardrailDecision:
    floor   = _get_float("min_ev_per_dollar_50trade", -0.02)
    min_n   = int(_get_float("min_ev_resolved_trades", 30))
    cutoff  = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    try:
        r = (_sb.table("trade_signals")
             .select("market_price,actual_outcome,side,signal_phase,resolved_at")
             .gte("resolved_at", cutoff)
             .eq("signal_phase", "phase2_sweep")
             .not_.is_("winning_bracket", "null")
             .order("resolved_at", desc=True)
             .limit(50)
             .execute())
    except Exception as e:
        _log.debug(f"guardrails: EV query failed ({e}); allowing")
        return GuardrailDecision(True, "", "")

    rows = r.data or []
    if len(rows) < min_n:
        return GuardrailDecision(True, "", "")

    # Compute per-trade EV-per-dollar assuming a unit-size bet at the row's
    # market_price for its side. (Same math as the dashboard; here we don't
    # care about the absolute size since EV/$ is size-invariant.)
    total_pnl_per_unit = 0.0
    n = 0
    for row in rows:
        p = float(row.get("market_price") or 0)
        if not (0 < p < 1):
            continue
        a = str(row.get("actual_outcome", ""))
        side = row.get("side") or "NO"
        won = (a == "true") if side == "YES" else (a == "false")
        # Per $1 deployed: win pays (1-p)/p, lose pays -1
        per_dollar = (1.0 - p) / p if won else -1.0
        total_pnl_per_unit += per_dollar
        n += 1
    if n < min_n:
        return GuardrailDecision(True, "", "")

    ev_per_dollar = total_pnl_per_unit / n
    if ev_per_dollar < floor:
        msg = f"last-{n} EV/$ {ev_per_dollar*100:.1f}% < floor {floor*100:.1f}%"
        _log_guardrail_event("ev_per_dollar", {
            "n": n, "ev_per_dollar": ev_per_dollar, "floor": floor,
        })
        try:
            _sb.table("system_config").upsert({
                "key": "phase2_paused", "value": "1",
            }).execute()
        except Exception:
            pass
        return GuardrailDecision(False, msg, "ev_per_dollar")
    return GuardrailDecision(True, "", "")


# ── Guardrail 4: rolling 3-day win rate ───────────────────────────────────

def _g_3day_win_rate() -> GuardrailDecision:
    min_rate = _get_float("min_3day_win_rate", 0.45)
    min_n    = int(_get_float("min_3day_resolved_trades", 15))
    cutoff   = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    try:
        r = (_sb.table("trade_signals")
             .select("pnl_usd, order_status, signal_phase, resolved_at")
             .gte("resolved_at", cutoff)
             .in_("signal_phase", ["phase2", "phase2_sweep"])
             .eq("order_status", "filled")
             .not_.is_("pnl_usd", "null")
             .limit(5000)
             .execute())
    except Exception as e:
        _log.debug(f"guardrails: 3-day query failed ({e}); allowing")
        return GuardrailDecision(True, "", "")

    rows = r.data or []
    n    = len(rows)
    if n < min_n:
        # Not enough data to make a claim — let the daily-loss guardrail catch real bleeding.
        return GuardrailDecision(True, "", "")

    wins = sum(1 for x in rows if float(x["pnl_usd"]) > 0)
    rate = wins / n
    if rate < min_rate:
        msg = f"3-day win rate {rate:.1%} ({wins}/{n}) < floor {min_rate:.1%}"
        _log_guardrail_event("3day_win_rate", {
            "wins": wins, "n": n, "rate": rate, "floor": min_rate,
        })
        # Also flip the master phase2_paused flag so the bot stops until human review.
        try:
            _sb.table("system_config").upsert({
                "key": "phase2_paused", "value": "1",
            }).execute()
        except Exception:
            pass
        return GuardrailDecision(False, msg, "3day_win_rate")
    return GuardrailDecision(True, "", "")


# ── Public entrypoint ──────────────────────────────────────────────────────

_GUARDRAILS = [
    _g_phase2_paused,
    _g_bankroll_floor,
    _g_daily_loss_limit,
    _g_ev_per_dollar,    # NEW 2026-05-21 — primary EV-based pause
    _g_3day_win_rate,    # secondary win-rate safety (kept for defense-in-depth)
]


def check_trade_allowed() -> GuardrailDecision:
    """Run all four guardrails in order; return the first that blocks."""
    if _sb is None:
        # Without DB connectivity, fail closed (don't trade real money).
        return GuardrailDecision(False, "no Supabase client", "no_db")
    for g in _GUARDRAILS:
        d = g()
        if not d.allowed:
            return d
    return GuardrailDecision(True, "", "")
