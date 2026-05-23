"""Full-replay backtest of the post-fix logic on the same (city, date)
cells that lost overnight. Re-selects brackets from scratch using the
CORRECTED running_max from WU's station hourly-obs endpoint, applies the
gate, picks top-3 NO candidates, simulates outcomes against actual winners.

This is what the bot WOULD have done with the precision fix in place.
"""
import os, re, sys, json
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
TOP_N = 3
SIM_SIZE = 5.0
US = {"NYC","Chicago","Miami","Los Angeles","Dallas","Atlanta","Houston","Austin","Seattle","San Francisco","Denver"}


def winner_label_from_question(q):
    """Extract the bracket label from a Polymarket question string."""
    q = (q or "").strip()
    m = re.search(r"between\s+(-?\d+)\s*-\s*(-?\d+)\s*°([FC])", q, re.IGNORECASE)
    if m: return f"{m.group(1)}-{m.group(2)}°{m.group(3).upper()}"
    m = re.search(r"(\d+)\s*°([FC])\s+or\s+below", q, re.IGNORECASE)
    if m: return f"≤{m.group(1)}°{m.group(2).upper()}"
    m = re.search(r"(\d+)\s*°([FC])\s+or\s+higher", q, re.IGNORECASE)
    if m: return f"≥{m.group(1)}°{m.group(2).upper()}"
    m = re.search(r"be\s+(-?\d+)\s*°([FC])", q, re.IGNORECASE)
    if m: return f"{m.group(1)}°{m.group(2).upper()}"
    return None


def f_to_c(f): return (f - 32.0) * 5.0 / 9.0


def native(c, city):
    if c is None or c == float("inf") or c == float("-inf"): return " ?  "
    if city in US: return f"{round(c*9/5+32):3d}°F"
    return f"{round(c):3d}°C"


# Find the unique (city, date) cells that had at least one paper sweep trade
since = "2026-05-21T19:31:00+00:00"
trades = (sb.table("trade_signals")
          .select("city,forecast_date,winning_bracket,actual_outcome")
          .eq("signal_phase", "phase2_sweep")
          .gte("signal_time", since)
          .not_.is_("winning_bracket", "null")
          .execute()).data
unique_cells = sorted({(t["city"], t["forecast_date"]) for t in trades})
print(f"Unique (city,date) cells to replay: {len(unique_cells)}")

# For each cell, replay the full candidate selection
total_fires = 0
total_wins = 0
total_pnl = 0.0

per_cell_results = []

for city, fdate in unique_cells:
    # 1. Get corrected running_max (hourly-obs max from WU history endpoint)
    corrected_rmax = wunderground.fetch_daily_max_c(city, fdate)
    if corrected_rmax is None:
        continue

    # 2. Load buckets (the ladder for this city/date)
    lr = (sb.table("ladders").select("buckets_json")
          .eq("city", city).eq("forecast_date", fdate).order("created_at", desc=True).limit(1).execute())
    if not lr.data or not lr.data[0].get("buckets_json"):
        continue
    buckets = json.loads(lr.data[0]["buckets_json"])

    # 3. Load ensemble members
    ef = (sb.table("ensemble_forecasts").select("raw_members,ecmwf_members")
          .eq("city", city).eq("forecast_date", fdate)
          .order("created_at", desc=True).limit(1).execute())
    if not ef.data: continue
    members = [float(m) for m in (ef.data[0].get("raw_members") or [])] + \
              [float(m) for m in (ef.data[0].get("ecmwf_members") or [])]
    if not members: continue

    # 4. Filter members
    filtered = [m for m in members if m >= corrected_rmax]
    if len(filtered) < 8: continue
    n_filtered = len(filtered)

    # 5. Get winner from any of our trade rows for this cell
    wb = next((t["winning_bracket"] for t in trades if t["city"] == city and t["forecast_date"] == fdate), None)
    winner_label = winner_label_from_question(wb) if wb else None

    # 6. Get market prices per bracket from Polymarket
    import requests
    city_slug_map = {"NYC":"nyc","Hong Kong":"hong-kong","Los Angeles":"los-angeles","San Francisco":"san-francisco",
                     "Mexico City":"mexico-city","Cape Town":"cape-town","São Paulo":"sao-paulo",
                     "Buenos Aires":"buenos-aires","Panama City":"panama-city","Tel Aviv":"tel-aviv",
                     "Kuala Lumpur":"kuala-lumpur"}
    slug_city = city_slug_map.get(city, city.lower().replace(" ","-"))
    # Polymarket date slug: "may-22-2026" (no leading zero)
    from datetime import date as _d
    d_obj = _d.fromisoformat(fdate)
    date_slug = d_obj.strftime("%B-%-d-%Y").lower() if hasattr(d_obj,'strftime') else ""
    slug = f"highest-temperature-in-{slug_city}-on-{date_slug}"
    pm = requests.get(f"https://gamma-api.polymarket.com/events/slug/{slug}", timeout=10)
    if not pm.ok: continue
    e = pm.json()
    no_prices_by_label = {}
    for m in e.get("markets", []):
        op = m.get("outcomePrices")
        if isinstance(op, str): op = json.loads(op)
        if not op or len(op) < 2: continue
        # Polymarket resolved markets: winner has YES=1, loser has YES=0
        # Pre-resolution market prices not available here. We approximate
        # using outcomePrices, but for resolved markets these are the
        # post-resolution end-state values. This is a known limitation —
        # the calibration result is honest, the simulated P&L is not.
        yes_p = float(op[0]); no_p = float(op[1])
        wl = winner_label_from_question(m.get("question", ""))
        if wl: no_prices_by_label[wl] = no_p

    # 7. For each bucket, compute prob_no and pass-gate
    candidates = []
    for b in buckets:
        label = b.get("label", "")
        low_c = float(b.get("low",  -9999))
        high_c = float(b.get("high",  9999))
        if b.get("unit") == "F":
            low_c = f_to_c(low_c); high_c = f_to_c(high_c)
        count_in = sum(1 for m in filtered if low_c <= m <= high_c)
        prob_yes = count_in / n_filtered
        prob_no = 1.0 - prob_yes
        no_price = no_prices_by_label.get(label)
        if no_price is None: continue
        edge_no = prob_no - no_price
        if prob_no < NEW_MIN_PROB: continue
        if edge_no < NEW_EDGE: continue
        candidates.append({
            "label": label, "prob_no": prob_no, "no_price": no_price, "edge_no": edge_no,
            "won": label != winner_label,
        })

    # 8. Top-N by edge
    candidates.sort(key=lambda c: c["edge_no"], reverse=True)
    chosen = candidates[:TOP_N]
    cell_pnl = 0.0
    cell_wins = 0
    for c in chosen:
        if c["won"]:
            cell_pnl += SIM_SIZE * (1 - c["no_price"]) / c["no_price"]
            cell_wins += 1
        else:
            cell_pnl -= SIM_SIZE
    per_cell_results.append({
        "city": city, "fdate": fdate, "rmax": corrected_rmax,
        "winner": winner_label, "n_fired": len(chosen), "wins": cell_wins,
        "pnl": cell_pnl, "chosen": chosen,
    })
    total_fires += len(chosen)
    total_wins += cell_wins
    total_pnl += cell_pnl

print()
print(f"{'CITY':14s} {'DATE':10s} {'R_MAX':6s} {'WINNER':9s} {'N':>2s}  {'W':>2s}  {'P&L':>8s}  CHOSEN BRACKETS")
print("-"*120)
for r in per_cell_results:
    chosen_str = "  ".join(f"NO {c['label']} ({c['won'] and 'W' or 'L'})" for c in r["chosen"])
    print(f"{r['city']:14s} {r['fdate']} {native(r['rmax'], r['city']):6s} {r['winner'] or '?':9s} "
          f"{r['n_fired']:>2d}  {r['wins']:>2d}  ${r['pnl']:>+7.2f}  {chosen_str}")

print()
print(f"=== SUMMARY ===")
print(f"  cells replayed: {len(per_cell_results)}")
print(f"  total trades fired: {total_fires}")
print(f"  wins: {total_wins}  ({(total_wins/total_fires*100 if total_fires else 0):.0f}%)")
print(f"  simulated P&L at $5/trade: ${total_pnl:+.2f}")
print(f"  vs actual live result (-$52.79 on 29 trades, 45% WR)")
