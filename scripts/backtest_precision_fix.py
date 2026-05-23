"""For every losing paper trade since the intraday fix, replay the NO sweep
decision using the CORRECTED running_max source (hourly station obs).
Tally how many losses would have been prevented and what the net P&L
would look like.
"""
import os, re, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, "/root/polymarket/scripts")

from supabase import create_client
import wunderground

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

US = {"NYC","Chicago","Miami","Los Angeles","Dallas","Atlanta","Houston","Austin","Seattle","San Francisco","Denver"}
SINCE = "2026-05-21T19:31:00+00:00"
NEW_EDGE = 0.08
NEW_MIN_PROB = 0.55
BUFFER_C = 1.0


def winner_mid_c(question):
    q = (question or "").lower()
    m = re.search(r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°([fc])", q)
    if m:
        u = m.group(3).upper(); a,b = float(m.group(1)), float(m.group(2))
        mid = (a+b)/2
        return (mid-32)*5/9 if u=="F" else mid
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)\s*°([fc])", q)
    if m:
        u = m.group(2).upper(); v = float(m.group(1))
        return (v-32)*5/9 if u=="F" else v
    return None


def bracket_bounds_c(label):
    s = (label or "").strip()
    m = re.match(r"^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)°([FC])$", s)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        u = m.group(3)
        if u == "F": return ((lo - 0.5 - 32)*5/9, (hi + 0.5 - 32)*5/9)
        return (lo - 0.5, hi + 0.5)
    m = re.match(r"^≤(-?\d+(?:\.\d+)?)°([FC])$", s)
    if m:
        hi = float(m.group(1)); u = m.group(2)
        return (float("-inf"), (hi + 0.5 - 32)*5/9 if u=="F" else hi + 0.5)
    m = re.match(r"^≥(-?\d+(?:\.\d+)?)°([FC])$", s)
    if m:
        lo = float(m.group(1)); u = m.group(2)
        return ((lo - 0.5 - 32)*5/9 if u=="F" else lo - 0.5, float("inf"))
    m = re.match(r"^(-?\d+(?:\.\d+)?)°([FC])$", s)
    if m:
        v = float(m.group(1)); u = m.group(2)
        if u == "F": return ((v - 0.5 - 32)*5/9, (v + 0.5 - 32)*5/9)
        return (v - 0.5, v + 0.5)
    return None


r = (sb.table("trade_signals")
     .select("city,forecast_date,outcome,market_price,model_probability,winning_bracket,actual_outcome")
     .eq("signal_phase", "phase2_sweep")
     .gte("signal_time", SINCE)
     .not_.is_("winning_bracket", "null")
     .order("signal_time")
     .execute())

print(f"Re-evaluating {len(r.data)} resolved trades with corrected running_max source")
print()
print(f"{'CITY':14s} {'BRACKET':9s} {'OLD_RMAX':8s} {'NEW_RMAX':8s} {'BRACKET':16s} {'OLD_DECISION':14s} {'NEW_DECISION':16s} {'RESULT':6s} {'OLD_PNL':8s} {'NEW_PNL':8s}")
print("-"*135)

old_pnl_total = 0
new_pnl_total = 0
recovered = 0   # losing trades the fix would have skipped
for x in r.data:
    city = x["city"]; fdate = x["forecast_date"]
    bracket_label = x["outcome"]
    bounds = bracket_bounds_c(bracket_label)
    if not bounds: continue
    low_c, high_c = bounds

    # OLD reading: the running_max_c stored in temp_readings
    tr = sb.table("temp_readings").select("running_max_c").eq("city", city).eq("reading_date", fdate).limit(1).execute()
    old_rmax = float(tr.data[0]["running_max_c"]) if tr.data else None
    # NEW reading: hourly-obs max for that historical date
    try:
        new_rmax = wunderground.fetch_daily_max_c(city, fdate)
    except Exception:
        new_rmax = None

    # Did this trade win?
    won = str(x.get("actual_outcome","")) == "false"
    p = x.get("market_price") or 0
    old_pnl = (5*(1-p)/p if won else -5) if 0<p<1 else 0

    # Would the new logic fire this trade?
    # The fix: if 0 < (new_rmax - high_c) < BUFFER_C, SKIP.
    # If high_c is float('inf'), no skip (the ≥ tail bracket has no top).
    # If new_rmax is below high_c → bracket alive, decision depends on probabilities (which we don't recompute here — keep model_prob as-is)
    if new_rmax is None or high_c == float("inf"):
        new_decision = "fire (unchanged)"
        new_pnl = old_pnl
    else:
        margin = new_rmax - high_c
        if 0 < margin < BUFFER_C:
            new_decision = "SKIP (buffer)"
            new_pnl = 0
            if not won: recovered += 1
        elif new_rmax > high_c + BUFFER_C:
            # comfortably past — fire would still happen, but with better probability calibration
            new_decision = "fire (safely dead)"
            new_pnl = old_pnl
        else:
            # running_max BELOW bracket high — bracket still alive
            # The OLD logic fired here because old_rmax was higher than the new value.
            # The new logic might NOT have fired this bracket if running_max is now too low
            # to clear the "dead" threshold AND too low to qualify as a sweep target.
            # For a NO sweep, we want bracket far enough above running_max that
            # member-counting still gives high prob_no.
            # Without recomputing probabilities, just flag: this is the ambiguous zone.
            new_decision = "AMBIGUOUS"
            new_pnl = old_pnl   # conservatively assume same trade fires

    old_pnl_total += old_pnl
    new_pnl_total += new_pnl

    def native(c):
        if c is None: return " ?   "
        if c == float("inf"): return " +inf"
        if c == float("-inf"): return " -inf"
        if city in US: return f"{round(c*9/5+32):3d}°F"
        return f"{round(c):3d}°C"

    print(f"{city:14s} {bracket_label:9s} {native(old_rmax):8s} {native(new_rmax):8s} "
          f"{('[' + native(low_c) + ',' + native(high_c) + ']'):16s} "
          f"{'fire (lost)' if not won else 'fire (won)':14s} {new_decision:16s} "
          f"{('WON' if won else 'LOST'):6s} "
          f"{old_pnl:+7.2f}  {new_pnl:+7.2f}")

print()
print(f"OLD total P&L: ${old_pnl_total:+.2f}")
print(f"NEW total P&L: ${new_pnl_total:+.2f}")
print(f"Δ = {new_pnl_total - old_pnl_total:+.2f}  (losing trades skipped by buffer: {recovered})")
print()
print("NB: New decisions marked 'fire (safely dead)' or 'AMBIGUOUS' assume same trade fires.")
print("    Real production behavior with new probabilities may further filter these via the gate.")
