"""For each city's CURRENT 5/22 ladder, show the FULL candidate analysis:
running_max, every bracket's prob_no, no_price, edge, and whether it
passes the gate. Lets us see where the precision-fixed bot is finding
edge vs not."""
import os, json, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, "/root/polymarket/scripts")

import requests
from supabase import create_client

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])


def f_to_c(f): return (f - 32.0) * 5.0 / 9.0


CITY_SLUG = {
    "NYC":"nyc","Chicago":"chicago","Miami":"miami","Los Angeles":"los-angeles",
    "Dallas":"dallas","Atlanta":"atlanta","Houston":"houston","Austin":"austin",
    "Seattle":"seattle","San Francisco":"san-francisco","Denver":"denver",
    "London":"london","Paris":"paris","Madrid":"madrid","Munich":"munich",
    "Milan":"milan","Amsterdam":"amsterdam","Warsaw":"warsaw","Helsinki":"helsinki",
    "Istanbul":"istanbul","Ankara":"ankara","Moscow":"moscow","Tel Aviv":"tel-aviv",
    "Hong Kong":"hong-kong","Seoul":"seoul","Tokyo":"tokyo","Busan":"busan",
    "Taipei":"taipei","Beijing":"beijing","Shanghai":"shanghai","Guangzhou":"guangzhou",
    "Shenzhen":"shenzhen","Chengdu":"chengdu","Chongqing":"chongqing","Wuhan":"wuhan",
    "Singapore":"singapore","Kuala Lumpur":"kuala-lumpur","Manila":"manila",
    "Jakarta":"jakarta","Wellington":"wellington","Toronto":"toronto",
    "Mexico City":"mexico-city","São Paulo":"sao-paulo","Buenos Aires":"buenos-aires",
    "Cape Town":"cape-town","Lagos":"lagos",
}


def inspect(city: str, fdate: str):
    print()
    print("=" * 90)
    print(f"  {city}  {fdate}")
    print("=" * 90)
    tr = sb.table("temp_readings").select("running_max_c,observed_at").eq("city",city).eq("reading_date",fdate).limit(1).execute()
    if not tr.data:
        print("  no temp_readings"); return
    rmax_c = float(tr.data[0]["running_max_c"])
    ef = sb.table("ensemble_forecasts").select("raw_members,ecmwf_members").eq("city",city).eq("forecast_date",fdate).order("created_at",desc=True).limit(1).execute()
    if not ef.data:
        print("  no ensemble"); return
    members = [float(m) for m in (ef.data[0].get("raw_members") or [])] + [float(m) for m in (ef.data[0].get("ecmwf_members") or [])]
    filt = [m for m in members if m >= rmax_c]
    print(f"  running_max = {rmax_c:.2f}°C   ensemble members ≥ rmax: {len(filt)}/{len(members)}")
    if not filt: return

    lr = sb.table("ladders").select("buckets_json").eq("city",city).eq("forecast_date",fdate).order("created_at",desc=True).limit(1).execute()
    if not lr.data: print("  no ladder"); return
    buckets = json.loads(lr.data[0]["buckets_json"])

    slug = CITY_SLUG.get(city, city.lower().replace(" ","-"))
    from datetime import date
    d = date.fromisoformat(fdate)
    date_slug = d.strftime("%B-%-d-%Y").lower()
    e = requests.get(f"https://gamma-api.polymarket.com/events/slug/highest-temperature-in-{slug}-on-{date_slug}", timeout=10).json()
    prices = {}
    for m in e.get("markets", []):
        op = m.get("outcomePrices")
        if isinstance(op,str): op=json.loads(op)
        if not op or len(op)<2: continue
        # market_price[1] = NO price (current orderbook)
        q = m.get("question","")
        # Parse the same way the bot does — match bracket labels
        prices[m.get("question","")] = (float(op[0]), float(op[1]))

    print(f"  {'BRACKET':10s} {'BOUNDS (°C)':15s} {'M_NO':6s} {'NO_PRICE':9s} {'EDGE':7s} {'GATE':6s}")
    for b in buckets:
        label = b.get("label","")
        low_c = float(b.get("low",-9999))
        high_c = float(b.get("high",9999))
        if b.get("unit") == "F":
            low_c = f_to_c(low_c); high_c = f_to_c(high_c)
        count_in = sum(1 for m in filt if low_c <= m <= high_c)
        prob_yes = count_in / len(filt)
        prob_no = 1 - prob_yes
        # Find market: match bracket label inside the question text
        clean = label.lower()
        no_p = None
        for q, (yp, np_) in prices.items():
            ql = q.lower()
            if clean in ql or ("≤" in clean and "or below" in ql and clean.replace("≤","") in ql) or ("≥" in clean and "or higher" in ql and clean.replace("≥","") in ql):
                no_p = np_; break
        edge = (prob_no - no_p) if no_p is not None else None
        gate_pass = (prob_no >= 0.55) and (edge is not None and edge >= 0.08)
        lo_s = "-inf" if low_c < -8000 else f"{low_c:5.1f}"
        hi_s = "+inf" if high_c > 8000 else f"{high_c:5.1f}"
        bounds_s = f"[{lo_s},{hi_s}]"
        np_s = f"{no_p:.3f}" if no_p is not None else "  ?  "
        edge_s = f"{edge:+.3f}" if edge is not None else "  ?  "
        gate_s = "✓ FIRE" if gate_pass else ""
        print(f"  {label:10s} {bounds_s:15s} {prob_no:.3f}  {np_s:9s} {edge_s:7s} {gate_s}")


for city in ("São Paulo","Madrid","Buenos Aires","Cape Town","London","Warsaw","Ankara"):
    inspect(city, "2026-05-22")
