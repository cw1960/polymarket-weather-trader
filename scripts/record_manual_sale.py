"""
Record a manual sale of a position.
====================================

When you sell a position via the Polymarket UI (rather than letting it
resolve), the trade_signals row stays with pnl_usd=NULL and the dashboard
keeps showing it as an open position.  Run this script after each manual
exit to keep the DB and dashboard in sync with reality.

Usage:
    python3 scripts/record_manual_sale.py CITY OUTCOME PROCEEDS [--date YYYY-MM-DD]

Examples:
    # Sold Madrid 21°C for $79.68 today
    python3 scripts/record_manual_sale.py Madrid 21°C 79.68

    # Sold half of Madrid yesterday
    python3 scripts/record_manual_sale.py Madrid 21°C 74.70 --date 2026-05-14

The script:
  1. Finds the matching trade_signals row (city + outcome + date + real-money)
  2. Computes pnl = proceeds − recommended_position (the original $ cost)
  3. Writes pnl_usd and sets order_status='sold'

If you sold the position in two halves on the same day (like Madrid 1st/2nd half),
add up both proceeds amounts into a single PROCEEDS value and run once.
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY


def main() -> int:
    ap = argparse.ArgumentParser(description="Record a manual position sale")
    ap.add_argument("city",     help="City name, e.g. Madrid")
    ap.add_argument("outcome",  help="Bracket label, e.g. 21°C")
    ap.add_argument("proceeds", type=float, help="Total $ received from the sale (sum if multiple partial sells)")
    ap.add_argument("--date",   default=date.today().isoformat(), help="forecast_date (default: today UTC)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would happen, don't write")
    args = ap.parse_args()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = (sb.table("trade_signals")
           .select("id, city, outcome, recommended_position, order_status, pnl_usd, fill_price, market_price")
           .eq("city", args.city)
           .eq("outcome", args.outcome)
           .eq("forecast_date", args.date)
           .eq("signal_phase", "phase2")
           .gt("recommended_position", 1)
           .execute()).data

    if not res:
        print(f"No matching real-money phase2 row for {args.city} {args.outcome} on {args.date}")
        return 1
    if len(res) > 1:
        print(f"Found {len(res)} matching rows; aborting. Specify date more narrowly.")
        return 1

    row     = res[0]
    cost    = float(row["recommended_position"])
    pnl     = round(args.proceeds - cost, 2)

    print(f"Found row id={row['id']}")
    print(f"  cost (recommended_position): ${cost:.2f}")
    print(f"  proceeds (from sale):        ${args.proceeds:.2f}")
    print(f"  P&L:                         ${pnl:+.2f}")
    print(f"  current order_status:        {row['order_status']}")
    print(f"  current pnl_usd:             {row['pnl_usd']}")
    if args.dry_run:
        print("[dry-run] no changes written")
        return 0

    sb.table("trade_signals").update({
        "pnl_usd":      pnl,
        "order_status": "sold",
    }).eq("id", row["id"]).execute()
    print(f"Updated row {row['id'][:8]}: order_status=sold, pnl_usd={pnl:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
