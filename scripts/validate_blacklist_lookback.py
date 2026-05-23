"""
Blacklist Lookback Validation.
==============================

Asks the question: of the real-money YES trades we placed in the last 30
days, how many WOULD have been blocked by the blacklist if it had been
in place at the time?

Doesn't just check the current blacklist — pulls each tracked trader's
full trade history from Polymarket and reconstructs what they were
holding at the time each of our trades fired.  That way the answer
reflects what the blacklist would have done historically, not what it
would do today against today's open positions.

Output:
  • Per-trade verdict (would_have_blocked: true/false/unknown)
  • Aggregate: how many trades blocked, how much $ saved/cost
  • If "would have blocked but the trade WON": flag — informs whether
    the rule is too aggressive in some patterns.

Run as a one-off:
  python3 scripts/validate_blacklist_lookback.py [--days 30]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.dirname(__file__))

from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY
from sync_bracket_blacklist import TRACKED_TRADERS, BLACKLIST_PRICE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | lookback | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("blacklist_lookback")

DATA_API = "https://data-api.polymarket.com"


def _fetch_trader_activity(wallet: str, since_ts: int) -> list[dict]:
    """Page through Polymarket activity for `wallet`, newest-first, until
    we pass the cutoff timestamp.  Filters to TRADE rows."""
    out: list[dict] = []
    offset = 0
    PAGE   = 200
    while True:
        try:
            r = requests.get(
                f"{DATA_API}/activity",
                params={"user": wallet, "limit": PAGE, "offset": offset},
                timeout=20,
            )
            r.raise_for_status()
            page = r.json() or []
        except Exception as e:
            log.warning(f"activity page error: {e}")
            break
        if not page:
            break
        for act in page:
            if act.get("type") != "TRADE":
                continue
            ts = int(act.get("timestamp") or 0)
            if ts < since_ts:
                # Activity is newest-first; once we cross the cutoff we're done
                return out
            out.append(act)
        if len(page) < PAGE:
            break
        offset += PAGE
        if offset > 5000:    # safety cap
            break
    return out


def _would_have_blocked(
    our_trade: dict,
    trader_trades: list[dict],
) -> tuple[bool, dict | None]:
    """Return (would_block, evidence_trade).

    Block if any tracker had a NO buy at >= BLACKLIST_PRICE on the same
    conditionId BEFORE our trade's created_at.  The earliest matching
    trade is returned as evidence.
    """
    cid = (our_trade.get("condition_id") or "").lower()
    if not cid:
        return False, None
    try:
        our_ts = int(datetime.fromisoformat(
            our_trade["created_at"].replace("Z", "+00:00")
        ).timestamp())
    except Exception:
        return False, None

    # Filter trader trades to those on this cid, before our trade, NO side, high price
    candidates = []
    for t in trader_trades:
        if (t.get("conditionId") or "").lower() != cid:
            continue
        if t.get("side") != "BUY":
            continue
        if t.get("outcome") != "No":
            continue
        if int(t.get("timestamp") or 0) >= our_ts:
            continue
        try:
            if float(t.get("price") or 0) < BLACKLIST_PRICE:
                continue
        except Exception:
            continue
        candidates.append(t)
    if not candidates:
        return False, None
    # Use the earliest match — that's when the bracket "became blacklisted"
    candidates.sort(key=lambda x: int(x.get("timestamp") or 0))
    return True, candidates[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30,
                    help="Lookback window in days (default 30)")
    args = ap.parse_args()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    cutoff_iso = cutoff.isoformat()
    cutoff_ts  = int(cutoff.timestamp())

    # Pull our real-money YES trades
    rows = (sb.table("trade_signals")
            .select("id, city, outcome, condition_id, signal_phase, side, "
                    "market_price, recommended_position, fill_price, pnl_usd, "
                    "order_status, created_at")
            .gt("recommended_position", 1)
            .eq("side", "YES")
            .gte("created_at", cutoff_iso)
            .order("created_at")
            .execute()).data or []

    log.info(f"checking {len(rows)} real-money YES trades since {cutoff_iso[:10]}")

    if not rows:
        log.info("nothing to check.")
        return 0

    # Pull each tracker's recent activity once (full window)
    all_trader_trades: list[dict] = []
    for wallet, label in TRACKED_TRADERS:
        log.info(f"pulling activity for {label} ({wallet[:10]}…)")
        trades = _fetch_trader_activity(wallet, cutoff_ts)
        log.info(f"  → {len(trades)} trades in window")
        for t in trades:
            t["_tracker_label"] = label
            t["_tracker_wallet"] = wallet
        all_trader_trades.extend(trades)

    blocked_count   = 0
    blocked_total_usd = 0.0
    wins_blocked    = 0
    losses_blocked  = 0
    open_blocked    = 0
    blocked_pnl_sum = 0.0

    rows_out: list[tuple[bool, dict, dict | None]] = []
    for r in rows:
        would, evidence = _would_have_blocked(r, all_trader_trades)
        rows_out.append((would, r, evidence))
        if would:
            blocked_count += 1
            blocked_total_usd += float(r.get("recommended_position") or 0)
            pnl = r.get("pnl_usd")
            if pnl is None:
                open_blocked += 1
            elif float(pnl) > 0:
                wins_blocked += 1
                blocked_pnl_sum += float(pnl)
            else:
                losses_blocked += 1
                blocked_pnl_sum += float(pnl)

    # ─── Report ───
    print()
    print("=" * 78)
    print(f"BLACKLIST LOOKBACK — last {args.days} days")
    print("=" * 78)
    print()
    print(f"Total real-money YES trades:  {len(rows)}")
    print(f"Would have been blocked:      {blocked_count}  "
          f"({100*blocked_count/len(rows):.1f}% of trades)")
    print(f"Total $ that would not have been deployed: ${blocked_total_usd:.2f}")
    print()
    print("Of the blocked trades:")
    print(f"  Wins:   {wins_blocked}  (would have FORFEITED these wins)")
    print(f"  Losses: {losses_blocked}  (would have AVOIDED these losses)")
    print(f"  Open:   {open_blocked}  (unresolved at lookback time)")
    print(f"  Net P&L change if blacklist had been live: "
          f"${-blocked_pnl_sum:+.2f}  (negative = blacklist would have hurt us)")
    print()

    if blocked_count == 0:
        print("Interpretation: blacklist would have made no difference in")
        print("the lookback window.  It's running as insurance — minimal")
        print("downside, just no recent trades crossed a tracker's threshold.")
    elif blocked_pnl_sum < 0:
        # We took losses → blacklist would have saved us
        print("Interpretation: blacklist would have SAVED money in the lookback.")
        print(f"Net positive impact: ${-blocked_pnl_sum:.2f}")
        print("Recommend: ship the rule.")
    else:
        # We won → blacklist would have cost us
        print("Interpretation: blacklist would have COST money in the lookback.")
        print(f"Net negative impact: ${-blocked_pnl_sum:.2f}")
        print("Recommend: review whether the threshold is too aggressive,")
        print("or whether the trader's pattern doesn't apply to our cities.")

    print()
    print("Per-trade verdicts:")
    print(f"  {'created_at':<19} {'city':<14} {'bracket':<10} {'$':>6}  "
          f"{'pnl':>8}  block?  evidence")
    for would, r, ev in rows_out:
        flag = "BLOCK" if would else " ok  "
        pnl  = r.get("pnl_usd")
        pnl_s = f"${float(pnl):+.2f}" if pnl is not None else "  open  "
        when = r["created_at"][:19].replace("T", " ")
        ev_s = ""
        if ev:
            ev_s = (
                f"{ev.get('_tracker_label','?')} NO @ "
                f"${float(ev.get('price',0)):.3f} on {datetime.fromtimestamp(int(ev.get('timestamp',0)), tz=timezone.utc).isoformat()[:16]}"
            )
        print(
            f"  {when:<19} {(r.get('city') or '')[:14]:<14} "
            f"{(r.get('outcome') or '')[:10]:<10} "
            f"${float(r.get('recommended_position') or 0):>5.2f}  "
            f"{pnl_s:>8}  {flag}  {ev_s}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
