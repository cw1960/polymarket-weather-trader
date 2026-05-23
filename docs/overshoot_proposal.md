# Overshoot Loss Proposal — Request for Review

**Date:** May 10, 2026
**Status:** Discussion document, no changes implemented yet
**Audience:** Senior dev review

---

## Summary

After implementing the calibration filter and price cap (May 8), our loss pattern shifted. Overshoots now dominate. We need a strategy to address them without breaking what's working.

This document presents the data, four proposed solutions, and asks for the senior dev's input on which to pursue.

---

## The Loss Pattern Has Shifted

**Original system (pre-filter, all trades):**
- 22 undershoots (65% of losses) — temperature kept rising after lock
- 12 overshoots (35%) — delta correction landed us one bracket too high

**New system (calibrated cities, price < 30¢, real-money trades only):**
- 2 undershoots — $90 in losses
- **10 overshoots — $450 in losses**

The recently deployed fixes (rate-of-change check, cloud cover boost) target undershoots. They will help with $90 of losses but do nothing for the dominant $450 problem.

---

## What An Overshoot Looks Like

For each real-money loss, here is the lock-time data:

| Date | City | Lock max | Delta | Adjusted | Bet | Actual | Result |
|------|------|----------|-------|----------|-----|--------|--------|
| May 5 | Hong Kong | 23.2°C | -0.23 | 23.0°C | 24°C | 23°C | **Over +1** |
| May 5 | Helsinki | 13.2°C | -1.13 | 12.1°C | 13°C | 12°C | **Over +1** |
| May 6 | Hong Kong | 25.4°C | -0.23 | 25.2°C | 25°C | 24°C | **Over +1** |
| May 7 | Madrid | 18.1°C | -0.10 | 18.0°C | 19°C | 17°C | **Over +2** |
| May 8 | Seoul | 19.0°C | +0.33 | 19.3°C | 21°C | 20°C | **Over +1** |
| May 8 | Tel Aviv | 25.0°C | 0.00 | 25.0°C | 26°C | 25°C | **Over +1** |
| May 8 | Amsterdam | 17.0°C | +0.30 | 17.3°C | 19°C | 17°C | **Over +2** |
| May 9 | Amsterdam | 18.6°C | +0.30 | 18.9°C | 19°C | 18°C | **Over +1** |
| May 10 | Wuhan | 28.0°C | +0.80 | 28.8°C | 29°C | 28°C | **Over +1** |
| May 10 | Chengdu | 31.4°C | +0.72 | 32.1°C | 32°C | 31°C | **Over +1** |

(Note: Some "delta" values shown are current values; historical deltas may have differed at trade time.)

### Two patterns visible

**Pattern A — Boundary proximity (5 of 10):** The adjusted temperature was within 0.3°C of a bracket boundary. Examples: Wuhan adj 28.8 (boundary at 28.5), Chengdu adj 32.1 (boundary at 31.5), Amsterdam adj 18.9 (boundary at 18.5).

**Pattern B — Persistent positive bias (5 of 10):** Cities with delta near zero or negative still overshoot, suggesting the METAR-to-WU relationship has structural noise we're not capturing. Tel Aviv (delta=0) overshot. Hong Kong (delta=-0.23) overshot twice.

---

## Why Did the Previous Boundary Buffer Attempt Fail?

We previously implemented an upward boundary buffer that pushed our bracket selection to the next-higher bracket when within 0.5°C of the boundary. This caused immediate losses (Seoul, Tel Aviv on May 8) and was reverted.

That failure happened because the buffer pushed us *toward* the higher bracket. The current data suggests the opposite is needed: we should be more cautious near the *upper* boundary, not aggressive.

---

## Four Proposed Solutions

### Option 1 — Boundary Buffer (Skip Near Edge)

Skip trades when the adjusted temperature is within 0.3°C of a bracket boundary. Example: if adjusted = 28.8°C and the upper boundary is 28.5°C, we'd skip rather than bet on bracket 29.

**Pros:**
- Directly addresses Pattern A (5 of 10 overshoots)
- Simple to implement
- No model retraining needed

**Cons:**
- Reduces trade count (an estimated 30-40% of qualifying trades would be filtered)
- Doesn't help with Pattern B (intrinsic noise overshoots)
- Doesn't address overshoots that are clearly NOT near a boundary

### Option 2 — Bayesian Shrinkage of Delta

For cities with small calibration samples (n < 10), shrink the estimated delta toward zero using a Bayesian prior. As n grows, trust the local delta more.

Formula: `effective_delta = (n / (n + K)) * raw_delta`, where K is a shrinkage parameter (suggested K=5).

Example with K=5:
- n=3 samples: use 37.5% of raw delta
- n=5 samples: use 50%
- n=10 samples: use 67%
- n=50 samples: use 91%

**Pros:**
- Statistically principled — addresses small-sample variance directly
- Self-correcting: cities prove themselves before getting full delta credit
- Recommended by senior dev in earlier review

**Cons:**
- Will increase undershoot rate for cities that genuinely have a strong positive delta (e.g., Wuhan +1.0°C, Chongqing +1.0°C)
- Counterfactual analysis is mixed: only 4 of 10 overshoots prevented at 50% shrinkage

### Option 3 — Delta Variance Tracking

Track the standard deviation of delta observations alongside the mean. Use `mean - K*sigma` for prediction. Cities with high variance get smaller effective delta.

**Implementation requires:**
- Schema change to add `delta_variance` column
- Update resolver to maintain running variance
- Update prediction logic

**Pros:**
- Addresses Pattern B (cities with noisy deltas)
- More information-rich than shrinkage alone
- Aligns with senior dev's "delta-by-condition" idea (precursor)

**Cons:**
- More complex implementation (running variance)
- Needs minimum n before variance is meaningful (probably n ≥ 5)
- Conservative bias may reduce overall trade count significantly

### Option 4 — Higher Confidence Threshold

Raise `PHASE2_MIN_CONFIDENCE` from 0.81 to 0.90. Only trade when extremely confident (later in day, longer stability, or with cloud cover boost).

**Pros:**
- Trivial to implement (one config value)
- Reduces trade count → reduces variance
- Naturally combines with cloud cover fix

**Cons:**
- Doesn't directly address overshoots — they happen at high confidence too
- May filter out high-EV cheap-bracket opportunities
- Crude tool

---

## Combined Approach (My Recommendation)

The cleanest path forward might be combining Options 1 and 2:

1. **Boundary buffer with asymmetry:** Skip trades when adjusted temp is within 0.3°C of the *upper* bracket boundary (overshoot risk side). Don't apply on the lower boundary side.
2. **Bayesian shrinkage** with K=5 to address small-sample delta variance.

Reasoning:
- Boundary buffer attacks the most identifiable failure mode (Pattern A)
- Shrinkage addresses Pattern B more gradually
- Combined effect: skip ~30% of trades, but those skipped have the highest overshoot risk

---

## Counterfactual Backtest Limitations

I want to be honest about what I can and can't backtest:

- **What I have:** Lock-time temperature, current delta values, actual resolution outcomes, bet bracket
- **What I don't have:** Historical delta values at the time of trade (deltas have evolved), raw temperature reading history, day-by-day market price snapshots

This means counterfactual estimates are directionally useful but not precise. A proposed change that "saves 4 of 10 overshoots" in backtest might save 2 or 6 in production.

The most reliable test is forward results. We should pick one approach, run it for 14 days, and compare to the current 35.7% / +$1,476 baseline.

---

## Specific Questions for Senior Dev

1. **Is asymmetric boundary buffer (Option 1) defensible?** It treats overshoots as worse than undershoots, which is true under our payout structure (cheap brackets) but feels ad-hoc.

2. **Bayesian shrinkage (Option 2) — what K value?** I suggested K=5. Too aggressive? Too conservative? What's the typical convention for small-sample bias correction in similar trading systems?

3. **Should we track delta variance (Option 3)?** It's the most informative approach but requires schema changes and minimum samples. Worth the complexity?

4. **Are these fixes mutually exclusive or stackable?** I assume stackable but want validation.

5. **What's the right success metric?** I'm proposing 14 days of forward data vs. May 2-10 baseline. Is that enough? Would you suggest a different evaluation method?

6. **Any hidden risks I'm missing?** Each fix reduces trade count, which means longer time to statistical significance. Is there a way to address overshoots that doesn't reduce sample size?

---

## Additional Context: What's Already Live

Three fixes were deployed today (May 10):

1. **Bracket matching fallback** — Jakarta/KL/Shanghai now find nearest available bracket within 1°C/2°F when exact match fails (purely additive, no risk)
2. **Rate-of-change check** — Lock requires flat/declining trend over last 6+ readings (targets undershoots only)
3. **Cloud cover boost** — BKN/OVC sky conditions add +0.05/+0.08 to lock confidence (targets undershoots, marginal)

None of these address the overshoot problem this document is about.

---

## Bankroll Status

- Starting bankroll (May 2): $1,000
- Current bankroll (May 10): $2,415
- Real-money trades placed: 17
- Win rate: 29.4%
- Total P&L: +$1,341
- ROI on deployed: 175.3%

The system is profitable. We are seeking incremental improvement, not rescue.
