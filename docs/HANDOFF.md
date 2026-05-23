# Handoff — Polymarket Weather Trading Bot

**Last updated:** 2026-05-19
**Read order:** CLAUDE.md first (behavioral rules), then this document, then `scripts/forecast_bias.py` and `scripts/wunderground.py` to see the most recent fixes in code.

If you're a new conversation picking this project up: this document is your entire onboarding. Do not start working until you've read it end to end.

---

## 1. What we are building

A real-money trading bot that predicts daily high temperatures at airport weather stations and trades the prediction markets on Polymarket to profit from those predictions.

**The user's stated goal, verbatim:** "the most highly accurate and highly profitable high temperature bot on the planet." This is not an academic exercise. The user has stated repeatedly that it is "life and death serious." Treat that seriously.

**The market structure we're trading.** Polymarket lists daily prediction markets for ~50 cities globally. Each market asks: "Will the highest temperature in [City] be [bracket] on [date]?" Brackets are 1°F (US cities) or 1°C (everywhere else) wide. The market resolves to the bracket containing the day's published official daily high at the named airport weather station. Buyers of the winning bracket's YES token get $1 each at resolution; all other YES tokens go to $0. Prices fluctuate during the day between ~1¢ and ~99¢ based on traders' assessments of which bracket will win.

**The economic edge we're trying to capture.** Three potential sources of edge over the market:

1. **Better daily-high forecasts than the market consensus prices in** — by combining multiple ensemble forecast sources (GFS, ECMWF, ICON, UKMO, etc.) and adjusting for known biases per city
2. **Better live-temperature reading than competitors** — by reading the exact data source the market resolves against (Wunderground's `calendarDayTemperatureMax` for 44 cities), so we know which bracket is about to win before the market fully prices it
3. **Better lock-timing decisions** — knowing when the daily peak is genuinely "in" so we can buy YES on the right bracket at a price discount before resolution

Whether ANY of these edges actually exists in practice is currently unproven. This week we discovered that two upstream bugs were corrupting our predictions to the point where the bot was systematically betting *against* itself. Those bugs are now fixed (candidate fixes, unvalidated). Whether the underlying model has real predictive edge, beneath those bugs, is what the next 1-2 weeks of validation data will reveal.

---

## 2. Where we are right now (2026-05-19)

**Financial position:**
- Initial deposit: **$499 USDC** on 2026-05-13
- Current Polymarket cash: **$60.21**
- Open positions: **0** (all weather markets were mass-archived by Polymarket due to their own oracle bug; refunds pending)
- Expected incoming refunds/payouts within 7-10 business days: **$331 to $531** (depending on whether Polymarket refunds all losing positions broadly or only oracle-error losses)
- After refunds: cash will be roughly $391 to $591 — most of the original $499 should be back

**Trading status:**
- `system_config.phase2_paused = 1` — ALL real-money trading suspended
- Phase 1 observation rows continue ($0.01 paper trades, for calibration data only)
- Polymarket has TEMPORARILY SHUT DOWN ALL WEATHER MARKETS following their oracle bug disclosure on 2026-05-18. Expected reopen: 2026-05-20 to 2026-05-21
- We will not unpause real-money trading until: (a) Polymarket reopens, (b) CLOB API stability is verified, (c) the candidate fixes from this week show evidence of working on fresh resolved markets

**System state:**
- All cron jobs running on VPS (216.238.81.206)
- Dashboard live on Netlify (https://weatherornotbot.netlify.app)
- Bias-audit cron capturing daily Wunderground-vs-METAR comparisons
- Two candidate fixes deployed this week but NOT YET VALIDATED — see Section 6

---

## 3. The trading strategy in detail

The bot operates in three distinct phases per market per day:

### Phase 1 — Morning forecast bracket distribution

Fires at 06:00, 12:00, 18:00, 00:00 UTC via `scripts/signal_engine.py`.

**For each city, each day:**
1. Fetch latest ensemble forecasts via Open-Meteo (82 members: 31 GFS + 51 ECMWF) plus 6 deterministic models
2. Apply per-city forecast bias correction (added 2026-05-18 — see `scripts/forecast_bias.py`)
3. Compute a probability distribution over the day's 1° brackets via normal CDF in `scripts/compute_probabilities.py`
4. Apply distance-from-mean filter (only consider brackets within 2σ) and calibration factors in `scripts/ladder.py`
5. For each bracket where our model probability > market price by some edge threshold: record a Phase 1 signal
6. Currently Phase 1 deploys $0.01 observation-only — no real money. Used purely for calibration tracking and Brier score measurement

**Why Phase 1 exists:** to build a calibration signal — over time, we can measure which brackets we predict well and which we don't. The Phase 1 record is also the input data for computing per-city forecast bias corrections.

**Phase 1's current measured win rate:** ~34% across 1000 resolved signals. This was distorted by the corrupted forecast bias system. Post-fix expected win rate (from backtest, unvalidated): 45-50% with top-1 bracket pick per market.

### Phase 2 — Live temperature lock-and-bet

Fires on every temp_monitor cycle (every 5 minutes) via `scripts/phase2_engine.py`, when conditions are met.

**For each city, each cycle:**
1. Read live temperature from Wunderground's `calendarDayTemperatureMax` (or METAR fallback for non-Wunderground cities)
2. Update `running_max_c` if a new high was reached
3. Check stability: has `running_max_c` been unchanged for ≥120 minutes (PHASE2_STABLE_READINGS=24 * 5min cycles)?
4. Check confidence: is bracket-lock confidence ≥ 0.80 (factoring local hour, stability, sky conditions, trend)?
5. Check time: is local hour ≥ 13:00? Local hour gate prevents firing too early in the day
6. If all gates pass: identify the bracket containing `running_max_c + delta_c` (delta_c was reset to 0 yesterday for Wunderground cities)
7. Apply price gate: YES price must be < $0.30 (PHASE2_MAX_CALIBRATED_PRICE)
8. Apply bracket-blacklist gate: skip if a tracked high-conviction trader (Weatherstappen) holds NO at $0.95+ on this exact bracket
9. Place real-money YES buy at $15 (PHASE2_CALIBRATED_TRADE_USD) when not paused

**Why Phase 2 exists:** this is where the actual money is made (or lost). Phase 1 is an observer; Phase 2 is the trader. By waiting until late in the day with a temperature plateau, we have much higher confidence than morning forecasts could provide. Combined with the 30¢ price cap, the EV math relies on win rate > ~30% to be net positive.

**Phase 2's current measured win rate:** ~36% across 14 historical real-money trades, net P&L +$103 (dominated by one outlier: Madrid +$139). Without the outlier, Phase 2 was approximately breakeven historically. This is on data that includes the bugs we've since fixed.

### NO Sweep — Short over-priced cold-tail brackets

Fires from `scripts/phase2_engine.py:_execute_no_sweep` once per day per city after 14:00 local.

When live `running_max_c` is comfortably above a bracket boundary (say running_max = 28°C, bracket = "21°C"), the YES token on that 21°C bracket should be near $0. If it's priced higher than that (say at 4¢), we can buy NO at $0.96 and collect $1 at resolution — small per-trade but high-probability.

Currently disabled by the global pause flag along with Phase 2.

---

## 4. System architecture

### Where things run

| Component | Where | Purpose |
|---|---|---|
| Python signal/execution scripts | VPS (216.238.81.206), cron-driven | Main bot logic |
| TypeScript CLOB shim (`server.mjs`) | Same VPS, persistent systemd service | Polymarket order signing (Python lib has L1 auth bug) |
| Supabase Postgres | Cloud | All state — signals, trades, readings, config |
| React+Vite dashboard | Netlify (weatherornotbot.netlify.app) | Live monitoring UI |
| Analyzer worker (FastAPI) | Same VPS | Trader-analysis dashboard (separate from trading) |

### Data flow

```
                                                  
   Open-Meteo                  Wunderground       
  (ensemble +                  (api.weather.com)  
  deterministic               via calendarDay-    
   forecasts)                  TemperatureMax     
        │                              │           
        ▼                              ▼           
  fetch_forecasts.py            temp_monitor.py   
  + forecast_bias.py            + wunderground.py 
        │                              │           
        ▼                              ▼           
  signal_engine.py    ◄──────►   phase2_engine.py 
  (Phase 1, paper)               (Phase 2, real $)
        │                              │           
        └──────► trade_signals ◄───────┘           
                        │                          
                        ▼                          
                  resolver.py (after market closes)
                        │                          
                        ▼                          
                trade_signals.pnl_usd              
```

### Cron schedule (on VPS)

```
*/5 * * * *  temp_monitor.py          # Live temperature poll
*/5 * * * *  reconcile_manual_buys.py # Detect user's manual Polymarket buys
*/5 * * * *  reconcile_manual_sales.py
*/10 * * * * watchdog.py              # Detect stuck components
0 */6 * * *  signal_engine.py         # Phase 1 fires (06:00, 12:00, 18:00, 00:00 UTC)
0 * * * *    resolver.py              # Hourly market resolution check
5 * * * *    reporter.py              # Hourly metrics rollup
15 * * * *   backfill_fills.py        # Catch missed order fills
20 * * * *   sync_bracket_blacklist.py # Track Weatherstappen NO positions
30 2 * * *   phase2_engine.py reconcile # Daily bankroll reconcile
10 0 * * *   daily_summary.py         # End-of-day summary
0 23 * * *   wunderground_bias_audit.py # Daily Wunderground vs METAR snapshot
```

### Key database tables

- `trade_signals` — every Phase 1 and Phase 2 signal, with bet, fill, P&L, resolution
- `temp_readings` — live temperature snapshots per (city, date)
- `ensemble_forecasts` — Open-Meteo forecast data
- `ladders` — Phase 1 bracket ladders per (city, date)
- `system_config` — runtime flags (phase2_paused, bankroll_usd, etc.)
- `resolution_stations` — city → station_id + delta_c (delta_c reset to 0 for WU cities on 2026-05-17)
- `forecast_bias_corrections` — NEW table created 2026-05-18, per-city forecast bias values (shadow of `scripts/forecast_bias.py`)
- `bracket_blacklist` — tracked-trader NO positions to avoid
- `delta_matrix` — LEGACY table, was misused for forecast bias, no longer read after 2026-05-18

### Key configuration constants (scripts/config.py)

```python
PHASE2_FIXED_DAILY_USD     = 120.0   # Daily budget for Phase 2
PHASE2_MAX_TRADE_USD       = 15.0    # Per-trade cap
PHASE2_CALIBRATED_TRADE_USD = 15.0   # Per-trade size for calibrated cities
PHASE2_CALIBRATION_MIN_SAMPLES = 2   # Need 2+ samples to be "calibrated"
PHASE2_MAX_CALIBRATED_PRICE = 0.30   # Don't buy YES above 30¢
PHASE2_MAX_YES_PRICE       = 0.85    # Absolute upper bound
PHASE2_MIN_CONFIDENCE      = 0.80    # Bracket-lock confidence floor
PHASE2_MIN_LOCAL_HOUR      = 13      # Don't fire before 1pm local
PHASE2_STABLE_READINGS     = 24      # 120-min plateau required (was 12 / 60min)
```

---

## 5. What "success" looks like

The user's metric is profitability. Operationally, this decomposes into:

**Accuracy targets (necessary but not sufficient for profit):**
- Brier score for Phase 1 signals: **< 0.15** (currently 0.25-0.28 — broken)
- Phase 1 calibration: predicted prob ≈ actual win rate, no inversion (currently inverted, fix shipped 5/18, unvalidated)
- Phase 1 win rate on top-1 picks: **≥ 50%** (currently 34% with bugs; backtest suggests 49.6% after fix)
- Phase 2 win rate: **≥ 65%** (currently 36% on n=14 — small sample, post-fix unknown)

**Financial targets:**
- Net positive P&L over a 4-week rolling window
- Maximum drawdown below 30% from peak
- Eventually: 50%+ ROI on deployed capital per year

**Stability targets:**
- Zero days where a known-bug class causes losses (the failures of this week)
- All real-money trades pass validation checklist before deployment
- No silent failures — every loss is attributable to an understood cause

**Coverage targets:**
- All 44 Wunderground cities with calibrated δ data
- All 4 non-Wunderground cities with appropriate scrapers (weather.gov for Istanbul/Moscow/Tel Aviv, HKO for Hong Kong)
- 5+ Phase 2 trades per day average across the calendar

The system is currently nowhere near any of these targets. The discovery this week was that we couldn't even measure these targets accurately because the underlying data and model were corrupted. With the candidate fixes deployed (Wunderground source + forecast bias correction), the next 2-4 weeks of clean data will tell us whether we have a real edge.

---

## 6. The two candidate fixes shipped this week (UNVALIDATED)

### Fix 1: Wunderground data source (shipped 2026-05-17)

**Files:** `scripts/wunderground.py` (new), `scripts/temp_monitor.py` (rewired)

**The bug it fixes:** Until 2026-05-17 the bot read METAR from `aviationweather.gov`. Polymarket's resolution source is Wunderground's daily history page. These can differ by 1-3°F because:
- METAR captures sub-hourly readings including peaks Wunderground misses
- Wunderground may include SPECI/1-min ASOS data METAR-via-v1 misses
- Wunderground's specific geocode-based gridded value may differ from station-direct readings

**The fix:** Read `calendarDayTemperatureMax` from `api.weather.com/v3/wx/forecast/daily/5day` at Wunderground's canonical geocode per airport (extracted from each city's wunderground.com history page SSR transfer state). For each (city, date), this returns the same value Wunderground displays — verified empirically for 7 cities on 5/17.

**Validation status:** UNPROVEN. The `wunderground_bias_audit.py` cron is capturing daily snapshots. Comparing those snapshots to actual Polymarket resolutions over the next 7+ days is the validation.

**What we don't know:**
- Whether the canonical geocodes change over time (if Wunderground updates them, our hardcoded coords drift)
- Whether the 4 weather.gov cities (Istanbul/Moscow/Tel Aviv) and HKO (Hong Kong) need their own scrapers (deferred work, not yet built)

### Fix 2: Forecast bias correction (shipped 2026-05-18)

**Files:** `scripts/forecast_bias.py` (new), `scripts/fetch_forecasts.py` (rewired), `forecast_bias_corrections` Supabase table (new)

**The bug it fixes:** Phase 1's calibration was inverted — predict 5%, actual 43%; predict 35%, actual 18%. Brier skill score −0.25 (worse than guessing the base rate). Root cause: `delta_matrix` table was computing `resolution_station_temp − comparison_station_temp` (station-to-station offset on historical observation days) but `fetch_forecasts.py` was adding that value to forecast means as if it were forecast-vs-actual bias. For many cities the sign was wrong, making cold-biased forecasts even colder.

**The fix:** New `forecast_bias.py` module holds per-city corrections computed as `median(winning_bracket_mid_c − stored_forecast_mean_c)` over historical resolved Phase 1 markets. Applied as `corrected_mean = mean + get_correction(city)`. Safety rules: require n≥5 samples, cap absolute correction at ±2.0°C, hardcoded 0 for Tel Aviv (weather.gov source — separate pipeline issue) and Denver (n=5 outlier inconsistent with US neighbors).

**Active corrections:** 25 cities. Largest are NYC +2.00, Chengdu +2.00, Shanghai +2.00, Kuala Lumpur +2.00 (all capped from larger raw medians).

**Backtest signal:** Top-1 bracket pick win rate moves from 34% to 49.6%. Brier skill from −0.25 to −0.10. **THIS IS A BACKTEST ON OVERLAPPING DATA, NOT A VALIDATION.**

**Validation status:** UNPROVEN. Need 7+ days of clean Phase 1 outcomes post-deploy.

**What we don't know:**
- Whether the historical data used to compute bias was polluted by Polymarket's oracle bug (Miami, Mexico City, Seoul, Hong Kong explicitly named as affected — could have skewed those cities' bias estimates)
- Whether the cap at ±2.0°C is too tight or too loose
- Whether the forecast std is also wrong (backtest suggests under-confidence at high p — std may be too wide)

---

## 7. Earlier fixes this week, also unvalidated

- **`PHASE2_STABLE_READINGS` 12 → 24** (60-min → 120-min plateau before lock fires). Premature locks were 5 of 6 May 13-17 losses.
- **`_is_trend_flat` threshold ≤0.1°C → ≤0.0°C** (any net rise disqualifies "flat")
- **`signalToTrade` (dashboard)** prefers `filled_size_usd`/`fill_price` over `recommended_position`/`market_price` for partial-fill accuracy
- **Executor data-api backstop** runs on any partial fill (matched>0), not just terminal status — prevents Houston-style $15→$3 drift
- **Manual-buy reconciler** uses cost-basis gap, not condition_id existence — closes the bug that hid ~$400 of manual buys behind $0.01 observation rows
- **Resolver skips `order_status='observation'` rows** so downgraded trades don't accrue fictitious P&L

Each is a real correction to a real bug. None has been validated against post-fix live data because the markets have been offline since 5/17.

---

## 8. Polymarket's own bug (separate from ours)

On 2026-05-18 Polymarket disclosed that their oracle batching system had been mis-indexing weather market resolutions. The 2026-05-19 follow-up clarified: **oracle failed due to missing Weather Underground data, causing markets to resolve to MINIMUM temperature brackets incorrectly.** Affected: Miami, Mexico City, Seoul, Hong Kong explicitly named, plus Atlanta + Toronto May 17 (mass-archived without resolution).

Their refund policy:
- Losing positions: receive refunds (interpretation ambiguous — may be all losing or only oracle-error losing)
- Winning positions: paid out per actual weather data
- Timeline: 7-10 business days, possibly faster

**Our specific situation:**
- Atlanta 88-89°F May 17 ($109 cost, actual high 89°F): expect ~$155 YES payout
- Toronto 26°C May 17 ($102 cost, actual high 26.1°C): expect ~$176 YES payout
- Miami 84-85°F May 17 ($200 cost, actual high 87°F): uncertain refund

Markets shut down 2026-05-17/18. Expected reopen: 2026-05-20 to 2026-05-21.

**Operational issues to expect on reopen (per 2026-05-19 digest):**
- CLOB API "invalid signature" / "maker address not allowed" errors widespread
- 504 timeouts on `/order`
- Latency 1500ms+ (was briefly 300-500ms before regressing)
- Silent fills, limit orders cancelled without user action
- Missing 8-hour windows of trade history

We must verify our CLOB signing works with a test order BEFORE deploying real money post-reopen.

---

## 9. The bigger question: do we have an edge?

This is what next 2-4 weeks of data should answer. Honest current state:

**What we know:**
- The model was fundamentally broken (inverted calibration). Bugs 1 and 2 explain most of why losses were systematic
- The fixes are mathematically correct in direction; backtest is suggestive
- We have full coverage of resolution sources for 44/50 cities

**What we don't know:**
- Whether the underlying Phase 1 model has any real edge above market consensus pricing
- Whether the Phase 2 lock-timing logic identifies brackets the market hasn't already priced
- Whether other smart traders on Polymarket already use similar data sources and our edge is mostly arbed away
- Whether the user's available capital ($499 starting, currently ~$60 + pending refunds) is sufficient to weather the variance of even an EV-positive strategy

**The honest skeptical case:** Polymarket weather markets are attractive to sophisticated traders. The same Open-Meteo ensemble forecasts we use are publicly accessible. The same Wunderground data is publicly displayed. If we have an edge it's likely small (maybe 2-5% over market pricing) and only realized through disciplined execution. A small edge with 50% variance over a $500 bankroll is a long, painful path to "highly profitable."

**The honest optimistic case:** Most traders don't bother to extract Wunderground's exact canonical geocode, blend ensemble forecasts properly, and calibrate per-city bias. If we do all three correctly we may be in a small minority of operators with truly clean data. The 30¢ price cap concentrates us on brackets with 70%+ implied tails, where small accuracy advantages compound. Madrid +$139 on a single trade demonstrates that big winners exist.

Where the truth lies between these two cases is what 2-4 weeks of post-fix clean data will reveal. **Do not commit additional capital until that data exists.**

---

## 10. What we're NOT doing (intentionally deferred)

These are real, valuable improvements that I have deliberately not started, because the discipline this week says: **fix one thing, validate one thing, then move on.** Stacking changes is what got us into the mess we're climbing out of.

| Item | Why deferred | Estimated effort |
|---|---|---|
| Synoptic Data scraper for weather.gov cities (Istanbul/Moscow/Tel Aviv) | 3 cities, low volume, can wait until Wunderground fix is validated | ~30 min build, ~1 day to verify |
| HKO Daily Extract parser for Hong Kong | 1 city only | ~30 min build |
| Recompute script for forecast bias | Manual upsert works for now; need scheduled refresh later | ~1 hour |
| Forecast std tightening | Backtest shows under-confidence; structural change needs its own backtest first | ~2 hours analysis + deploy |
| Watchdog upstream-awareness | Phantom alerts during Polymarket downtime annoying but not money-affecting | ~1 hour |
| Dashboard live-MTM column | Existed earlier; broken by yesterday's positions disappearing; needs separate "recently resolved" panel | ~2 hours |
| Multi-model forecast blending | Current code blends GFS+ECMWF; ICON and UKMO have lower bias per audit | ~4 hours analysis + deploy |
| Per-bracket Kelly sizing | Currently fixed-$15-per-trade; smarter sizing could lift expected return | ~3 hours + backtest |
| Auto-refund tracking | Manual checking of Polymarket activity feed; should be automated | ~2 hours |
| CLOB stability monitor | Detect "invalid signature" errors and auto-disable trading until resolved | ~2 hours |

**The rule for deferred work: do not pull any of these forward without explicit user authorization. Stacking unvalidated changes is how the prior 9 weeks generated the bugs we just fixed.**

---

## 11. Specific operational state to check before doing anything

Before any new conversation does any work on this project, run these checks. If any returns unexpected results, escalate before proceeding.

```python
# Polymarket cash + positions
import requests
WALLET = "0x24AcEc88dAd000c36E706Cb4041d4d553FB2C567"
pos = requests.get('https://data-api.polymarket.com/positions',
                   params={'user': WALLET}).json()
# Expected: 0 positions, until markets reopen and we open new ones
```

```sql
-- Pause flag (MUST be '1' unless validation has occurred)
SELECT value FROM system_config WHERE key = 'phase2_paused';

-- Bot's tracked bankroll (should equal Polymarket cash balance)
SELECT value FROM system_config WHERE key = 'bankroll_usd';

-- Recent real-money trades since pause (should be ZERO)
SELECT count(*) FROM trade_signals
WHERE signal_phase = 'phase2'
  AND order_status = 'filled'
  AND filled_size_usd > 1
  AND signal_time > '2026-05-18T00:00:00';

-- Forecast bias corrections active
SELECT city, bias_c, n_samples FROM forecast_bias_corrections WHERE bias_c <> 0 ORDER BY bias_c DESC;
-- Expected: 25 rows
```

```bash
# VPS cron status
ssh root@216.238.81.206 'crontab -l | grep -c wunderground_bias_audit'
# Expected: 1

# Recent bias-audit captures
ssh root@216.238.81.206 'wc -l /root/polymarket/logs/wu_bias_daily.csv'
# Expected: increasing daily by ~44 rows

# Recent temp_monitor log activity (should not be flooded with errors)
ssh root@216.238.81.206 'tail -20 /root/polymarket/logs/temp_monitor.log'
```

---

## 12. Unpause checklist (the gate that must pass before any real-money trade)

This is the only thing that should clear `phase2_paused` back to `'0'`. Every item must pass independently. No shortcuts.

1. **Polymarket has formally reopened weather markets** — verifiable by checking Gamma API for fresh markets on multiple cities for tomorrow's date
2. **Re-read the rule text on a fresh market** — Polymarket may have changed the resolution methodology, station list, or URL patterns as part of their oracle fix. Compare to what's hardcoded in `scripts/wunderground.py:STATION_LATLON` and rule-text history. If anything changed, the fixes from this week may need updating.
3. **Wunderground source validation: 3+ cities cross-checked** — pull our `calendar_max_f` from `wu_bias_daily.csv`, open each city's Wunderground history page in the browser, confirm match for the same UTC date. 3/3 cities must match exactly.
4. **Expected refunds have landed** — bot's `bankroll_usd` matches Polymarket cash balance, and the cash balance reflects the expected $331-$531 in refunds/payouts
5. **CLOB signing works** — place a test order at $0.01 size, confirm it submits without "invalid signature" / "maker address not allowed" errors
6. **At least 1 resolved Phase 1 market post-reopen shows calibration is no longer inverted** — predicted prob roughly correlated with actual outcome, no high-prob brackets losing systematically
7. **48-hour observation period** of all components running cleanly after Polymarket reopens — even if all above passes, wait 48 hours for residual reopen-day bugs to surface

When all 7 pass, unpause with **reduced trade size** (`PHASE2_CALIBRATED_TRADE_USD = 5.0` instead of 15.0) for at least 7 more days. Only return to full size after 7 days of clean P&L data at the reduced size.

---

## 13. Behavioral rules I committed to

Full text in `CLAUDE.md`. Summary of the 7 rules, with what each prevents:

1. **Read Polymarket rule text first** — prevents the METAR/Wunderground source bug class
2. **No deploy without a falsifying test passed against real data** — prevents "should work, ship it" failures
3. **A fix is candidate until ≥1 resolved market validates** — prevents premature celebration
4. **When reasoning about probability, stop and write a concrete test** — prevents narrative-driven analysis
5. **Three red flags: stacking on unvalidated foundation; generalizing from one city; treating API response as resolution source** — prevents this week's specific failure patterns
6. **The pause flag is sacred** — prevents revenue-pressure overrides
7. **Push back if asked to skip these rules** — prevents user-pressure overrides

These rules exist because I violated each of them at least once this week and each violation cost real money or real user time. If a future me sees these and is tempted to bypass: don't. The user is not responsible for catching every violation. Self-policing is the rule.

---

## 14. Recent history (last 7 days)

| Date | Event |
|---|---|
| 2026-05-13 | Initial $499 USDC deposit. Bot starts live trading. |
| 2026-05-14 | First wave of Phase 2 real-money trades. Madrid wins +$139 (carries the week). |
| 2026-05-16 | Toronto + Dallas wins (+$356) credited 5/17 AM. |
| 2026-05-17 AM | Manual buys totaling ~$430 (Miami $200, Atlanta $109, Toronto $100, others). |
| 2026-05-17 PM | METAR vs Wunderground source mismatch discovered. Real-money trading paused. Wunderground reader patch shipped. |
| 2026-05-17 PM | Wrong-geocode bug in patch discovered. Re-shipped with WU canonical geocodes. |
| 2026-05-18 AM | Polymarket digest: oracle batching bug affected Miami/Mexico City/Seoul/Hong Kong. Refunds promised. Atlanta + Toronto May 17 mass-archived without resolution. Weather markets shut down platform-wide. |
| 2026-05-18 PM | Audit revealed Phase 1 calibration INVERTED. Traced to `delta_matrix` adding station-to-station offsets as forecast bias. Forecast bias correction shipped (Fix 2). |
| 2026-05-19 AM | Polymarket digest confirmed oracle root cause: missing Wunderground data, markets resolved to minimum brackets incorrectly. Refund terms clarified. Markets expected to reopen 5/20-21. CLOB API still unstable. |

---

## 15. How a new context should start work

1. Read `CLAUDE.md` cover to cover. These are the behavioral rules.
2. Read this document cover to cover. This is the project state.
3. Check Section 11's queries to verify current operational state.
4. Ask the user what they want to focus on. Do not assume.

If the user asks for "morning report" or "status update": run Section 11's queries plus check the bias-audit CSV, summarize, ask what to do next.

If the user asks for changes to the bot: confirm we are still within the unpause-checklist gate. If Phase 2 is still paused, validation work and deferred-list items are appropriate. Real-money-touching code changes are not appropriate until validation passes.

If the user asks me to skip a rule from CLAUDE.md: push back, name the rule, name the test that hasn't run yet, offer the fastest path to running the test.

If you find yourself proposing to deploy multiple fixes simultaneously: stop. Ship one, validate, then ship the next. The discipline of "one change, one validation" is what we lost during the prior 9 weeks of work and what we're trying to recover now.

The bot is paused. Refunds are inbound. The markets are offline. There is nothing on fire and nothing requires urgent deployment. The patient, structured path is the right one.

---

End of handoff. When in doubt, prefer NOT to deploy.
