"""
Bracket Blacklist sync.
========================

Pulls live positions from tracked external traders (initially just
Weatherstappen) via Polymarket's data-api and rebuilds the
`bracket_blacklist` table with every NO position at >= BLACKLIST_PRICE.

Run hourly via cron.  Idempotent — upserts on conditionId.

Why this exists
---------------
Weatherstappen has 99.7% win rate over 937 resolved >=0.50 NO trades.
When they put $200 down at $0.99 on "Madrid 25°C will NOT happen,"
it's the highest-quality external signal we have access to.  Any YES
trade WE would place on that same bracket is −EV by construction —
we're betting against a battle-tested operator who has done $1M of
volume on this exact pattern.

The blacklist captures their currently-open NO positions; the Phase 2
engine refuses to place real-money YES orders on any conditionId in
the table.  Blocked attempts are logged to bracket_blacklist_blocks
so we can measure the savings.

Adding more tracked traders
---------------------------
Append (wallet, label) tuples to TRACKED_TRADERS.  Each gets pulled
in turn.  Per-trader gating (e.g. require min_volume_usd before
trusting) lives in _qualifies_for_blacklist().
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
    format="%(asctime)s UTC | blacklist-sync | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("blacklist_sync")

DATA_API = "https://data-api.polymarket.com"

# Brackets get added to the blacklist when the tracker holds NO at >= this price
BLACKLIST_PRICE = 0.95

# (wallet_address, friendly_label) — only traders whose >=0.95 NO history
# we trust as informed market consensus.  See trader-analyzer notes.
TRACKED_TRADERS: list[tuple[str, str]] = [
    ("0xb9012e0d9b60d3920286309328b935cdfa609fc4", "Weatherstappen"),
]


# ── Title parsing ─────────────────────────────────────────────────────────
# Polymarket weather questions look like:
#   "Will the highest temperature in Madrid be 21°C on April 3?"
#   "Will the highest temperature in Atlanta be between 78-79°F on May 16?"
#   "Will the highest temperature in Sao Paulo be 23°C or below on April 2?"
# We just need (city, bracket_label, date) for human-readable storage.

_QUESTION_RE = re.compile(
    r"highest temperature in (?P<city>.+?) be (?P<bracket>.+?) on (?P<date>.+?)\??$",
    re.I,
)


def _parse_question(question: str) -> tuple[str, str, str]:
    """Best-effort parse of Polymarket weather market question."""
    if not question:
        return "", "", ""
    m = _QUESTION_RE.search(question)
    if not m:
        return "", "", ""
    return (
        m.group("city").strip(),
        m.group("bracket").strip(),
        m.group("date").strip(),
    )


def _parse_market_date(slug: str) -> str | None:
    """Extract YYYY-MM-DD from a weather slug.  Returns ISO date or None."""
    if not slug:
        return None
    # Slugs look like '...-on-april-3', '...-on-may-16-2026'
    m = re.search(r"-on-([a-z]+)-(\d+)(?:-(\d{4}))?", slug.lower())
    if not m:
        return None
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
        "june": 6, "july": 7, "august": 8, "september": 9,
        "october": 10, "november": 11, "december": 12,
    }
    mon = months.get(m.group(1))
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else datetime.now().year
    if not mon:
        return None
    try:
        return f"{year:04d}-{mon:02d}-{day:02d}"
    except Exception:
        return None


# ── Polymarket API ────────────────────────────────────────────────────────

def _fetch_positions(wallet: str) -> list[dict]:
    """All open positions for a wallet on Polymarket data-api."""
    try:
        r = requests.get(
            f"{DATA_API}/positions",
            params={"user": wallet},
            timeout=20,
        )
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        log.warning(f"positions fetch failed for {wallet[:10]}…: {e}")
        return []


def _qualifies_for_blacklist(pos: dict) -> bool:
    """Apply the threshold rule + sanity filters.

    A position qualifies if all of:
      * outcome name is "No"            (we only blacklist NO-side bets)
      * avgPrice >= BLACKLIST_PRICE     (high-conviction)
      * curPrice >= 0.50                (still believed by the market)
      * market hasn't already resolved (we want active bets)
      * trader still actually holds the size (size > 0)
    """
    if pos.get("outcome") != "No":
        return False
    avg = float(pos.get("avgPrice") or 0)
    if avg < BLACKLIST_PRICE:
        return False
    cur = float(pos.get("curPrice") or 0)
    if cur < 0.50:
        return False
    if float(pos.get("size") or 0) <= 0:
        return False
    # `redeemable` is True once the market resolved; skip those — they're history
    if pos.get("redeemable") is True:
        return False
    return True


# ── Main sync ─────────────────────────────────────────────────────────────

def main() -> int:
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    total_seen   = 0
    total_kept   = 0
    upserted_ids: set[str] = set()

    for wallet, label in TRACKED_TRADERS:
        positions = _fetch_positions(wallet)
        log.info(f"{label}: {len(positions)} open positions on Polymarket")
        total_seen += len(positions)

        for pos in positions:
            if not _qualifies_for_blacklist(pos):
                continue
            cid = (pos.get("conditionId") or "").lower()
            if not cid:
                continue
            question = pos.get("title", "")
            slug     = pos.get("slug", "")
            city, bracket, _date_str = _parse_question(question)
            mkt_date = _parse_market_date(slug)

            row = {
                "condition_id":       cid,
                "market_question":    question,
                "city":               city.lower() if city else None,
                "bracket_label":      bracket or None,
                "market_date":        mkt_date,
                "source_wallet":      wallet,
                "source_label":       label,
                "source_side":        "NO",
                "source_price":       round(float(pos.get("avgPrice") or 0), 4),
                "source_size_tokens": round(float(pos.get("size") or 0), 4),
                "source_cost_usd":    round(float(pos.get("initialValue") or 0), 2),
                "last_confirmed_at":  datetime.now(timezone.utc).isoformat(),
                "reason":             (
                    f"{label} holds NO @ ${float(pos.get('avgPrice',0)):.3f} "
                    f"(${float(pos.get('initialValue',0)):.0f} cost); 99.7% win-rate "
                    f"history on this pattern."
                ),
            }
            try:
                sb.table("bracket_blacklist").upsert(row, on_conflict="condition_id").execute()
                upserted_ids.add(cid)
                total_kept += 1
            except Exception as e:
                log.warning(f"upsert failed for {cid[:10]}…: {e}")

    # Prune rows that no longer appear in any tracker's positions (positions
    # closed, resolved, or sold).  Only prune ones for cities/markets that
    # have passed their resolution date — we don't want flapping for active
    # markets where a tracker briefly de-risked.
    try:
        all_rows = (sb.table("bracket_blacklist")
                    .select("condition_id, market_date")
                    .execute()).data or []
        from datetime import date as _date
        today = _date.today()
        stale_ids = []
        for row in all_rows:
            cid = (row.get("condition_id") or "").lower()
            md_str = row.get("market_date")
            md = _date.fromisoformat(md_str) if md_str else None
            # Prune if (a) we didn't see this position this cycle AND
            # (b) the market date has passed (so we know the trade is over)
            if cid not in upserted_ids and md is not None and md < today:
                stale_ids.append(cid)
        if stale_ids:
            for chunk_start in range(0, len(stale_ids), 100):
                chunk = stale_ids[chunk_start:chunk_start + 100]
                sb.table("bracket_blacklist").delete().in_("condition_id", chunk).execute()
            log.info(f"pruned {len(stale_ids)} expired blacklist rows")
    except Exception as e:
        log.warning(f"prune failed: {e}")

    log.info(f"done: kept {total_kept}/{total_seen} positions across "
             f"{len(TRACKED_TRADERS)} tracked traders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
