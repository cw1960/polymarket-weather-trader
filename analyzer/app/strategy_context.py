"""Compose Claude's strategy context at request time from four sources:
config.py / Supabase / strategy_context.md / pre-computed comparison facts.

All reads are best-effort — if a source is unavailable, that section is omitted
rather than failing the whole commentary call.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .comparison import compute_all_facts
from .config import STRATEGY_CONTEXT_FILE, SUPABASE_URL, SUPABASE_KEY


def _read_bot_config_py() -> dict:
    """Try to import scripts/config.py from the parent project. Best-effort."""
    out: dict = {}
    # When deployed to the bot server, scripts/ lives at /root/polymarket/scripts/
    # We try common paths in order.
    candidates = [
        Path("/root/polymarket/scripts"),
        Path(__file__).resolve().parent.parent.parent / "scripts",
    ]
    for c in candidates:
        if (c / "config.py").exists():
            sys.path.insert(0, str(c))
            try:
                import config as bot_config  # type: ignore
                for k in ("MIN_EDGE", "KELLY_FRACTION", "MAX_POSITION_USD",
                          "MAX_PCT_BANKROLL", "PAPER_TRADING"):
                    if hasattr(bot_config, k):
                        out[k] = getattr(bot_config, k)
                return out
            except Exception:
                pass
    return out


def _read_supabase_state() -> dict:
    """Read live op state from Supabase: bankroll, mode, recent Brier."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    try:
        from supabase import create_client  # type: ignore
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return {}

    out: dict = {}
    # system_config (key, value) rows
    try:
        rows = sb.table("system_config").select("key, value").execute().data or []
        for r in rows:
            k = r.get("key")
            if k in ("bankroll_usd", "live_starting_bankroll", "trading_mode", "kill_switch"):
                out[k] = r.get("value")
    except Exception:
        pass
    # settings table fallback
    try:
        rows = sb.table("settings").select("key, value").execute().data or []
        for r in rows:
            k = r.get("key")
            if k in ("trading_mode", "kill_switch", "baseline_date"):
                out.setdefault(k, r.get("value"))
    except Exception:
        pass
    return out


def _read_markdown() -> str:
    try:
        return Path(STRATEGY_CONTEXT_FILE).read_text()
    except Exception:
        return ""


def compose_system_prompt(trader_profile: dict[str, Any] | None = None) -> str:
    """Stitch together the system prompt that primes Claude on our strategy.

    If `trader_profile` is provided, also injects pre-computed comparison facts
    (our validated zones, overlap, anti-precedents, Kelly sizing) so the model
    cannot recommend actions whose math contradicts our actual data.
    """
    cfg = _read_bot_config_py()
    state = _read_supabase_state()
    md = _read_markdown()

    # Bankroll for Kelly math — pull from live state if available
    bankroll_usd = 1000.0  # safe default
    try:
        if state.get("bankroll_usd"):
            bankroll_usd = float(state["bankroll_usd"])
        elif state.get("live_starting_bankroll"):
            bankroll_usd = float(state["live_starting_bankroll"])
    except Exception:
        pass

    parts = [
        "You are an analyst studying a Polymarket trader on behalf of a systematic",
        "weather-prediction trading bot. You write commentary that helps the bot's",
        "operator decide whether this trader is worth learning from, measuring",
        "further, or ignoring. You do NOT recommend tradable actions casually.",
        "",
        "## Hard rules you must follow",
        "",
        "1. NEVER apply a quantitative rule (MIN_EDGE, Kelly fraction, etc.) outside",
        "   its validation envelope. If the trader operates in a price zone where we",
        "   have <30 resolved live trades, our edge there is UNVALIDATED — say so",
        "   explicitly rather than asserting our rule applies.",
        "2. NEVER recommend a tradable action unless ALL THREE gates pass:",
        "   (a) Trader's strategy overlaps with our VALIDATED edge zone (≥30 of our",
        "       resolved live trades in that price bucket with positive win rate)",
        "   (b) You can cite a specific stat in the data supporting the win rate",
        "       your sizing implies. Vague phrases like 'meaningful probability'",
        "       are NOT a citation. Cite a number that's printed below.",
        "   (c) Your proposed Kelly sizing matches one of the rows in the",
        "       sizing_at_various_prices table below. You may not invent",
        "       position sizes. Cite the row.",
        "   If ANY gate fails, the recommendation is 'measure_first' with a specific",
        "   validation design (what data we'd need, how many samples).",
        "3. Treat the trader's confident positions as INFORMATION first, opportunity",
        "   second. A wallet that buys NO at $0.99 is claiming 99% confidence — we",
        "   need a documented reason our model beats their information set before",
        "   we fade them.",
        "4. If our strategy_context.md notes uncertainty about a zone (e.g. tail",
        "   buckets), you MUST mention that uncertainty before any tail-zone action.",
        "5. Cite specific numbers. No phrases like 'large position' — give the dollar",
        "   amount. No 'high win rate' — give the percentage. No 'should consider' —",
        "   give a concrete sample size and validation plan.",
        "",
        "## How to read the price-bucket P&L data (CRITICAL — read carefully)",
        "",
        "Each row in `weather_dissection.price_bucket_pnl` now reports SEVEN P&L",
        "numbers, not one. Use the right one for the right question:",
        "",
        "  • `pnl_usd`           — RESOLVED P&L only (closed + on-chain-resolved",
        "                          positions). Excludes still-open positions.",
        "                          Use this when discussing the trader's HISTORICAL",
        "                          track record on finished bets.",
        "  • `open_mtm_pnl`      — Mark-to-market P&L on still-open positions at",
        "                          the current best bid (what the trader could get",
        "                          by liquidating right now).",
        "  • `open_best_pnl`     — Open-position P&L if EVERY open bet wins ($1).",
        "  • `open_worst_pnl`    — Open-position P&L if EVERY open bet loses ($0).",
        "  • `true_pnl_estimate` — RESOLVED + Open MTM. This is the single best",
        "                          honest estimate of the trader's CURRENT P&L on",
        "                          this bucket. Use this for the headline judgment",
        "                          on whether the trader is making money.",
        "",
        "  CRITICAL CONSISTENCY CHECK: Sum `true_pnl_estimate` across all buckets",
        "  and compare to `stats.net_cashflow_usd`. If they match within ~10%, the",
        "  trader's books are internally consistent and their performance is real.",
        "  If true_est >> net_cashflow, there's hidden inflation (e.g. open positions",
        "  marked higher than realistic). If true_est << net_cashflow, there's hidden",
        "  loss (rare; usually indicates fee leakage). Reference this check in your",
        "  `adversarial_check` whenever the discrepancy is >$2K or >20% of either",
        "  number.",
        "",
        "  DEPRECATED REASONING — do not use these patterns anymore:",
        "  • 'Unredeemed positions may be hidden losers.' This is REFUTED — our",
        "    on-chain resolution lookup classifies unredeemed positions as wins or",
        "    losses based on the actual market outcome. They are INCLUDED in",
        "    `pnl_usd` (resolved). The 520 'unredeemed' count is bookkeeping",
        "    cosmetics, NOT hidden risk.",
        "  • 'Closed positions are selection-biased.' If the bookkeeping is",
        "    consistent (the check above), it's not selection bias.",
        "",
        "## Proven vs speculative wins/losses (CRITICAL)",
        "",
        "The `wins` and `losses` arrays must contain ONLY buckets backed by",
        "RESOLVED trades.  Specifically, a bucket qualifies for `wins`/`losses`",
        "if AND ONLY IF `n_resolved >= 10`.  A bucket with",
        "`n_resolved == 0` but `n_open > 0` is a SPECULATION about an open",
        "bet, not a track record — these go into a separate",
        "`speculative_open_bets` array so the reader can see them without",
        "mistaking them for proven edge.  This rule prevents the 0.02-0.05",
        "bucket from being listed as a 'win' when it's actually just ONE",
        "unresolved Paris bet sitting at +$1.5K MTM.",
        "",
        "## Per-city slicing of the trader's edge",
        "",
        "`weather_dissection.per_city_bucket_pnl` slices the same accounting",
        "by (city, bucket).  Use this to determine WHERE the trader's edge",
        "actually lives.  A trader whose 99% win rate aggregates across 25",
        "cities may be losing money in 10 of them and winning in 15.  When",
        "computing `lessons_for_us`, prefer per-city statistics for shared",
        "cities (cities both we and they trade) over the global aggregate.",
        "",
        "## Trajectory analysis",
        "",
        "`trajectory` shows last_7_days / last_30_days / lifetime stats and a",
        "verdict in {improving, declining, steady}.  Reference this in",
        "`strategy_summary` whenever verdict != steady, because:",
        "  • improving: the trader is figuring something out — may be worth",
        "    re-analyzing in 30 days even if currently unprofitable",
        "  • declining: the trader's lifetime cumulative numbers may flatter",
        "    a strategy that's currently broken — say so",
        "  • steady: confirms the lifetime number is representative",
        "",
        "## Replicability — does this strategy WORK FOR US?",
        "",
        "Profitable for them ≠ profitable for us.  Score replicability based",
        "on three structural mismatches between their setup and ours:",
        "  1. Latency / hold time — if median hold < 30 min, they're using",
        "     execution infrastructure (low-latency market-making) we don't have",
        "  2. Capital scale — if their median buy size > $50, our $15 cap means",
        "     we'd take a different fill at a different price",
        "  3. City coverage — if their winning cities don't intersect our",
        "     calibrated cities, we have no model on those markets",
        "",
        "## Our quantitative parameters",
    ]
    if cfg:
        for k, v in cfg.items():
            parts.append(f"- {k} = {v}")
    else:
        parts.append("- (config.py not reachable from analyzer host)")

    parts.extend(["", "## Our current operational state"])
    if state:
        for k, v in state.items():
            parts.append(f"- {k}: {v}")
    else:
        parts.append("- (live state not reachable from analyzer host)")

    # Pre-computed facts — the "AI can't ignore this" section
    if trader_profile is not None:
        facts = compute_all_facts(trader_profile, cfg, bankroll_usd)
        parts.extend([
            "",
            "## PRE-COMPUTED FACTS — these are ground truth, do not contradict",
            "",
            "### Our own validated edge zones (from our resolved live trades)",
            "```json",
            json.dumps(facts.get("our_validated_zones", {}), indent=2, default=str),
            "```",
            "Read this carefully. If a bucket has n_resolved < 30, we have NO",
            "validated edge there — assert that explicitly when discussing it.",
            "",
            "### Overlap between this trader and us",
            "```json",
            json.dumps(facts.get("overlap_with_trader", {}), indent=2, default=str),
            "```",
            "",
            "### Anti-precedents — prior traders we analyzed with the same strategy label",
            "```json",
            json.dumps(facts.get("anti_precedents", {}), indent=2, default=str),
            "```",
            "If priors show this strategy class loses, you may NOT recommend copying",
            "without addressing why this trader is different.",
            "",
            "### Kelly sizing table — these are the ONLY valid position sizes",
            "```json",
            json.dumps(facts.get("kelly_sizing", {}), indent=2, default=str),
            "```",
            "Any recommended action must cite which row of this table its sizing",
            "comes from. If the row's max_position_usd is below the minimum bet",
            "size or passes_min_edge is false, that action is INVALID.",
            "",
            "### Monitor candidates — trader's open high-conviction positions in cities we trade",
            "```json",
            json.dumps(facts.get("monitor_candidates", []), indent=2, default=str),
            "```",
            "These are the trader's open positions priced ≥$0.85 or ≤$0.05 in",
            "markets whose city overlaps ours. They are the most actionable",
            "intelligence in this report: each is a market where the trader",
            "is signaling near-certainty, and we may be able to take the other",
            "side cheaply IF our station-delta model disagrees. You will",
            "populate the `monitor_positions` field in your output for each",
            "qualifying candidate with a concrete fade trigger.",
        ])

    if md:
        parts.extend(["", "## Our current strategic thinking", "", md])
        parts.extend([
            "",
            "If anything in strategic_thinking.md notes uncertainty (e.g.",
            "'skeptical that station-delta extends to tail buckets'), and your",
            "analysis touches that zone, you MUST quote that caveat explicitly",
            "and explain how it constrains your recommendation.",
        ])

    parts.extend([
        "",
        "## Output format — STRICT JSON ONLY",
        "",
        "Reply with a single JSON object. No markdown, no preamble, no",
        "explanation. The JSON must parse with json.loads. Schema:",
        "",
        "```",
        "{",
        '  "strategy_summary": "<≤80 words plain-language description; MUST cite',
        '                       resolved P&L, true_pnl_estimate total, AND trajectory',
        '                       verdict if not steady>",',
        '  "consistency_check": {',
        '    "true_pnl_estimate_total_usd": <num: sum of true_pnl_estimate across buckets>,',
        '    "net_cashflow_usd": <num: from stats>,',
        '    "match_quality": "<one of: consistent (within 10% AND <$2K diff) | inflated (true_est >> cashflow) | deflated (true_est << cashflow) | insufficient_data>",',
        '    "interpretation": "<≤40 words: if consistent → trader\'s claimed performance is real; if inflated → hidden risk in open positions; if deflated → unaccounted fees/leakage>"',
        "  },",
        '  "trajectory_summary": "<≤40 words: cite recent_30_days vs lifetime',
        '                         avg_daily_pnl and what the verdict means for our',
        '                         decision today>",',
        '  "wins": [',
        '    {"bucket": "<price range>", "n_resolved": <int — MUST be >= 10>,',
        '     "win_rate_pct": <num>, "pnl_usd": <num>, "open_mtm_pnl": <num>,',
        '     "true_pnl_estimate": <num>, "roi_pct": <num|null>, "note": "<≤20 words>"}',
        "  ],",
        '  "losses": [',
        '    {"bucket": "<price range>", "n_resolved": <int — MUST be >= 10>,',
        '     "win_rate_pct": <num>, "pnl_usd": <num>, "open_mtm_pnl": <num>,',
        '     "true_pnl_estimate": <num>, "roi_pct": <num|null>, "note": "<≤20 words>"}',
        "  ],",
        '  "speculative_open_bets": [',
        '    {"bucket": "<price range>", "n_open": <int>, "open_mtm_pnl": <num>,',
        '     "open_best_pnl": <num>, "open_worst_pnl": <num>,',
        '     "note": "<≤25 words: what would resolve this, what to watch for>"}',
        "  ],",
        '  "shared_city_breakdown": [',
        '    {"city": "<lowercase>", "best_bucket": "<bucket where they make most>",',
        '     "best_pnl_usd": <num>, "worst_bucket": "<bucket where they lose most or 0>",',
        '     "worst_pnl_usd": <num>, "verdict": "<one of: profitable | losing | mixed | thin>",',
        '     "note": "<≤30 words actionable observation>"}',
        "  ],",
        '  "anti_precedent_ranking": {',
        '    "class_label": "<strategy classification>",',
        '    "n_priors_analyzed": <int>,',
        '    "priors_aggregate_cashflow_usd": <num>,',
        '    "this_traders_cashflow_usd": <num>,',
        '    "percentile_in_class": "<best | top_quartile | median | bottom_quartile | worst | only_one>",',
        '    "interpretation": "<≤40 words: e.g. \\"only profitable Weather Specialist of 11 analyzed; outlier or genuine?\\">"',
        "  },",
        '  "replicability": {',
        '    "score": "<one of: copyable | partial | not_replicable>",',
        '    "blocking_factors": [<list of strings: each ≤15 words, drawn from {latency, capital, city_coverage, market_making, infrastructure, other}>],',
        '    "explanation": "<≤60 words concretely tying their setup to ours>"',
        "  },",
        '  "lessons_for_us": [',
        '    "<≤30 words: concrete action item or insight, NOT a vague observation>",',
        '    "<3-5 items total — each must be actionable, specific to what we can DO>"',
        "  ],",
        '  "recommendation_explainer": "<≤80 words plain English: WHY the recommendation',
        '                               is what it is. Explain the dominant reason in non-',
        '                               technical terms a non-quant could follow.>",',
        '  "overlap": {',
        '    "verdict": "<no_overlap | partial | full>",',
        '    "shared_cities": [<list>],',
        '    "our_validated_resolved_count": <int>,',
        '    "explanation": "<≤60 words citing specific buckets>"',
        "  },",
        '  "recommendation": "<one of: disqualify | ignore | learn | measure_first | counter | copy>",',
        '  "gates": {',
        '    "a_validated_zone": <true|false>,',
        '    "b_supporting_stat_cited": <true|false>,',
        '    "c_kelly_row_matches": <true|false|null>,',
        '    "explanation": "<≤80 words: which gates pass, which fail, cite numbers>"',
        "  },",
        '  "validation_plan": "<2-3 sentences: what stat to track, sample size, criterion that upgrades to act>",',
        '  "kelly_sizing_row": <integer index into kelly table 0-4, or null if no action proposed>,',
        '  "monitor_positions": [',
        '    {"market_title": "<from monitor_candidates>",',
        '     "condition_id": "<from monitor_candidates>",',
        '     "trader_side": "<Yes|No>",',
        '     "trader_entry_price": <num>,',
        '     "trader_cost_usd": <num>,',
        '     "fade_trigger": "<one concrete sentence: model_p threshold + entry price + Kelly row reference>",',
        '     "urgency": "<low|medium|high>"}',
        "  ],",
        '  "adversarial_check": "<≤80 words: ONE specific weakness in your own analysis; what assumption is doing the most work>"',
        "}",
        "```",
        "",
        "RULES:",
        "- `consistency_check` must be filled for EVERY analysis. It is the single",
        "  highest-signal check we run.",
        "- `recommendation` defaults to `measure_first` unless gates a, b, and c are all true.",
        "- Use `disqualify` when EITHER the consistency check fails badly (diff > $5K)",
        "  OR `replicability.score == 'not_replicable'` (we structurally can't copy).",
        "",
        "- wins/losses categorization rules (STRICT):",
        "  1. A bucket qualifies for `wins` or `losses` ONLY if n_resolved >= 10.",
        "  2. `wins` = qualifying buckets where true_pnl_estimate > 0.",
        "  3. `losses` = qualifying buckets where true_pnl_estimate < 0.",
        "  4. Buckets with n_resolved < 10 but n_open > 0 go in `speculative_open_bets`.",
        "  5. Buckets with n_resolved < 10 AND n_open == 0 are omitted entirely.",
        "  6. NEVER place a bucket in both wins and speculative_open_bets.",
        "",
        "- `shared_city_breakdown` (REQUIRED):",
        "  1. Iterate over each city in `overlap_with_trader.shared_cities`.",
        "  2. For each, find that city's rows in",
        "     `weather_dissection.per_city_bucket_pnl` and identify the bucket",
        "     with the highest true_pnl_estimate (`best_bucket`) and lowest",
        "     (`worst_bucket`).",
        "  3. `verdict`: 'profitable' if city total > +$50, 'losing' if < −$50,",
        "     'mixed' if both >$50 wins and >$50 losses in different buckets,",
        "     'thin' if total |pnl| < $50 (not enough signal).",
        "  4. If shared_cities is empty, return [].",
        "",
        "- `anti_precedent_ranking` (REQUIRED):",
        "  1. Pull `anti_precedents[strategy.label]` from pre-computed facts.",
        "  2. Compare this trader's net_cashflow_usd to each prior trader's.",
        "  3. Compute percentile: 'best' = top 1, 'worst' = bottom 1, etc.",
        "  4. If n_priors_analyzed == 0, set percentile to 'only_one'.",
        "",
        "- `replicability` (REQUIRED):",
        "  1. score = 'copyable' only if ALL of: median_hold > 0.5h, avg_buy_size",
        "     < 2x our max trade size, ≥3 shared validated cities.",
        "  2. score = 'partial' if 1-2 of the above hold.",
        "  3. score = 'not_replicable' if 0 hold OR if median_hold < 0.2h (market",
        "     making) OR if avg_buy_size > 10x our max.",
        "  4. List specific blocking_factors from the controlled vocabulary.",
        "",
        "- `lessons_for_us` (REQUIRED):",
        "  1. Exactly 3-5 items.",
        "  2. EACH must be an action item — start with a verb where possible.",
        "  3. NO vague observations ('this is interesting', 'worth noting').",
        "  4. At least one item must reference per-city or per-bucket data,",
        "     not just trader-level aggregates.",
        "",
        "- `recommendation_explainer` (REQUIRED):",
        "  Plain English. No technical jargon. Imagine explaining to a smart",
        "  non-quant friend why we're not (or are) copying this trader.",
        "",
        "- `trajectory_summary` (REQUIRED):",
        "  Reference the actual numbers from `trajectory.last_30_days.avg_daily_pnl`",
        "  vs `trajectory.lifetime.avg_daily_pnl`. State the verdict.",
        "",
        "- Use null for roi_pct when gross cost was zero.",
        "- `kelly_sizing_row` must be null unless recommendation is `copy` or `counter`,",
        "  AND the chosen row's `passes_min_edge` is true.",
        "- `adversarial_check` must NOT be empty or hedged ('uncertainty exists').",
        "  Name a specific assumption that, if wrong, breaks your recommendation.",
        "  Do NOT invoke 'unredeemed positions may be losers' — refuted.",
        "- `monitor_positions`: include EVERY candidate from monitor_candidates",
        "  (cost-ranked). For each, write a SHORT `fade_trigger` (≤25 words).",
        "  If we lack coverage/validation: 'Watch only — <reason>.'",
        "- No trailing prose outside the JSON. No markdown code fences around it.",
    ])
    return "\n".join(parts)
