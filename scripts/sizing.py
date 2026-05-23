"""
sizing.py — week-by-week trade sizing for Phase 2.

Reads the sizing_schedule table to find the row whose [start_date, end_date]
contains today. Returns trade size and operational caps for the calling code
in phase2_engine.py.

Falls back to safe defaults (size=$0.01 = observation only) if no row exists
for today, so a missing/expired schedule never produces an oversized trade.

See scripts/migrate_sizing_and_guardrails.sql for schema.
See CLAUDE.md Rule 5 — week 2+ rows are only inserted after week 1 data
validates the gate.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client

_log = logging.getLogger(__name__)

# Defer dotenv loading: importers should already have .env loaded.
_url = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
_key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY", "")
_sb = create_client(_url, _key) if (_url and _key) else None


# Hard-coded safe defaults. Used only when sizing_schedule has no row for today,
# which should never happen in production but might during testing or after a
# week ends without a new row being inserted.
_DEFAULT_YES_SIZE_USD          = 0.01
_DEFAULT_NO_SWEEP_SIZE_USD     = 0.01
_DEFAULT_NO_SWEEP_MAX_PER_CITY = 1
_DEFAULT_DEPLOYMENT_CAP_PCT    = 10.0
_DEFAULT_KELLY_FRACTION        = 0.0

# Absolute per-trade and daily-deployment caps. These supersede the percent-
# of-bankroll cap in sizing_schedule and exist to bound the damage of bugs
# (misconfigured schedule row, decimal error, duplicate execution, etc.).
# Read from system_config at query time so the operator can adjust without
# a code deploy. The defaults below are conservative; they only kick in if
# the system_config rows are missing.
_DEFAULT_MAX_TRADE_USD_ABSOLUTE  = 10.0
_DEFAULT_MAX_DAILY_DEPLOY_USD    = 50.0


def _config_float(key: str, default: float) -> float:
    """Read a numeric system_config value, fall back to default on any error."""
    if _sb is None:
        return default
    try:
        r = (_sb.table("system_config").select("value")
             .eq("key", key).maybe_single().execute())
        if not r.data or r.data.get("value") in (None, ""):
            return default
        return float(r.data["value"])
    except Exception:
        return default


def _absolute_cap_usd() -> float:
    return _config_float("max_trade_usd_absolute", _DEFAULT_MAX_TRADE_USD_ABSOLUTE)


def _daily_deploy_cap_usd() -> float:
    return _config_float("max_daily_deploy_usd", _DEFAULT_MAX_DAILY_DEPLOY_USD)


def deployed_today_usd() -> float:
    """Sum of real-money (size > $1) signals written today UTC.
    Used by callers to enforce the daily deployment cap before placing a trade."""
    if _sb is None:
        return 0.0
    try:
        from datetime import datetime, timezone
        today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
        r = (_sb.table("trade_signals")
             .select("recommended_position,filled_size_usd")
             .gte("created_at", today_start)
             .in_("signal_phase", ["phase2", "phase2_sweep"])
             .limit(5000)
             .execute())
        total = 0.0
        for row in r.data or []:
            size = float(row.get("filled_size_usd") or row.get("recommended_position") or 0)
            if size > 1.0:    # exclude $0.01 observations
                total += size
        return total
    except Exception:
        return 0.0


@dataclass(frozen=True)
class SizingConfig:
    week_label: str
    phase2_yes_size_usd: float
    phase2_no_sweep_size_usd: float
    phase2_no_sweep_max_per_city: int
    deployment_cap_pct: float
    kelly_fraction: float
    is_default: bool   # True when fallback defaults are in use (no DB row matched today)


def _safe_defaults() -> SizingConfig:
    return SizingConfig(
        week_label="default_safe",
        phase2_yes_size_usd=_DEFAULT_YES_SIZE_USD,
        phase2_no_sweep_size_usd=_DEFAULT_NO_SWEEP_SIZE_USD,
        phase2_no_sweep_max_per_city=_DEFAULT_NO_SWEEP_MAX_PER_CITY,
        deployment_cap_pct=_DEFAULT_DEPLOYMENT_CAP_PCT,
        kelly_fraction=_DEFAULT_KELLY_FRACTION,
        is_default=True,
    )


def get_current_sizing(today: Optional[date] = None) -> SizingConfig:
    """Return the SizingConfig row covering `today`, or safe defaults."""
    if _sb is None:
        _log.warning("sizing.get_current_sizing: no Supabase client; returning safe defaults")
        return _safe_defaults()

    today = today or date.today()
    today_iso = today.isoformat()
    try:
        r = (_sb.table("sizing_schedule")
             .select("week_label, phase2_yes_size_usd, phase2_no_sweep_size_usd, "
                     "phase2_no_sweep_max_per_city, deployment_cap_pct, kelly_fraction")
             .lte("start_date", today_iso)
             .gte("end_date", today_iso)
             .order("start_date", desc=True)
             .limit(1)
             .execute())
    except Exception as e:
        _log.warning(f"sizing.get_current_sizing: query failed ({e}); using safe defaults")
        return _safe_defaults()

    if not r.data:
        _log.warning(f"sizing.get_current_sizing: no sizing_schedule row covers {today_iso}; using safe defaults")
        return _safe_defaults()

    row = r.data[0]
    return SizingConfig(
        week_label=str(row["week_label"]),
        phase2_yes_size_usd=float(row["phase2_yes_size_usd"]),
        phase2_no_sweep_size_usd=float(row["phase2_no_sweep_size_usd"]),
        phase2_no_sweep_max_per_city=int(row["phase2_no_sweep_max_per_city"]),
        deployment_cap_pct=float(row["deployment_cap_pct"]),
        kelly_fraction=float(row["kelly_fraction"] or 0),
        is_default=False,
    )


def _apply_caps(nominal: float, bankroll: float, cfg: SizingConfig) -> float:
    """Apply three independent caps and return the most restrictive.

    Caps:
      1. deployment_cap_pct × bankroll       (sanity: % of capital)
      2. max_trade_usd_absolute              (hard ceiling against bugs)
      3. remaining daily deploy budget       (max_daily_deploy_usd − deployed_today_usd)

    Returns 0.0 if the daily-deploy cap is exhausted (caller should treat
    that as "no more real-money trades today" and downgrade to observation).
    """
    pct_cap     = bankroll * cfg.deployment_cap_pct / 100.0
    abs_cap     = _absolute_cap_usd()
    daily_room  = max(0.0, _daily_deploy_cap_usd() - deployed_today_usd())
    return min(nominal, pct_cap, abs_cap, daily_room)


def size_for_yes_lock(model_prob: float, market_price: float, bankroll: float, today: Optional[date] = None) -> float:
    """Return the YES-lock trade size for one trade.

    In week 1 (kelly_fraction=0) this is a flat $X from the schedule.
    Once kelly_fraction > 0 we scale: size = base * (1 + kelly * edge / 0.10).
    Three caps applied: deployment_cap_pct, absolute per-trade cap, daily deploy cap.
    """
    cfg = get_current_sizing(today)
    if cfg.kelly_fraction <= 0:
        nominal = cfg.phase2_yes_size_usd
    else:
        edge = max(0.0, model_prob - market_price)
        # Linear edge bump: 8pp edge => 1.0x, 18pp edge => 2.0x, capped at 3x
        bump = min(3.0, 1.0 + cfg.kelly_fraction * (edge - 0.08) / 0.10)
        nominal = cfg.phase2_yes_size_usd * max(1.0, bump)
    return _apply_caps(nominal, bankroll, cfg)


def size_for_no_sweep(model_prob_no: float, no_price: float, bankroll: float, today: Optional[date] = None) -> float:
    """Return the per-bracket NO sweep size for one trade. See size_for_yes_lock for cap chain."""
    cfg = get_current_sizing(today)
    if cfg.kelly_fraction <= 0:
        nominal = cfg.phase2_no_sweep_size_usd
    else:
        edge = max(0.0, model_prob_no - no_price)
        bump = min(3.0, 1.0 + cfg.kelly_fraction * (edge - 0.08) / 0.10)
        nominal = cfg.phase2_no_sweep_size_usd * max(1.0, bump)
    return _apply_caps(nominal, bankroll, cfg)


def no_sweep_max_brackets_per_city(today: Optional[date] = None) -> int:
    return get_current_sizing(today).phase2_no_sweep_max_per_city
