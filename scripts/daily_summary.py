"""
Daily summary email.
====================

Runs once a day (cron) after the end-of-day reconciler.  Builds a short
plain-text summary of:

  • Current bankroll vs starting bankroll vs yesterday
  • Trades executed today (Phase 1 + Phase 2)
  • Today's P&L (realized) and unrealized open position value
  • Any failed orders worth reviewing

Emails it to ALERT_TO_EMAIL.  Idempotent — safe to re-run.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))

from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY
from notifier import send_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | summary | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("daily_summary")


def _get_cfg(sb, key: str, default: str = "") -> str:
    try:
        r = sb.table("system_config").select("value").eq("key", key).single().execute()
        return r.data.get("value", default) if r.data else default
    except Exception:
        return default


def build_summary(sb, for_date: date) -> str:
    iso = for_date.isoformat()

    bankroll_now  = float(_get_cfg(sb, "bankroll_usd", "0") or 0)
    cash_now      = float(_get_cfg(sb, "available_cash_usd", "0") or 0)
    positions_now = float(_get_cfg(sb, "open_position_value_usd", "0") or 0)
    live_start    = _get_cfg(sb, "live_start_date", "")
    live_start_br = float(_get_cfg(sb, "live_starting_bankroll", "0") or 0)

    # Yesterday's snapshot for delta (if any)
    yesterday = (for_date - timedelta(days=1)).isoformat()
    snap_resp = (sb.table("bankroll_snapshots")
                 .select("total_value, snapshot_date")
                 .lte("snapshot_date", yesterday)
                 .order("snapshot_date", desc=True)
                 .limit(1)
                 .execute())
    prev_bankroll = float(snap_resp.data[0]["total_value"]) if snap_resp.data else None
    delta_day     = (bankroll_now - prev_bankroll) if prev_bankroll is not None else None

    # Trades today (any phase)
    trades = (sb.table("trade_signals")
              .select("city, outcome, signal_phase, market_price, recommended_position, "
                      "order_status, fill_price, pnl_usd")
              .eq("forecast_date", iso)
              .gt("recommended_position", 1)
              .execute()).data or []

    real_trades = [t for t in trades if (t.get("recommended_position") or 0) > 1]
    filled    = [t for t in real_trades if t.get("order_status") == "filled"]
    failed    = [t for t in real_trades if t.get("order_status") == "failed"]
    pending   = [t for t in real_trades if t.get("order_status") == "pending"]

    # Only count realized P&L from actually-filled orders. A trade marked
    # 'failed' never reached the exchange and must not contribute to P&L
    # even if a stale row has a non-null pnl_usd from an earlier writer.
    realized_today = sum(
        float(t.get("pnl_usd") or 0)
        for t in real_trades
        if t.get("pnl_usd") is not None and t.get("order_status") == "filled"
    )

    lines = []
    lines.append(f"Daily summary — {iso}")
    lines.append("=" * 50)
    lines.append("")
    lines.append("Bankroll:")
    lines.append(f"  current total     : ${bankroll_now:.2f}")
    lines.append(f"    • cash          : ${cash_now:.2f}")
    lines.append(f"    • open positions: ${positions_now:.2f}")
    # "change vs prior" is only meaningful once we have at least one
    # snapshot taken AFTER the live_start_date.  Skip it during the very
    # first day of live trading to avoid printing nonsense like "−$393.67"
    # caused by inheriting an old paper-mode snapshot value.
    if delta_day is not None and live_start and yesterday >= live_start:
        sign = "+" if delta_day >= 0 else ""
        lines.append(f"  change vs prior   : {sign}${delta_day:.2f}")
    if live_start:
        cum = bankroll_now - live_start_br
        sign = "+" if cum >= 0 else ""
        lines.append(f"  vs live start     : {sign}${cum:.2f} ({live_start})")
    lines.append("")

    lines.append("Trade activity today (real-money only):")
    lines.append(f"  filled  : {len(filled)}")
    lines.append(f"  pending : {len(pending)}")
    lines.append(f"  failed  : {len(failed)}")
    lines.append(f"  realized P&L today : ${realized_today:+.2f}")
    lines.append("")

    if filled:
        lines.append("Filled today:")
        for t in filled:
            fp = t.get("fill_price")
            fp_s = f"{fp*100:.1f}¢" if fp is not None else "?"
            pnl  = t.get("pnl_usd")
            pnl_s = f"${pnl:+.2f}" if pnl is not None else "open"
            lines.append(
                f"  • {t['city']} {t['outcome']} {t['signal_phase']}: "
                f"${t['recommended_position']:.2f} @ {fp_s} → {pnl_s}"
            )
        lines.append("")

    if failed:
        lines.append("FAILED today (review):")
        for t in failed:
            lines.append(
                f"  • {t['city']} {t['outcome']} {t['signal_phase']}: "
                f"${t['recommended_position']:.2f} @ {t['market_price']*100:.1f}¢"
            )
        lines.append("")

    if pending:
        lines.append("Still pending at EOD:")
        for t in pending:
            lines.append(
                f"  • {t['city']} {t['outcome']} {t['signal_phase']}: "
                f"${t['recommended_position']:.2f} @ {t['market_price']*100:.1f}¢"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    today = date.today()
    body = build_summary(sb, today)
    log.info("sending daily summary")
    # alert_key includes the date so we don't dedupe consecutive days
    send_alert(
        subject=f"Daily summary — {today.isoformat()}",
        body=body,
        severity="info",
        alert_key=f"daily_summary_{today.isoformat()}",
        dedupe_minutes=720,   # 12h, well under daily cadence
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
