"""
Detect and record user-initiated manual sells on Polymarket.
============================================================

The system can't always reach the wallet directly, but Polymarket's
public data-api exposes every trade for any address.  This script:

  1. Finds every trade_signals row that is `filled` but not yet resolved
     (pnl_usd IS NULL) and whose market has NOT yet reached its
     resolution date (so we don't compete with the resolver).
  2. For each such row, queries Polymarket's /positions API for the
     funder address.  If the position is gone (or smaller than the
     amount we bought), the user manually sold some or all of it.
  3. Sums the user's SELL transactions on that conditionId via
     /activity and uses the actual proceeds to compute realized P&L.
  4. Writes pnl_usd + order_status='sold' to the trade_signals row
     and emails an info-severity confirmation.

Designed to run every 5 min via cron alongside the bankroll reconciler.
Safe to run more often — uses dedupe on order_status='sold' to avoid
re-processing the same row, and the per-row write is idempotent.

Limitations
-----------
• Only handles full closes and partial sells where the user has
  exited the WHOLE remaining position.  If the user sells 50% and
  keeps 50%, this script records what was sold (cost-basis-prorated)
  and marks `order_status='partial_sold'` so the resolver still picks
  up the remainder.
• Cannot tell the difference between a user-initiated sell and a
  market that resolved and the user redeemed.  We mitigate by
  refusing to touch rows whose forecast_date <= today (those are
  resolver territory).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))

from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY
try:
    from notifier import send_alert
except Exception:
    def send_alert(*_a, **_k): return False


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | manual-recon | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("manual_recon")


DATA_API = "https://data-api.polymarket.com"


# ── Helpers ───────────────────────────────────────────────────────────────

def _polymarket_positions(user_addr: str) -> list[dict]:
    """Live open positions for a wallet.  Empty list on error."""
    try:
        r = requests.get(f"{DATA_API}/positions",
                         params={"user": user_addr}, timeout=15)
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        log.warning(f"positions fetch failed: {e}")
        return []


def _polymarket_sells_for_condition(user_addr: str, condition_id: str,
                                    since_iso: str | None = None) -> list[dict]:
    """
    Return every SELL trade by `user_addr` on `condition_id`.  Polymarket
    activity is paginated; we walk pages until we exhaust matches or hit
    a reasonable cap.  `since_iso` filters out activity older than that
    timestamp (useful so we don't pick up sells from a prior cycle of
    the same market).
    """
    out: list[dict] = []
    offset = 0
    PAGE   = 200
    while True:
        try:
            r = requests.get(
                f"{DATA_API}/activity",
                params={"user": user_addr, "limit": PAGE, "offset": offset},
                timeout=15,
            )
            r.raise_for_status()
            page = r.json() or []
        except Exception as e:
            log.warning(f"activity fetch error at offset={offset}: {e}")
            break
        if not page:
            break
        for act in page:
            cid = (act.get("conditionId") or "").lower()
            if cid != condition_id.lower():
                continue
            if act.get("type") != "TRADE":
                continue
            if act.get("side") != "SELL":
                continue
            if since_iso:
                ts = act.get("timestamp")
                if ts and datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() < since_iso:
                    continue
            out.append(act)
        if len(page) < PAGE:
            break
        offset += PAGE
        if offset > 1000:   # hard cap; should never happen for a single market
            break
    return out


# ── Main reconcile loop ───────────────────────────────────────────────────

def main() -> int:
    funder = os.getenv("POLY_FUNDER_ADDRESS", "")
    if not funder:
        log.error("POLY_FUNDER_ADDRESS not set in env — cannot reconcile")
        return 1

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Pull every real-money trade_signal that is filled-but-unresolved AND
    # whose market hasn't yet reached its resolution date.
    today = date.today().isoformat()
    candidates = (sb.table("trade_signals")
                  .select("id, city, outcome, condition_id, forecast_date, "
                          "fill_price, recommended_position, filled_size_usd, "
                          "order_status, created_at, signal_phase")
                  .eq("order_status", "filled")
                  .is_("pnl_usd", "null")
                  .gt("recommended_position", 1)
                  .gte("forecast_date", today)
                  .execute()).data or []

    if not candidates:
        log.info("no filled-but-unresolved positions to check")
        return 0

    log.info(f"checking {len(candidates)} open positions for manual sells")

    # Fetch live positions once
    live_positions = _polymarket_positions(funder)
    pos_by_cid = {(p.get("conditionId") or "").lower(): p
                  for p in live_positions if p.get("size") is not None}

    reconciled = 0
    for sig in candidates:
        cid = (sig.get("condition_id") or "").lower()
        if not cid:
            continue

        # What size we expect to be holding (token-equivalents from our buy).
        # If filled_size_usd is set, it's the cost basis; tokens ≈ filled/fill_price.
        cost_basis = (float(sig.get("filled_size_usd"))
                      if sig.get("filled_size_usd") is not None
                      else float(sig["recommended_position"]))
        fill_price = float(sig.get("fill_price") or sig.get("market_price") or 0)
        if fill_price <= 0:
            continue
        expected_tokens = cost_basis / fill_price

        live = pos_by_cid.get(cid)
        live_size = float(live.get("size", 0)) if live else 0.0

        # Heuristic: if live_size is < 50% of what we should be holding, the
        # user has sold a meaningful chunk.  Close to zero = full exit.
        if live_size >= expected_tokens * 0.5:
            continue   # no meaningful sell detected

        # Pull SELL transactions on this market since we entered
        sells = _polymarket_sells_for_condition(
            funder, cid, since_iso=sig["created_at"],
        )
        if not sells:
            # Position vanished without a sell trade — could be redemption
            # (market resolved on-chain).  Skip; resolver will handle it.
            log.info(
                f"  {sig['city']} {sig['outcome']}: position vanished without "
                f"SELL activity — likely resolved; leaving for resolver"
            )
            continue

        proceeds = sum(float(s.get("usdcSize", 0)) for s in sells)
        tokens_sold = sum(float(s.get("size", 0)) for s in sells)

        is_full_exit = live_size < 0.5    # tolerate rounding dust

        # Cost-basis pro-rated to tokens actually sold
        cost_sold = cost_basis * (tokens_sold / expected_tokens) if expected_tokens > 0 else 0
        pnl = round(proceeds - cost_sold, 2)

        new_status = "sold" if is_full_exit else "partial_sold"

        log.info(
            f"  {sig['city']} {sig['outcome']}: tokens_sold={tokens_sold:.2f} "
            f"proceeds=${proceeds:.2f} cost_sold=${cost_sold:.2f} pnl=${pnl:+.2f} "
            f"→ {new_status}"
        )

        sb.table("trade_signals").update({
            "pnl_usd":      pnl,
            "order_status": new_status,
        }).eq("id", sig["id"]).execute()

        send_alert(
            subject=f"Auto-reconciled: {sig['city']} {sig['outcome']}",
            body=(
                f"Detected a manual sell on Polymarket and updated the DB.\n\n"
                f"Position:  {sig['city']} {sig['outcome']} ({sig['signal_phase']})\n"
                f"Cost basis: ${cost_sold:.2f}\n"
                f"Proceeds:   ${proceeds:.2f}\n"
                f"Realized:   ${pnl:+.2f}\n"
                f"Status:     {new_status}\n\n"
                f"You don't need to take any action — this is informational."
            ),
            severity="info",
            alert_key=f"manual_recon_{sig['id']}",
            dedupe_minutes=1440,   # 24h, prevent re-firing if reconcile races
        )
        reconciled += 1

    if reconciled:
        log.info(f"reconciled {reconciled} position(s)")
    else:
        log.info("no manual sells detected")

    return 0


if __name__ == "__main__":
    sys.exit(main())
