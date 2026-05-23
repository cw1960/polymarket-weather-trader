"""Quick state check: what does today's data look like post-precision-fix?"""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
from supabase import create_client

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

# 1) Current temp_readings on 5/22
print("=== Current temp_readings for 5/22 (top 15 by most-recent obs) ===")
r = sb.table("temp_readings").select("city,running_max_c,temp_c,observed_at,local_hour").eq("reading_date","2026-05-22").order("observed_at", desc=True).limit(15).execute()
for x in r.data:
    print(f"  {x['city']:14s}  rmax_c={x['running_max_c']!s:6s}  temp_c={x['temp_c']!s:6s}  observed_at={str(x['observed_at'])[:19]}  local_hour={x['local_hour']}")

# 2) Today's phase2_sweep trades, split by before/after the precision fix
print()
print("=== Today's phase2_sweep trades, split by precision-fix deploy (~17:00 UTC) ===")
r = sb.table("trade_signals").select("signal_time,city,outcome,market_price,model_probability,winning_bracket,actual_outcome").eq("signal_phase","phase2_sweep").gte("signal_time","2026-05-22T00:00:00").order("signal_time").execute()
pre  = [x for x in r.data if str(x.get("signal_time","")) <  "2026-05-22T17:00:00"]
post = [x for x in r.data if str(x.get("signal_time","")) >= "2026-05-22T17:00:00"]

def summarize(rows, label):
    resolved = [x for x in rows if x.get("winning_bracket")]
    wins = sum(1 for x in resolved if str(x.get("actual_outcome",""))=="false")
    sim_pnl = 0.0
    for x in resolved:
        p = x.get("market_price") or 0
        won = str(x.get("actual_outcome",""))=="false"
        if 0 < p < 1:
            sim_pnl += 5*(1-p)/p if won else -5
    print(f"  {label}: n={len(rows)}  resolved={len(resolved)}  wins={wins}  "
          f"WR={(wins/len(resolved)*100 if resolved else 0):.0f}%  sim_pnl @ $5 = ${sim_pnl:+.2f}")

summarize(pre, "PRE  (<17:00 UTC, old gridded reading)")
summarize(post, "POST (≥17:00 UTC, hourly-obs reading)")

print()
print("=== Post-fix trade details ===")
for x in post:
    p = x.get("market_price") or 0
    mp = x.get("model_probability") or 0
    won_s = "?"
    if x.get("winning_bracket"):
        won_s = "WON" if str(x.get("actual_outcome",""))=="false" else "LOST"
    print(f"  {str(x['signal_time'])[:19]} {x['city']:14s} NO {x['outcome']:9s}  no_p={p:.3f}  m_no={mp:.2f}  {won_s}")
