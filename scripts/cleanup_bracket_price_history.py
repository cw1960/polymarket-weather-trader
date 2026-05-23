"""
cleanup_bracket_price_history.py — daily prune of the live Trader-app
collector table. Keeps only the most recent 48 hours of rows; everything
older is dropped IN SMALL BATCHES so we don't trigger long row-locks or
exceed PostgREST's request timeout on a very large table.

Cron:
  10 4 * * * cd /root/polymarket && venv/bin/python3 scripts/cleanup_bracket_price_history.py >> logs/cleanup_bph.log 2>&1
"""
from __future__ import annotations
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
from supabase import create_client  # noqa: E402

KEEP_HOURS = 48
BATCH_SIZE = 2000              # rows per delete request

sb = create_client(
    os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

log = logging.getLogger("cleanup")
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S UTC")
logging.Formatter.converter = time.gmtime


def main():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=KEEP_HOURS)).isoformat()
    log.info(f"deleting bracket_price_history rows older than {cutoff} (batch={BATCH_SIZE})")
    total_deleted = 0
    iters = 0
    MAX_ITERS = 5000     # safety cap (~10M rows max per run)
    while iters < MAX_ITERS:
        iters += 1
        try:
            # Get a batch of IDs to delete
            sel = (sb.table("bracket_price_history")
                   .select("id")
                   .lt("recorded_at", cutoff)
                   .order("id", desc=False)
                   .limit(BATCH_SIZE)
                   .execute())
            ids = [r["id"] for r in (sel.data or [])]
            if not ids:
                break
            sb.table("bracket_price_history").delete().in_("id", ids).execute()
            total_deleted += len(ids)
            if total_deleted % 20000 < BATCH_SIZE:
                log.info(f"  ...deleted {total_deleted} so far")
            # Be polite to the DB — short sleep between batches reduces IO spike
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"batch {iters} failed (will retry): {e}")
            time.sleep(2)
    log.info(f"done. total deleted = {total_deleted} in {iters} batches")


if __name__ == "__main__":
    main()
