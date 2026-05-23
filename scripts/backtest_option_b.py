"""Backtest Option B: re-evaluate the EXACT trades the bot fired,
using the corrected running_max source. Same brackets, same recorded
market prices (the pre-resolution no_price the bot saw at trade time),
but with new prob_no computed from corrected-precision intraday filtering.

Question answered: of the trades that DID fire, how many would still
fire with the precision fix? And what's the net P&L on the survivors?
"""
import os, json, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, "/root/polymarket/scripts")

from supabase import create_client
import wunderground

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

NEW_EDGE = 0.08
NEW_MIN_PROB = 0.55
SIM_SIZE = 5.0
US = {"NYC","Chicago","Miami","Los Angeles","Dallas","Atlanta","Houston","Austin","Seattle","San Francisco","Denver"}


def f_to_c(f): return (f - 32.0) * 5.0 / 9.0


def native(c, city):
    if c is None or c == float("inf") or c == float("-inf"): return "  ?  "
    if city in US: return f"{round(c*9/5+32):3d}°F"
    return f"{round(c):3d}°C"


# Pull all resolved post-fix sweep trades + needed fields
since = "2026-05-21T19:31:00+00:00"
r = (sb.table("trade_signals")
     .select("city,forecast_date,outcome,market_price,model_probability,winning_bracket,actual_outcome")
     .eq("signal_phase", "phase2_sweep")
     .gte("signal_time", since)
     .not_.is_("winning_bracket", "null")
     .order("signal_time")
     .execute())

trades = r.data
print(f"Re-evaluating {len(trades)} trades with corrected running_max + recorded market prices\n")

# For each (city, date), cache: corrected_rmax + ensemble members + ladder buckets
cell_cache = {}

def get_cell_data(city, fdate):
    key = (city, fdate)
    if key in cell_cache: return cell_cache[key]
    corrected_rmax = wunderground.fetch_daily_max_c(city, fdate)
    ef = (sb.table("ensemble_forecasts").select("raw_members,ecmwf_members")
          .eq("city", city).eq("forecast_date", fdate)
          .order("created_at", desc=True).limit(1).execute())
    members = []
    if ef.data:
        members = [float(m) for m in (ef.data[0].get("raw_members") or [])] + \
                  [float(m) for m in (ef.data[0].get("ecmwf_members") or [])]
    lr = (sb.table("ladders").select("buckets_json")
          .eq("city", city).eq("forecast_date", fdate)
          .order("created_at", desc=True).limit(1).execute())
    buckets = []
    if lr.data and lr.data[0].get("buckets_json"):
        buckets = json.loads(lr.data[0]["buckets_json"])
    cell_cache[key] = (corrected_rmax, members, buckets)
    return cell_cache[key]


print(f"{'CITY':14s} {'BRACKET':9s} {'OLD_NO_PRICE':12s} {'OLD_M_NO':9s} {'OLD_FIRED':10s} "
      f"{'NEW_RMAX':9s} {'NEW_M_NO':9s} {'NEW_EDGE':9s} {'NEW_DECISION':14s} {'WON?':5s} {'OLD_PNL':>8s} {'NEW_PNL':>8s}")
print("-" * 145)

old_total_pnl = 0
new_total_pnl = 0
old_fires = 0
new_fires = 0
new_wins = 0
old_wins = 0

for t in trades:
    city = t["city"]; fdate = t["forecast_date"]; bracket_label = t["outcome"]
    no_price = t.get("market_price") or 0   # recorded at trade time (pre-resolution)
    old_m_no = t.get("model_probability") or 0
    won = str(t.get("actual_outcome","")) == "false"

    # OLD result (what actually happened live):
    old_fires += 1
    if won: old_wins += 1
    old_pnl = (SIM_SIZE * (1 - no_price) / no_price if won else -SIM_SIZE) if 0 < no_price < 1 else 0
    old_total_pnl += old_pnl

    # NEW computation:
    corrected_rmax, members, buckets = get_cell_data(city, fdate)
    if corrected_rmax is None or not members or not buckets:
        new_decision = "NO_DATA"
        new_m_no = 0; new_edge = 0; new_pnl = 0
    else:
        # Find bucket bounds in °C
        bucket = next((b for b in buckets if b.get("label") == bracket_label), None)
        if not bucket:
            new_decision = "NO_BUCKET"
            new_m_no = 0; new_edge = 0; new_pnl = 0
        else:
            low_c = float(bucket.get("low", -9999))
            high_c = float(bucket.get("high", 9999))
            if bucket.get("unit") == "F":
                low_c = f_to_c(low_c); high_c = f_to_c(high_c)
            filtered = [m for m in members if m >= corrected_rmax]
            if len(filtered) < 8:
                new_decision = "SPARSE_SKIP"
                new_m_no = 0; new_edge = 0; new_pnl = 0
            else:
                count_in = sum(1 for m in filtered if low_c <= m <= high_c)
                new_p_yes = count_in / len(filtered)
                new_m_no = 1.0 - new_p_yes
                new_edge = new_m_no - no_price
                if new_m_no < NEW_MIN_PROB:
                    new_decision = "SKIP_low_prob"
                    new_pnl = 0
                elif new_edge < NEW_EDGE:
                    new_decision = "SKIP_low_edge"
                    new_pnl = 0
                else:
                    new_decision = "FIRE"
                    new_fires += 1
                    if won: new_wins += 1
                    new_pnl = (SIM_SIZE * (1 - no_price) / no_price if won else -SIM_SIZE) if 0 < no_price < 1 else 0
    new_total_pnl += new_pnl

    print(f"{city:14s} {bracket_label:9s} {no_price:>11.3f}  {old_m_no:>8.2f}  "
          f"{'fire':>10s}  {native(corrected_rmax, city):>9s} {new_m_no:>8.2f}  "
          f"{new_edge:>+8.3f}  {new_decision:14s} {'WON' if won else 'LOST':5s} "
          f"{old_pnl:>+7.2f}  {new_pnl:>+7.2f}")

print()
print(f"=== SUMMARY ===")
print(f"  OLD: fired {old_fires}, wins {old_wins} ({old_wins/old_fires*100:.0f}%), P&L $5/trade ${old_total_pnl:+.2f}")
print(f"  NEW: fired {new_fires}, wins {new_wins} ({(new_wins/new_fires*100 if new_fires else 0):.0f}%), P&L $5/trade ${new_total_pnl:+.2f}")
print(f"  Δ trades skipped: {old_fires - new_fires}")
print(f"  Δ P&L: ${new_total_pnl - old_total_pnl:+.2f}")
