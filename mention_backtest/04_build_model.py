"""
Compute model probability p_hat per bracket using prior-corpus frequencies.

For each event E (date D), build a prior corpus from the 10 most recent
Trump transcripts dated before D. For each bracket term T:

  count(T, transcript) = # case-insensitive word-boundary matches of T
                        (incl. plural/possessive: T, Ts, T's, Ts')
  lambda_hat(T)        = mean(count(T, transcript)) across prior corpus
  p_hat(T)             = 1 - exp(-lambda_hat(T))

Multi-alternative brackets (e.g. "Japan / Korea") -> p_hat for union of terms
under Poisson independence: 1 - exp(-(lam_T1 + lam_T2 + ...))

v1 explicitly does NOT model:
  - compound-word matches (Polymarket counts "killjoy" for "joy" — rare in
    political speech; bias is toward under-prediction, which makes the
    falsification test conservative)
  - context multipliers from news
  - recency weighting within the prior corpus
  - event-type matching (rally vs interview vs SOTU)

Output: model.json with per-bracket {event_slug, bracket, p_hat, n_priors, ...}
"""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
PRICES = ROOT / "prices.json"
TRANSCRIPTS_DIR = ROOT / "transcripts"
OUT = ROOT / "model.json"

N_PRIORS = 10


def load_transcripts() -> list[dict]:
    items = []
    for f in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            rec = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if rec.get("text") and rec.get("date"):
            items.append(
                {
                    "date": date.fromisoformat(rec["date"]),
                    "slug": rec["slug"],
                    "text": rec["text"],
                    "words": rec.get("trump_words", len(rec["text"].split())),
                }
            )
    items.sort(key=lambda x: x["date"])
    return items


def split_bracket_terms(bracket: str) -> list[str]:
    """A bracket title like 'Japan / Korea' -> ['Japan', 'Korea']."""
    if not bracket:
        return []
    parts = re.split(r"\s*/\s*", bracket.strip())
    return [p.strip() for p in parts if p.strip()]


def count_term(term: str, text: str) -> int:
    """Case-insensitive word-boundary matches of term plus plural/possessive.

    Matches: term, term+s, term's, term+s'. Does NOT match compounds or
    other inflections (per Polymarket rule).
    """
    t = re.escape(term)
    # Treat multi-word terms (e.g. "Hong Kong", "Six Seven") with internal
    # whitespace as a phrase; collapse spaces in regex.
    if " " in term:
        t = re.escape(term).replace(r"\ ", r"\s+")
    # Plural / possessive forms appended after the term.
    pat = re.compile(rf"\b{t}(?:s|'s|s')?\b", re.IGNORECASE)
    return len(pat.findall(text))


def main() -> None:
    prices = json.loads(PRICES.read_text())
    print(f"loaded {len(prices)} bracket records")

    transcripts = load_transcripts()
    print(f"loaded {len(transcripts)} transcripts")
    if not transcripts:
        print("ERROR: no transcripts available; run 03_pull_transcripts.py first")
        return

    # Map event_slug -> event date (from event_end_iso).
    out_records: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for r in prices:
        grouped.setdefault(r["event_slug"], []).append(r)

    for slug, brackets in grouped.items():
        # Event date = endDate (UTC) cast to date.
        ev_end = brackets[0]["event_end_iso"][:10]
        ev_date = date.fromisoformat(ev_end)

        priors = [t for t in transcripts if t["date"] < ev_date][-N_PRIORS:]
        n_priors = len(priors)

        for r in brackets:
            terms = split_bracket_terms(r["bracket"])
            if not terms:
                continue
            # Sum of per-term lambdas (Poisson independence approx).
            lam = 0.0
            per_term_counts = {}
            for term in terms:
                counts = [count_term(term, p["text"]) for p in priors]
                avg = sum(counts) / n_priors if n_priors else 0.0
                per_term_counts[term] = {"avg": avg, "sum": sum(counts)}
                lam += avg
            import math
            p_hat = 1.0 - math.exp(-lam) if lam > 0 else 0.0

            out_records.append(
                {
                    "event_slug": slug,
                    "event_date": ev_date.isoformat(),
                    "bracket": r["bracket"],
                    "terms": terms,
                    "n_priors": n_priors,
                    "lambda_hat": round(lam, 4),
                    "p_hat": round(p_hat, 4),
                    "p_market_yes": r["yes_price_entry"],
                    "resolved_yes": r["resolved_yes"],
                    "saturated_at_entry": r["saturated_at_entry"],
                    "edge": round(p_hat - r["yes_price_entry"], 4),
                    "per_term_counts": per_term_counts,
                }
            )

    OUT.write_text(json.dumps(out_records, indent=2))
    print(f"wrote {len(out_records)} model records to {OUT}")

    # Quick diagnostic: distribution of edges, n_priors coverage
    from collections import Counter
    n_priors_dist = Counter(r["n_priors"] for r in out_records)
    print(f"n_priors distribution: {dict(sorted(n_priors_dist.items()))}")
    too_few = sum(1 for r in out_records if r["n_priors"] < 5)
    print(f"records with n_priors<5 (will be dropped from sim): {too_few}")
    p_hat_at_zero = sum(1 for r in out_records if r["p_hat"] == 0.0)
    print(f"records with p_hat==0 (term never said in prior corpus): {p_hat_at_zero}")


if __name__ == "__main__":
    main()
