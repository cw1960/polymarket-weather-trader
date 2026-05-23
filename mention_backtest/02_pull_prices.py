"""
Pull Yes-token CLOB price for each bracket and final resolution.

Entry-time rule (pre-committed, v2 — revised 2026-05-23 after discovering
many markets open only a few hours before the event):

  entry_ts = max(market.startDate + 30min, endDate - 24h)

  Long-lived markets get T-24h. Short-fuse markets get market_open+30min
  (lets bots seed, captures early-human pricing). Uniform methodology
  across the sample; reflects what a real trader sees.

Saturation: entry price <0.02 or >0.98 means the bracket was effectively
already resolved — flagged but kept for transparency.

Resolution truth: outcomePrices = ["1","0"] -> Yes, ["0","1"] -> No.
"""
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SAMPLE = ROOT / "sample_events.json"
OUT = ROOT / "prices.json"


def curl_json(url: str, retries: int = 2):
    for _ in range(retries + 1):
        r = subprocess.run(
            ["curl", "-sS", url], capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout:
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(0.5)
    return None


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00").replace(" ", "T")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def main() -> None:
    events = json.loads(SAMPLE.read_text())
    print(f"loaded {len(events)} events")

    out_records: list[dict] = []
    for ev in events:
        slug = ev["slug"]
        ev_end = parse_iso(ev.get("endDate", ""))
        if not ev_end:
            continue

        per_event = 0
        for m in ev.get("markets", []):
            try:
                tokens = json.loads(m.get("clobTokenIds") or "[]")
                outcomes = json.loads(m.get("outcomePrices") or "[]")
            except json.JSONDecodeError:
                continue
            if len(tokens) < 2 or len(outcomes) < 2:
                continue
            try:
                yes_final = float(outcomes[0])
                no_final = float(outcomes[1])
            except ValueError:
                continue
            if {yes_final, no_final} != {0.0, 1.0}:
                continue
            resolved = int(yes_final)
            yes_token = tokens[0]

            mkt_start = parse_iso(m.get("startDate") or m.get("createdAt") or "")
            mkt_end = parse_iso(m.get("endDate") or ev.get("endDate"))
            if not mkt_start or not mkt_end:
                continue

            entry_dt = max(mkt_start + timedelta(minutes=30), mkt_end - timedelta(hours=24))
            entry_ts = int(entry_dt.timestamp())

            # Window: capped at <=6 days (API rejects >7d at fidelity=60).
            window_start = max(int(mkt_start.timestamp()) - 3600, entry_ts - 5 * 86400)
            window_end = min(int(mkt_end.timestamp()) + 3600, entry_ts + 6 * 86400)
            d = curl_json(
                f"https://clob.polymarket.com/prices-history"
                f"?market={yes_token}&startTs={window_start}&endTs={window_end}&fidelity=60"
            )
            pts = (d or {}).get("history", []) if d else []
            if not pts:
                # Try wider via interval=max
                d = curl_json(
                    f"https://clob.polymarket.com/prices-history"
                    f"?market={yes_token}&interval=max&fidelity=60"
                )
                pts = (d or {}).get("history", []) if d else []
            if not pts:
                continue

            closest = min(pts, key=lambda x: abs(x["t"] - entry_ts))
            entry_price = float(closest["p"])
            saturated = entry_price < 0.02 or entry_price > 0.98

            out_records.append(
                {
                    "event_slug": slug,
                    "event_end_iso": ev.get("endDate"),
                    "market_start_iso": mkt_start.isoformat(),
                    "market_end_iso": mkt_end.isoformat(),
                    "market_lifespan_h": round((mkt_end - mkt_start).total_seconds() / 3600, 2),
                    "entry_ts": entry_ts,
                    "entry_actual_ts": closest["t"],
                    "entry_offset_s": closest["t"] - entry_ts,
                    "bracket": m.get("groupItemTitle"),
                    "question": m.get("question"),
                    "yes_price_entry": entry_price,
                    "resolved_yes": resolved,
                    "saturated_at_entry": saturated,
                    "uma_status": m.get("umaResolutionStatuses"),
                }
            )
            per_event += 1
            time.sleep(0.04)
        print(f"  {slug}: {per_event} brackets")

    OUT.write_text(json.dumps(out_records, indent=2))
    n = len(out_records)
    sat = sum(1 for r in out_records if r["saturated_at_entry"])
    yes = sum(1 for r in out_records if r["resolved_yes"] == 1)
    print(f"\nwrote {n} records to {OUT}")
    print(f"saturated at entry: {sat} / {n} ({100*sat/max(n,1):.1f}%)")
    print(f"resolved Yes      : {yes} / {n} ({100*yes/max(n,1):.1f}%)")
    # Event coverage
    by_ev = {}
    for r in out_records:
        by_ev[r["event_slug"]] = by_ev.get(r["event_slug"], 0) + 1
    print(f"events with >=1 bracket: {len(by_ev)} / {len(events)}")


if __name__ == "__main__":
    main()
