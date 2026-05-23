# Polymarket Weather Trading System — Technical Overview

**Date:** May 9, 2026
**Status:** Live (paper trading with real-money sizing logic)
**Bankroll:** $2,032 (started at $1,000 on May 2)

---

## 1. What the System Does

This system trades daily high-temperature markets on Polymarket. Each day, Polymarket lists ~50 cities with markets like "Will the highest temperature in Amsterdam be 14°C on May 5?" Each bracket (e.g., "14°C", "15°C", "16°C") trades as a binary YES/NO contract that resolves to $1 or $0 based on what Weather Underground reports as the official high.

The system identifies brackets where the market price is wrong — specifically, brackets that are cheap (under 30 cents) but where we have high-confidence evidence the temperature will land there. We buy YES at 5-25 cents and collect $1 if correct, for a 4-20x payout.

---

## 2. Two-Phase Architecture

### Phase 1: Morning Forecast (Observation Only)

**Runs:** 4x daily at GFS model run times (03:30, 09:30, 15:30, 21:30 UTC)
**Data source:** Open-Meteo ensemble API
  - GFS ensemble: 31 members
  - ECMWF IFS025 ensemble: 51 members
  - 6 deterministic models: GFS, ECMWF, ICON, MeteoFrance, UKMO, GEM

**Process:**
1. Fetch ensemble forecasts for all 50 cities
2. For each city, build a "ladder" of bracket probabilities by counting how many of the 82 ensemble members fall into each temperature bracket
3. Compute model probability, market price, and expected value for each bracket
4. Store all signals in `trade_signals` table with `signal_phase = 'phase1'`
5. No capital deployed — Phase 1 is purely observational ($0.01 symbolic position)

**Purpose:** Phase 1 provides the morning probability baseline and feeds the delta calibration pipeline (see Section 4). It does NOT drive trading decisions.

### Phase 2: Afternoon Confirmation (Real Money)

**Runs:** Every 5 minutes via `temp_monitor.py` cron job
**Data source:** Real-time METAR aviation weather reports (ICAO stations)

**Process:**
1. Every 5 minutes, poll METAR data for all 50 cities via aviationweather.gov
2. Track each city's running daily maximum temperature
3. When a city's running max has been stable for 12 consecutive readings (60 minutes) AND it is past 1 PM local time, consider the bracket "locked"
4. Compute lock confidence based on time of day, stability duration, and number of readings
5. Look up the corresponding Polymarket bracket and current YES price
6. Apply trading filters (see Section 3)
7. If all filters pass, place a $45 YES trade on the locked bracket

**The edge:** By the time Phase 2 fires, we have 60+ minutes of stable real-time temperature data showing the daily high has likely peaked. The market is still pricing brackets based on morning forecasts and general uncertainty. We're buying at morning odds with afternoon information.

---

## 3. Trading Filters (Current Configuration)

All three filters must pass for a real-money trade:

| Filter | Threshold | Rationale |
|--------|-----------|-----------|
| **Calibration** | `delta_samples >= 3` | Only trade cities where we have enough historical data to know the station bias between METAR and Weather Underground. Backtest: calibrated cities = +$272 (36% WR, 69.5% ROI); uncalibrated = -$202 (14% WR). |
| **Price cap** | `YES price < 30 cents` | Only buy cheap brackets where the payout asymmetry works in our favor. Backtest: trades under 30 cents = +$183 (positive EV); above 30 cents = net negative. |
| **Confidence** | `lock confidence >= 0.81` | Temperature must be stable for 60+ minutes past 1 PM local time. |

**Position sizing:** $45 flat per qualifying trade. $350/day budget cap.

**Observation mode:** Cities that fail the calibration or price filter still get a $0.01 symbolic trade. This keeps the delta calibration pipeline running (see Section 4) without risking capital.

---

## 4. Delta Calibration System

### The Problem

Polymarket resolves temperature markets using Weather Underground, which sources from specific weather stations. Our real-time data comes from METAR (aviation weather reports) at nearby airports. These two sources often disagree by 0.5-2.0°C due to:
- Different physical station locations (airport vs. city center)
- Different sensor equipment and reporting standards
- Weather Underground's proprietary data processing
- Microclimate differences between station sites

Since Polymarket brackets are 1°C wide, even a 1°C systematic bias means we consistently pick the wrong bracket.

### The Solution: Adaptive Delta Correction

Each city has a learned `delta_c` value stored in the `resolution_stations` table. This represents the systematic bias: `resolution_temp = METAR_temp + delta_c`.

**How it's computed:**
1. When a Phase 2 trade resolves, we know:
   - The METAR temperature at lock time (stored as `mean_high` in trade_signals)
   - The actual resolution temperature (inferred from the winning bracket)
2. `observed_delta = resolution_temp - METAR_lock_temp`
3. Update using exponential smoothing: `new_delta = old_delta * (1 - alpha) + observed_delta * alpha`
4. `alpha = max(0.20, 1 / (1 + samples))` — high weight early, stabilizes after 5+ observations

**How it's applied:**
When Phase 2 locks a bracket, it adds `delta_c` to the running max before looking up which bracket to bet on:
```
adjusted_temp = running_max_metar + delta_c
bracket = find_bracket(adjusted_temp)
```

### Current Calibration State (14 cities qualified)

| City | Delta | Samples | Interpretation |
|------|-------|---------|---------------|
| Amsterdam | +0.80°C | 5 | WU reads ~0.8°C warmer than METAR |
| Ankara | +1.00°C | 4 | WU reads ~1.0°C warmer |
| Cape Town | +0.00°C | 3 | METAR and WU agree |
| Chengdu | +1.00°C | 5 | WU reads ~1.0°C warmer |
| Chongqing | +1.00°C | 4 | WU reads ~1.0°C warmer |
| Helsinki | -1.13°C | 3 | WU reads ~1.1°C cooler than METAR |
| Hong Kong | -0.67°C | 3 | WU reads ~0.7°C cooler |
| Lagos | -0.10°C | 4 | Nearly identical |
| Madrid | -0.10°C | 3 | Nearly identical |
| Miami | +0.12°C | 3 | Nearly identical |
| NYC | +0.71°C | 3 | WU reads ~0.7°C warmer |
| Seoul | +0.33°C | 3 | WU reads ~0.3°C warmer |
| Tel Aviv | +0.00°C | 5 | METAR and WU agree |
| Wuhan | +1.00°C | 5 | WU reads ~1.0°C warmer |

**Uncalibrated cities (36)** continue running in observation mode. Each resolved observation trade adds a calibration sample. Cities automatically graduate to real trading when they accumulate 3+ samples.

### Default Delta for Uncalibrated Cities

Cities with fewer than 3 calibration samples use a default delta of +1.0°C. This reflects the general pattern that Weather Underground tends to read warmer than METAR for most cities. However, some cities (Helsinki, Hong Kong) have negative deltas, which is why the default is only a rough approximation and real calibration data is required before deploying capital.

---

## 5. Bracket Locking Logic

The `temp_monitor.py` script runs every 5 minutes and tracks each city's temperature throughout the day. The locking decision uses:

**Stability requirement:** The running daily maximum must remain unchanged for 12 consecutive 5-minute readings (60 minutes of stability). This was increased from 6 readings (30 minutes) after analysis showed 13 out of 23 early Phase 2 losses were "premature locks" — the temperature continued rising after the system bet.

**Time gate:** Must be past 1 PM local city time. This prevents betting during the morning warm-up when temperatures are still rising rapidly.

**Confidence formula:** Combines time-of-day (later = higher confidence) and stability duration into a 0-1 score. Must reach 0.81 to trigger Phase 2.

**METAR sources:** Primary source is aviationweather.gov METAR reports via ICAO station codes (e.g., EHAM for Amsterdam Schiphol, KLGA for NYC LaGuardia). Fallback to Open-Meteo current weather API for cities without reliable METAR coverage.

---

## 6. Performance Data

### Backtest Results (May 2-8, 2026 — 46 trades, adjusted to current strategy)

| Metric | Value |
|--------|-------|
| Real trades (calibrated + price < 30 cents) | 13 |
| Win rate | 30.8% (4W / 9L) |
| Total P&L | +$1,047.70 |
| ROI on deployed capital | 179.1% |
| Average win | +$363.17 |
| Average loss | -$45.00 (always the full stake) |
| Profitable days | 4 out of 5 (80%) |
| Best single day | +$575.69 |
| Worst single day | -$135.00 |

### Why 30% Win Rate is Profitable

The system buys YES at 5-25 cents. A loss costs exactly the stake ($45). A win pays $45 / price — at 7 cents, that's $45 / 0.07 = $643 payout minus $45 cost = $598 profit. The breakeven win rate at 7 cents is ~7%. At 25 cents it's ~25%. Our 30.8% win rate exceeds breakeven at every price point in our range.

### Loss Pattern Analysis

Of 34 total losses across all Phase 2 trades (before filtering):
- **97% were off-by-one bracket misses** — the system picked an adjacent bracket
- **22 losses (65%) were undershoots** — premature lock; temperature continued rising after we bet
- **12 losses (35%) were overshoots** — delta correction was too aggressive for that city

This confirms the system is identifying the correct *region* of temperature nearly every time. The remaining error is split between timing (premature lock) and station bias (delta accuracy), both of which improve as calibration data accumulates.

---

## 7. Infrastructure

| Component | Details |
|-----------|---------|
| **VPS** | Vultr Ubuntu 22.04, 108.61.241.81 |
| **Database** | Supabase (PostgreSQL) — tables: trade_signals, ladders, temp_readings, ensemble_forecasts, resolution_stations, bankroll_snapshots, system_config |
| **Frontend** | React + Vite + Tailwind, deployed on Netlify (weatherornotbot.netlify.app) |
| **Cron schedule** | signal_engine.py 4x/day (GFS windows), temp_monitor.py every 5 min, bankroll reconciliation daily at 02:30 UTC |
| **API dependencies** | Open-Meteo (forecasts), aviationweather.gov (METAR), Gamma API (Polymarket prices) |
| **Execution** | Paper mode — signals are written to DB with correct sizing; no actual CLOB orders placed yet. Executor module exists for live trading. |

---

## 8. Key Questions for Review

1. **Is the delta calibration approach sound?** Exponential smoothing with alpha = max(0.20, 1/(1+n)) on the METAR-to-WU bias. Is there a better estimator given small sample sizes (3-5 observations)?

2. **Is the 30-cent price cap justified?** The backtest shows positive EV only below 30 cents. Is this a real structural edge (payout asymmetry) or an artifact of small sample size and a few large wins?

3. **Premature lock mitigation:** 65% of losses are premature locks (temp still rising). Current mitigation is 60 minutes of stability. Are there better signals for "temperature has peaked" — e.g., comparing to forecast peak hour, or using rate-of-change?

4. **Sample size concern:** 13 real trades is a small sample. The two largest wins (Lagos +$642, Amsterdam +$576) represent ~116% of total profit. How much confidence should we place in a 179% ROI from 13 trades?

5. **Compounding plan:** Currently flat $45/trade. Would a Kelly criterion or fractional Kelly approach to position sizing be more appropriate as the sample grows?

---

## 9. Risk Factors

- **Concentration risk:** Two cities (Lagos, Amsterdam) generated most of the profit. If those cities' delta calibrations are wrong, forward performance will differ significantly.
- **Small sample:** 13 trades over 5 days. Edge may not persist.
- **Resolution source risk:** If Weather Underground changes their data source or processing for any city, our delta calibrations become invalid instantly.
- **Liquidity:** Polymarket temperature markets are thin. At $45/trade we're fine; at $200+/trade we may move the market.
- **Regulatory:** Polymarket's legal status varies by jurisdiction.
