"""
Pull Trump-only utterances from Roll Call Factba.se transcripts.

URL discovery: Wayback Machine CDX index for rollcall.com/factbase/trump/transcript/*
Fetch: direct from rollcall.com (canonical), normal User-Agent.
Parse: regex-extract <h2>Speaker</h2> + utterance <div>; keep speaker=="Donald Trump".

Persists per-transcript JSON to transcripts/. Skips already-fetched files.

Note: rollcall.com robots.txt blocks AI-training UAs (anthropic-ai, GPTBot,
Google-Extended). It does NOT block normal browsers. This script uses a
normal UA for personal research, throttles requests, and does not feed
the content into model training. Surfaced to user 2026-05-23.
"""
import json
import re
import subprocess
import sys
import time
from datetime import date
from html import unescape
from pathlib import Path

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "transcripts"
OUT_DIR.mkdir(exist_ok=True)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

UTT_PAT = re.compile(
    r'<h2 class="text-md inline">([^<]+)</h2>'
    r'.*?<div class=" flex-auto text-md text-gray-600 leading-loose">([^<]*)</div>',
    re.DOTALL,
)


def fetch_cdx_urls(start: str = "20260101", end: str = "20260520") -> list[tuple[date, str]]:
    """Pull deduped Trump transcript URLs from Wayback CDX."""
    cdx_url = (
        "https://web.archive.org/cdx/search/cdx"
        "?url=rollcall.com/factbase/trump/transcript/*"
        f"&output=json&from={start}&to={end}"
        "&filter=statuscode:200&collapse=urlkey"
    )
    r = subprocess.run(["curl", "-sS", cdx_url], capture_output=True, text=True, timeout=120)
    rows = json.loads(r.stdout)
    urls: dict[str, date] = {}
    for row in rows[1:]:
        u = row[2].rstrip("/")
        slug = u.rsplit("/", 1)[-1]
        m = re.search(
            r"(january|february|march|april|may|june|july|august|september|"
            r"october|november|december)-(\d{1,2})-(\d{4})$",
            slug,
        )
        if not m:
            continue
        try:
            d = date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            continue
        # Keep one URL per slug (CDX collapsed by urlkey already).
        urls[u] = d
    # Filter on URL-date (slug date), not snapshot date — CDX timestamp is
    # when Wayback grabbed the page, which can be much later than the
    # transcript's actual date.
    from_d = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:8]}")
    to_d = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:8]}")
    items = [(d, u) for u, d in urls.items() if from_d <= d <= to_d]
    items.sort()
    return items


def parse_trump(html: str) -> str:
    utts = UTT_PAT.findall(html)
    trump_chunks = [unescape(t.strip()) for sp, t in utts if sp.strip() == "Donald Trump" and t.strip()]
    return " ".join(trump_chunks)


def fetch_and_parse(url: str, dt: date) -> dict | None:
    slug = url.rsplit("/", 1)[-1]
    out_path = OUT_DIR / f"{dt.isoformat()}_{slug}.json"
    if out_path.exists():
        return None  # already cached

    r = subprocess.run(
        ["curl", "-sS", "-A", "Mozilla/5.0", url + "/"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if r.returncode != 0 or len(r.stdout) < 5000:
        return {"slug": slug, "date": dt.isoformat(), "url": url, "error": "fetch_failed"}

    text = parse_trump(r.stdout)
    rec = {
        "slug": slug,
        "date": dt.isoformat(),
        "url": url,
        "trump_words": len(text.split()),
        "trump_chars": len(text),
        "text": text,
    }
    out_path.write_text(json.dumps(rec))
    return rec


def main(limit: int | None = None) -> None:
    items = fetch_cdx_urls()
    if limit:
        items = items[-limit:]  # most recent N for a quick smoke run
    print(f"transcripts to consider: {len(items)}")

    new_fetched = 0
    skipped = 0
    failed = 0
    total_words = 0
    for i, (dt, url) in enumerate(items, 1):
        slug = url.rsplit("/", 1)[-1]
        out_path = OUT_DIR / f"{dt.isoformat()}_{slug}.json"
        if out_path.exists():
            skipped += 1
            continue
        rec = fetch_and_parse(url, dt)
        if rec is None:
            skipped += 1
        elif rec.get("error"):
            failed += 1
            print(f"  FAIL {dt} {slug}")
        else:
            new_fetched += 1
            total_words += rec["trump_words"]
            if new_fetched % 10 == 0:
                print(
                    f"  [{i}/{len(items)}] fetched={new_fetched} skipped={skipped} "
                    f"failed={failed} avg_words={total_words//max(new_fetched,1)}"
                )
        time.sleep(0.5)  # polite throttle

    print(
        f"\nDone. new={new_fetched} skipped={skipped} failed={failed} "
        f"avg_trump_words_per_transcript={total_words // max(new_fetched, 1)}"
    )


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit)
