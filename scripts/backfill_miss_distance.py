"""Backfill miss_distance_c for existing resolved Phase 2 trades.
Run after migrate_miss_distance.sql has been applied."""
import re
import sys
sys.path.insert(0, '.')
from config import SUPABASE_URL, SUPABASE_KEY
from supabase import create_client
from resolver import _bracket_midpoint_c

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

res = (sb.table("trade_signals")
       .select("id, outcome, winning_bracket, signal_phase")
       .eq("signal_phase", "phase2")
       .not_.is_("pnl_usd", "null")
       .limit(500)
       .execute())

updated = 0
skipped = 0
for r in res.data:
    bet  = _bracket_midpoint_c(r.get("outcome") or "")
    win  = _bracket_midpoint_c(r.get("winning_bracket") or "")
    if bet is None or win is None:
        skipped += 1
        continue
    miss = round(abs(bet - win), 2)
    try:
        sb.table("trade_signals").update({"miss_distance_c": miss}).eq("id", r["id"]).execute()
        updated += 1
    except Exception as e:
        print(f"Error updating {r['id']}: {e}")
        sys.exit(1)

print(f"Backfilled {updated} trades, skipped {skipped} (tail brackets / unparseable)")

# Show distribution
res2 = (sb.table("trade_signals")
        .select("miss_distance_c, recommended_position, pnl_usd")
        .eq("signal_phase", "phase2")
        .not_.is_("miss_distance_c", "null")
        .limit(500)
        .execute())

real = [r for r in res2.data if float(r.get("recommended_position") or 0) > 1]
from collections import Counter
dist_counter = Counter(float(r["miss_distance_c"]) for r in real)
print(f"\nReal-money trade miss distribution ({len(real)} trades):")
for d in sorted(dist_counter.keys()):
    n = dist_counter[d]
    pnl = sum(float(r["pnl_usd"]) for r in real if float(r["miss_distance_c"]) == d)
    print(f"  miss={d:>4}°C  n={n:2}  total PnL=${pnl:+.2f}")

avg_miss = sum(float(r["miss_distance_c"]) for r in real) / len(real) if real else 0
print(f"\nAverage miss distance (real trades): {avg_miss:.2f}°C")
