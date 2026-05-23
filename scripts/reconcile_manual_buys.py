"""
Detect and record user-initiated manual BUYS on Polymarket.
===========================================================

Mirror image of reconcile_manual_sales.py.  That one detects when the
user has sold a bot-placed position; this one detects when the user has
opened a position the bot never knew about (purchased manually via the
Polymarket UI).

How it works
------------
  1. Pull every open position on Polymarket for our funder wallet.
  2. For each, check whether any trade_signals row references the same
     conditionId.  If so, skip — the bot already knows about it.
  3. If not, fetch the buy activity from data-api to find the exact
     fill timestamp and price, then insert a `signal_phase='manual'`
     row into trade_signals.  The resolver and reconcile_manual_sales
     pick up manual rows automatically because they don't filter on
     signal_phase.
  4. Email a one-time info-severity alert per new position.

Runs every 5 minutes via cron alongside reconcile_manual_sales.
Idempotent: a position already in trade_signals is never re-inserted.

Limitations
-----------
  • Side detection: Polymarket's `outcome` field tells us 'Yes' / 'No'
    directly.  If a market has been resolved and the user is just sitting
    on a zombie position (current value $0), we skip it — it would
    confuse the resolver.
  • Cost/fill computation: we use the position's `avgPrice` as fill_price
    and `initialValue` as cost.  These reflect cumulative buys; for a
    single-trade position they're exact.  Multi-fill manual positions
    get an average-price record (close enough).
"""
from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone

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
    format="%(asctime)s UTC | manual-buys | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("manual_buys")


DATA_API = "https://data-api.polymarket.com"

# How long after a position appears on Polymarket do we wait before
# auto-recording?  Brief delay so a slow-confirming bot trade doesn't
# get accidentally double-recorded as a manual buy.  90s is plenty.
MIN_AGE_SECONDS = 90


# ── Title parsing ─────────────────────────────────────────────────────────
# Polymarket weather questions:
#   "Will the highest temperature in Madrid be 21°C on April 3?"
#   "Will the highest temperature in Atlanta be between 78-79°F on May 16?"
#   "Will the highest temperature in Sao Paulo be 23°C or below on April 2?"
_TITLE_RE = re.compile(
    r"highest temperature in (?P<city>.+?) be (?P<bracket>.+?) on (?P<date>.+?)\??$",
    re.I,
)


def _parse_title(title: str) -> tuple[str, str, str]:
    if not title:
        return "", "", ""
    m = _TITLE_RE.search(title)
    if not m:
        return "", "", ""
    return (
        m.group("city").strip(),
        m.group("bracket").strip(),
        m.group("date").strip(),
    )


def _parse_forecast_date(slug: str, title: str) -> str | None:
    """Extract YYYY-MM-DD from slug or title.  Tries slug first (more
    reliable), then title's date suffix."""
    if slug:
        m = re.search(r"-on-([a-z]+)-(\d+)(?:-(\d{4}))?", slug.lower())
        if m:
            months = {"january": 1, "february": 2, "march": 3, "april": 4,
                      "may": 5, "june": 6, "july": 7, "august": 8,
                      "september": 9, "october": 10, "november": 11, "december": 12}
            mon = months.get(m.group(1))
            if mon:
                day  = int(m.group(2))
                year = int(m.group(3)) if m.group(3) else datetime.now().year
                try:
                    return f"{year:04d}-{mon:02d}-{day:02d}"
                except Exception:
                    pass
    return None


# ── Polymarket API ────────────────────────────────────────────────────────

def _fetch_positions(wallet: str) -> list[dict]:
    try:
        r = requests.get(f"{DATA_API}/positions",
                         params={"user": wallet}, timeout=20)
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        log.warning(f"positions fetch failed: {e}")
        return []


def _fetch_first_buy_activity(
    wallet: str, condition_id: str
) -> dict | None:
    """Find the earliest BUY trade for this conditionId by paging
    activity newest-first until we see the first buy."""
    offset = 0
    PAGE = 200
    last_match = None
    while True:
        try:
            r = requests.get(
                f"{DATA_API}/activity",
                params={"user": wallet, "limit": PAGE, "offset": offset},
                timeout=15,
            )
            r.raise_for_status()
            page = r.json() or []
        except Exception:
            break
        if not page:
            break
        for act in page:
            if act.get("type") != "TRADE":
                continue
            if act.get("side") != "BUY":
                continue
            if (act.get("conditionId") or "").lower() != condition_id.lower():
                continue
            # newest-first; the LAST match we walk to is the earliest
            last_match = act
        if len(page) < PAGE:
            break
        offset += PAGE
        if offset > 2000:
            break
    return last_match


def _fetch_latest_buy_activity(
    wallet: str, condition_id: str
) -> dict | None:
    """Find the most-recent BUY trade for this conditionId.  Activity is
    returned newest-first so the first match in page order is the latest."""
    offset = 0
    PAGE = 200
    while True:
        try:
            r = requests.get(
                f"{DATA_API}/activity",
                params={"user": wallet, "limit": PAGE, "offset": offset},
                timeout=15,
            )
            r.raise_for_status()
            page = r.json() or []
        except Exception:
            break
        if not page:
            break
        for act in page:
            if act.get("type") != "TRADE":
                continue
            if act.get("side") != "BUY":
                continue
            if (act.get("conditionId") or "").lower() != condition_id.lower():
                continue
            return act   # newest-first → first hit is latest
        if len(page) < PAGE:
            break
        offset += PAGE
        if offset > 2000:
            break
    return None


# ── Main reconcile ────────────────────────────────────────────────────────

def main() -> int:
    funder = os.getenv("POLY_FUNDER_ADDRESS", "")
    if not funder:
        log.error("POLY_FUNDER_ADDRESS not set — cannot reconcile")
        return 1

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    positions = _fetch_positions(funder)
    log.info(f"checking {len(positions)} open Polymarket positions")

    # Cost-basis dedup.  Earlier versions checked "any row exists for
    # this conditionId" — that caused the 2026-05-17 incident where
    # ~$400 of manual buys (Miami 84-85°F ~$200, Toronto 26°C ~$100,
    # Atlanta 88-89°F ~$109) were silently dropped because each market
    # already had a $0.01 Phase 1 observation row.  The fix: sum the
    # actual USD cost the bot is aware of, compare to Polymarket's
    # `initialValue` for the position, and insert a delta row when the
    # gap is material.  Idempotent: after insert, next cycle sees
    # known_cost == pm_cost so gap → 0.
    MATERIAL_GAP_USD = 0.50   # ignore dust / rounding noise

    def _known_cost_usd(cid: str) -> float:
        """Sum USD-cost we've already recorded against this conditionId.

        Only counts rows that actually deployed real money:
          - order_status in ('filled','sold','partial')
        Observation rows (order_status='observation') are excluded —
        they sometimes have inflated recommended_position from the
        cap-final-guard downgrade path (Tel Aviv-style), which would
        otherwise falsely satisfy the dedup.

        Within counted rows, prefer filled_size_usd (the actual cost
        basis) and fall back to recommended_position if filled_size_usd
        was never written (legacy rows from before that column was
        populated by the executor).
        """
        try:
            r = (sb.table("trade_signals")
                 .select("filled_size_usd,recommended_position,order_status")
                 .eq("condition_id", cid)
                 .limit(1000)
                 .execute()).data or []
        except Exception as e:
            log.warning(f"  could not sum cost for {cid[:12]}…: {e}")
            return float("inf")   # fail-CLOSED: pretend fully reconciled
        total = 0.0
        for row in r:
            if row.get("order_status") not in ("filled", "sold", "partial"):
                continue
            fs = row.get("filled_size_usd")
            if fs is not None:
                total += float(fs)
            else:
                total += float(row.get("recommended_position") or 0)
        return total

    new_count = 0
    for pos in positions:
        cid = (pos.get("conditionId") or "").lower()
        if not cid:
            continue
        # Skip zombie positions (resolved markets sitting at $0)
        if float(pos.get("currentValue") or 0) < 0.01 and float(pos.get("size") or 0) > 0:
            # If it's resolved-to-NO, our_position would be worthless.
            # `redeemable` flag is the on-chain "market resolved" signal.
            if pos.get("redeemable") is True:
                continue

        pm_cost     = float(pos.get("initialValue") or 0)
        known_cost  = _known_cost_usd(cid)
        gap         = round(pm_cost - known_cost, 2)
        if gap < MATERIAL_GAP_USD:
            continue   # nothing materially new

        # Find the latest BUY activity for this cid — that's the most
        # recent unaccounted-for purchase, and gives us a timestamp.
        latest_buy = _fetch_latest_buy_activity(funder, cid)
        if not latest_buy:
            log.debug(f"no buy activity found for {cid[:12]}…; skipping")
            continue
        ts = int(latest_buy.get("timestamp") or 0)
        # Wait a beat so an in-flight bot trade isn't mis-attributed
        if datetime.now(timezone.utc).timestamp() - ts < MIN_AGE_SECONDS:
            log.info(f"  {cid[:12]}… too fresh (<{MIN_AGE_SECONDS}s); will reconsider next cycle")
            continue

        title  = pos.get("title", "")
        slug   = pos.get("slug", "")
        city, bracket, _ = _parse_title(title)
        fdate  = _parse_forecast_date(slug, title)
        if not fdate:
            log.warning(f"  could not parse forecast_date from slug for {cid[:12]}…; skipping")
            continue

        # The position's avgPrice is blended across all fills (bot + manual).
        # For the delta row we don't know the exact manual-fill price, so
        # we use avgPrice as a reasonable proxy.  cost_basis comes from the
        # gap between Polymarket's cumulative buy cost and what we already
        # have on record.
        avg_price  = float(pos.get("avgPrice")    or 0)
        cost_basis = gap
        side       = "YES" if pos.get("outcome") == "Yes" else "NO"

        if cost_basis < 0.5:
            log.info(f"  {cid[:12]}… cost <$0.50 ({cost_basis}); ignoring dust")
            continue

        when_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        row = {
            "city":                 (city or "Unknown").title(),
            "forecast_date":        fdate,
            "market_id":            cid,
            "condition_id":         cid,
            "outcome":              bracket,
            "side":                 side,
            "market_price":         round(avg_price, 4),
            "fill_price":           round(avg_price, 4),
            "recommended_position": round(cost_basis, 2),
            "filled_size_usd":      round(cost_basis, 2),
            "model_probability":    round(avg_price, 4),
            "corrected_probability":round(avg_price, 4),
            "edge":                 0.0,
            "delta_mean":           0.0,
            "delta_std":            0.0,
            "confidence":           0.0,
            "mean_high":            0.0,
            "std_high":             0.0,
            "signal_phase":         "manual",
            "rung_type":            "manual",
            "distance_sigma":       0.0,
            "order_status":         "filled",
            "signal_time":          when_iso,
            "created_at":           when_iso,
            "traded":               True,
            "market_question":      title,
        }
        try:
            sb.table("trade_signals").insert(row).execute()
            log.info(
                f"  ✅ recorded manual buy: {row['city']} {row['outcome']} "
                f"{side} ${cost_basis:.2f} @ {avg_price*100:.1f}¢"
            )
            send_alert(
                subject=f"Manual buy recorded: {row['city']} {row['outcome']}",
                body=(
                    f"Detected a manual buy on Polymarket the bot didn't place "
                    f"and added it to the DB.\n\n"
                    f"  {row['city']} {row['outcome']} {side}\n"
                    f"  size: ${cost_basis:.2f} at {avg_price*100:.1f}¢\n"
                    f"  conditionId: {cid}\n"
                    f"  forecast_date: {fdate}\n\n"
                    f"The position will now appear on the dashboard and the "
                    f"resolver will compute P&L on settlement.  No action needed."
                ),
                severity="info",
                alert_key=f"manual_buy_recorded_{cid}",
                dedupe_minutes=1440,
            )
            new_count += 1
        except Exception as e:
            log.warning(f"  insert failed for {cid[:12]}…: {e}")

    if new_count:
        log.info(f"recorded {new_count} new manual buy(s)")
    else:
        log.info("no new manual buys detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
