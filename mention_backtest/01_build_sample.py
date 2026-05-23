"""
Build the fixed 30-event sample for the mention-market backtest.

Trump-only (per design decision 2026-05-23): single speaker, single transcript
source (Roll Call Factba.se), single prior-corpus definition. Heterogeneous
multi-speaker sample was rejected because per-bucket n would be too small.

Pre-committed selection rule (no cherry-picking):
  - slug starts with "what-will-trump-say"
  - closed=True, has endDate
  - event volume >= $50,000
  - >= 10 sub-markets (brackets)
  - take the 30 most recent by endDate

Persists to sample_events.json. This file is the frozen sample; do not
regenerate after the backtest begins.
"""
import json
import subprocess
import time
from pathlib import Path

OUT = Path(__file__).parent / "sample_events.json"

# Union across multiple queries — public-search truncates at ~50 results, so
# we have to fan out and dedupe.
SEARCH_QUERIES = [
    "what+will+trump+say",
    "what+will+trump+say+during",
    "trump+say+rally",
    "trump+say+interview",
    "trump+say+address",
    "trump+say+conference",
    "trump+say+remarks",
    "trump+say+speech",
    "trump+say+dinner",
    "trump+say+town+hall",
]


def curl_json(url: str):
    r = subprocess.run(["curl", "-sS", url], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def main() -> None:
    all_events: dict[str, dict] = {}
    for q in SEARCH_QUERIES:
        d = curl_json(
            f"https://gamma-api.polymarket.com/public-search?q={q}&limit_per_type=50"
        )
        if not d:
            print(f"  WARN search failed: {q}")
            continue
        for e in d.get("events", []):
            slug = e.get("slug", "")
            if slug and slug.startswith("what-will-trump-say"):
                all_events[slug] = e
        time.sleep(0.3)
    print(f"unique 'what-will-trump-say' events: {len(all_events)}")

    qualifying = []
    for e in all_events.values():
        if not e.get("closed"):
            continue
        if not e.get("endDate"):
            continue
        vol = float(e.get("volume") or 0)
        if vol < 50_000:
            continue
        n_markets = len(e.get("markets", []))
        if n_markets < 10:
            continue
        qualifying.append(e)

    qualifying.sort(key=lambda x: x.get("endDate", ""), reverse=True)
    sample = qualifying[:30]
    print(f"qualifying (vol>=50K, brackets>=10, closed): {len(qualifying)}")
    print(f"sample size: {len(sample)}")

    # Refetch each event via /events?slug= for full market detail.
    full = []
    for e in sample:
        slug = e["slug"]
        d = curl_json(f"https://gamma-api.polymarket.com/events?slug={slug}")
        if not d or not isinstance(d, list) or not d:
            print(f"  WARN could not fetch full event: {slug}")
            continue
        full.append(d[0])
        time.sleep(0.2)

    OUT.write_text(json.dumps(full, indent=2))
    print(f"\nwrote {len(full)} full events to {OUT}")
    print("\n== sample ==")
    for e in full:
        print(
            f"  {e.get('endDate','')[:10]} | "
            f"vol=${float(e.get('volume') or 0):>12,.0f} | "
            f"nMkts={len(e.get('markets',[])):>3} | "
            f"{e.get('slug','')[:75]}"
        )


if __name__ == "__main__":
    main()
