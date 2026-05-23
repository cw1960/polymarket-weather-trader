"""For each city that had a 100%-NO loss yesterday, compare:
  (A) Our current bot reading (running_max_c from temp_readings) — the value
      that drove the bad decision
  (B) calendarDayTemperatureMax NOW (v3/forecast/daily/5day, gridded geocode)
  (C) Max of hourly observations from v1/location/.../historical.json
      (the actual airport STATION — what WU's history page displays)
  (D) Polymarket's resolved winning bracket midpoint (= the value the market
      was actually settled against)

If C ≈ D and A != D, we've found the bug: we're using a gridded
calendarDayTemperatureMax that doesn't match WU's airport-station history.
The fix would be to switch the bot's reads to fetch_daily_max_c (hourly-obs
based) instead of fetch_calendar_day_max_c (gridded-forecast based).
"""
import os, re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
import sys
sys.path.insert(0, "/root/polymarket/scripts")

from supabase import create_client
import wunderground  # has fetch_daily_max_c (hourly-obs) and fetch_calendar_day_max_c (gridded)

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

US = {"NYC","Chicago","Miami","Los Angeles","Dallas","Atlanta","Houston","Austin","Seattle","San Francisco","Denver"}


def native(c, city):
    if c is None: return "  ?  "
    if city in US: return f"{round(c * 9 / 5 + 32):3d}°F"
    return f"{round(c):3d}°C"


def winner_mid_c(question):
    q = (question or "").lower()
    m = re.search(r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°([fc])", q)
    if m:
        unit = m.group(3).upper()
        a, b = float(m.group(1)), float(m.group(2))
        mid = (a + b) / 2
        return (mid - 32) * 5 / 9 if unit == "F" else mid
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)\s*°([fc])", q)
    if m:
        unit = m.group(2).upper()
        v = float(m.group(1))
        return (v - 32) * 5 / 9 if unit == "F" else v
    return None


SINCE = "2026-05-21T19:31:00+00:00"
r = (sb.table("trade_signals")
     .select("city,forecast_date,outcome,model_probability,winning_bracket,actual_outcome,market_price")
     .eq("signal_phase", "phase2_sweep")
     .gte("signal_time", SINCE)
     .not_.is_("winning_bracket", "null")
     .order("signal_time")
     .execute())

# Focus on the 100%-NO losses — that's where the precision bug bites hardest
print(f"{'CITY':14s} {'DATE':10s} {'BRACKET':9s} | {'(A) bot':9s} {'(B) cal':9s} {'(C) hist':9s} {'(D) WU':9s} | {'A vs D':9s} {'B vs D':9s} {'C vs D':9s}")
print("-" * 130)

gaps_calendar = []   # (calendarDayTemperatureMax) - (WU resolution)
gaps_history  = []   # (hourly_obs_max) - (WU resolution)
gaps_bot      = []   # (bot's stored running_max) - (WU resolution)
for x in r.data:
    won = str(x.get("actual_outcome", "")) == "false"
    model_prob = x.get("model_probability") or 0
    if won or model_prob < 0.95:
        continue   # only inspect 100%-NO losses
    city = x["city"]; fdate = x["forecast_date"]
    # (A) what the bot's temp_readings says
    tr = sb.table("temp_readings").select("running_max_c").eq("city", city).eq("reading_date", fdate).limit(1).execute()
    bot_max = float(tr.data[0]["running_max_c"]) if tr.data else None
    # (B) what calendarDayTemperatureMax returns NOW
    try:
        cal_max = wunderground.fetch_calendar_day_max_c(city)
    except Exception:
        cal_max = None
    # (C) what hourly-obs max returns for this historical date
    try:
        hist_max = wunderground.fetch_daily_max_c(city, fdate)
    except Exception:
        hist_max = None
    # (D) Polymarket winner bracket midpoint
    wu_target = winner_mid_c(x.get("winning_bracket", ""))

    if bot_max is not None and wu_target is not None: gaps_bot.append(bot_max - wu_target)
    if cal_max is not None and wu_target is not None: gaps_calendar.append(cal_max - wu_target)
    if hist_max is not None and wu_target is not None: gaps_history.append(hist_max - wu_target)

    def delta(v):
        if v is None or wu_target is None: return "      ?"
        d = v - wu_target
        if city in US: d = d * 9 / 5
        return f"{d:+5.1f}{'F' if city in US else 'C'}"

    print(f"{city:14s} {fdate} {x.get('outcome',''):9s} | "
          f"{native(bot_max, city):9s} {native(cal_max, city):9s} {native(hist_max, city):9s} {native(wu_target, city):9s} | "
          f"{delta(bot_max):9s} {delta(cal_max):9s} {delta(hist_max):9s}")

import statistics
print()
print("AGGREGATE GAPS (positive = source reads HIGHER than Polymarket's resolution):")
if gaps_bot:      print(f"  (A) bot's running_max:          mean {statistics.mean(gaps_bot):+.2f}°C  median {statistics.median(gaps_bot):+.2f}°C  n={len(gaps_bot)}")
if gaps_calendar: print(f"  (B) calendarDayTemperatureMax:  mean {statistics.mean(gaps_calendar):+.2f}°C  median {statistics.median(gaps_calendar):+.2f}°C  n={len(gaps_calendar)}")
if gaps_history:  print(f"  (C) hourly-obs max (station):   mean {statistics.mean(gaps_history):+.2f}°C  median {statistics.median(gaps_history):+.2f}°C  n={len(gaps_history)}")
print()
print("If (C) ≈ 0 and (A,B) > 0, the fix is to switch to fetch_daily_max_c (hourly-obs).")
