"""
Real Polymarket portfolio reconciler.
=====================================

Pulls the *actual* portfolio total directly from Polymarket and writes it
to `system_config.bankroll_usd` so the dashboard reflects truth, not a
computed estimate of P&L.

Portfolio total = USDC cash on the deposit wallet
                + market value of all open positions (current price × size)

Sources:
  • USDC cash     → TS executor /balance-allowance (POLY_1271 / deposit wallet)
  • Position value → https://data-api.polymarket.com/value?user={proxy}

Designed to run every minute via systemd timer.  Safe to run concurrently
with the trade pipeline — it only writes one row in `system_config`.
"""
from __future__ import annotations

import logging
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))

from clob_http import get_client
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY
try:
    from notifier import send_alert
except Exception:
    def send_alert(*_a, **_k): return False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | reconciler | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bankroll_reconciler")

DATA_API = "https://data-api.polymarket.com"

# State file: tracks consecutive failures so a single transient blip
# (e.g. Polymarket's /balance-allowance taking 20+ seconds to respond)
# does not spam the inbox.  Alert only after the threshold is reached.
import json as _json
from pathlib import Path as _Path
FAIL_STATE = _Path("/root/polymarket/state/reconciler_failures.json")
ALERT_AFTER_CONSECUTIVE = 3   # ~3 minutes of failures at the 60s cadence


def _load_fail_state() -> dict:
    try:
        if FAIL_STATE.exists():
            return _json.loads(FAIL_STATE.read_text())
    except Exception:
        pass
    return {"consecutive": 0, "last_error": "", "last_time": ""}


def _save_fail_state(d: dict) -> None:
    try:
        FAIL_STATE.parent.mkdir(parents=True, exist_ok=True)
        FAIL_STATE.write_text(_json.dumps(d))
    except Exception:
        pass


def get_cash_balance() -> float:
    """USDC available on the deposit wallet (in dollars)."""
    c = get_client()
    raw = c.get_balance_allowance()
    # The TS service returns Polymarket's raw response; balance is a string in 1e6 units.
    bal_str = raw.get("balance", "0")
    return float(bal_str) / 1e6


def get_position_value(proxy_addr: str) -> float:
    """Sum of current market value of all open positions (in dollars)."""
    try:
        r = requests.get(
            f"{DATA_API}/value",
            params={"user": proxy_addr},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        # Response is a list with one element: [{"user": ..., "value": 1.23}]
        if isinstance(data, list) and data:
            return float(data[0].get("value", 0))
        if isinstance(data, dict):
            return float(data.get("value", 0))
    except Exception as e:
        log.warning(f"position value fetch failed: {e}")
    return 0.0


def main() -> int:
    proxy = os.getenv("POLY_FUNDER_ADDRESS", "")
    if not proxy:
        log.error("POLY_FUNDER_ADDRESS missing in env — cannot reconcile")
        return 1

    try:
        cash = get_cash_balance()
    except Exception as e:
        msg = str(e)
        log.error(f"cash balance fetch failed: {msg}")

        # Track consecutive failures so a single transient blip (e.g. a
        # 504 upstream timeout caused by Polymarket's API momentarily
        # being slow) does not page you.  Only alert once we've had
        # ALERT_AFTER_CONSECUTIVE failures in a row.
        state = _load_fail_state()
        state["consecutive"] = int(state.get("consecutive", 0)) + 1
        state["last_error"]  = msg
        state["last_time"]   = datetime.now(timezone.utc).isoformat()
        _save_fail_state(state)

        if state["consecutive"] >= ALERT_AFTER_CONSECUTIVE:
            # Distinguish the most common failure modes so the email is helpful.
            is_upstream_timeout = "504" in msg or "upstream timeout" in msg.lower()
            is_connection_down  = "Connection refused" in msg or "Max retries" in msg
            if is_upstream_timeout:
                subject = "Polymarket API persistently slow (reconciler)"
                hint = (
                    "The TS executor is alive but Polymarket's API has been "
                    "responding slower than the 20s upstream timeout for "
                    f"{state['consecutive']} consecutive minutes.\n\n"
                    "This is usually a Polymarket infrastructure issue and "
                    "resolves on its own. If it persists for an hour, file a "
                    "ticket with Polymarket support.  No action needed unless "
                    "the alert continues."
                )
            elif is_connection_down:
                subject = "TS executor unreachable from reconciler"
                hint = (
                    "Connection to the TS executor is failing entirely.  "
                    "Watchdog should auto-restart it within 10 minutes.\n\n"
                    "If this alert keeps firing past 10 min, run:\n"
                    "  systemctl status polymarket-ts-executor"
                )
            else:
                subject = "Reconciler can't read cash balance"
                hint = (
                    f"Unfamiliar error pattern.  Investigate manually:\n"
                    f"  ssh root@... 'systemctl status polymarket-ts-executor'\n"
                    f"  ssh root@... 'journalctl -u polymarket-ts-executor -n 50'"
                )

            send_alert(
                subject=subject,
                body=(
                    f"Failed {state['consecutive']} consecutive reconciler runs.\n\n"
                    f"Last error: {msg}\n\n{hint}"
                ),
                severity="critical",
                alert_key="reconciler_cash_fetch",
            )
        else:
            log.info(
                f"transient failure {state['consecutive']}/"
                f"{ALERT_AFTER_CONSECUTIVE} — not alerting yet"
            )
        return 1
    # Got cash successfully — clear the failure counter
    _save_fail_state({"consecutive": 0, "last_error": "", "last_time": ""})

    positions = get_position_value(proxy)
    total = round(cash + positions, 2)

    log.info(
        f"cash ${cash:.2f} + positions ${positions:.2f} = portfolio ${total:.2f}"
    )

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        sb.table("system_config").upsert(
            {"key": "bankroll_usd", "value": str(total)},
            on_conflict="key",
        ).execute()
        # Also store the cash component separately for the dashboard if it
        # wants to display "available to trade" distinct from total portfolio.
        sb.table("system_config").upsert(
            {"key": "available_cash_usd", "value": str(round(cash, 2))},
            on_conflict="key",
        ).execute()
        sb.table("system_config").upsert(
            {"key": "open_position_value_usd", "value": str(round(positions, 2))},
            on_conflict="key",
        ).execute()
    except Exception as e:
        log.error(f"supabase write failed: {e}")
        send_alert(
            subject="Bankroll reconciler can't write to Supabase",
            body=f"system_config upsert raised: {e}\n\n"
                 f"Dashboard bankroll will be stale until this is resolved.",
            severity="critical",
            alert_key="reconciler_db_write",
        )
        return 1

    log.info(f"system_config.bankroll_usd ← ${total:.2f} (refreshed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
