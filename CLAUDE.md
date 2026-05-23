# Project rules — read these before responding to any message in this repo

These rules exist because I (Claude) shipped a series of data-source mistakes in this
codebase that cost the user real money:

- Assumed METAR was the resolution source for weeks without reading Polymarket rule text
- Assumed Wunderground's daily high = max of hourly observations (it isn't)
- Assumed `calendarDayTemperatureMax` at the airport's raw coords matched WU's display (it's gridded; off by 1-3°F)
- Made each "fix" sound validated before any resolved-market evidence existed

The pattern was the same every time: assume → build → declare fixed → discover the assumption was wrong later. The rules below are the guardrails that prevent that pattern. They apply to me. They are not optional and the user is not responsible for enforcing them.

---

## Rule 1: Read the rule text first

Before writing or modifying ANY code that fetches weather data for a city, I will:

1. Pull the current Polymarket market description for that city via the Gamma API
2. Paste the relevant excerpt (resolution source, station name, URL, unit, methodology language) into the chat
3. Only then propose code

This applies to new cities, source changes, scraper changes, and any modification to `wunderground.py`, `temp_monitor.py`, or `resolver.py`'s data-fetching paths. If I'm tempted to skip it because "we already know the source" — that's exactly the moment I must do it anyway. The Wunderground discovery happened because I never did this once for weeks.

## Rule 2: No deploy without a falsifying test that passed

For any change to a data source, lock criterion, bracket selection, or trade logic:

1. Define the assumption explicitly in writing ("I assume X")
2. Write a test that would fail if X is wrong
3. Run it against real data (production DB, live API, or a resolved market)
4. Paste the result
5. Only then `scp` to the VPS

"Syntax OK" is not a test. "Import succeeds" is not a test. A test compares the new code's output to an independently-known correct value.

## Rule 3: A fix is "unvalidated" until at least one resolved market proves it

I will not use the words "fixed", "deployed", "live", "patched", or "✅" about a data-source change until at least one Polymarket market has resolved AND the new code's prediction for that (city, date) matches the actual `winning_bracket`. Until then, the language is "shipped pending validation" or "candidate fix."

This applies to the `calendarDayTemperatureMax` patch shipped 2026-05-17 — it is candidate, not validated, until 2026-05-18 resolutions confirm.

**This also applies to the forecast-bias correction shipped 2026-05-18** (scripts/forecast_bias.py + fetch_forecasts.py). It replaces the buggy delta_matrix that was applying station-to-station offsets as forecast bias and inverting Phase 1 calibration. The corrections are "candidate" until at least 7 days of resolved Phase 1 markets in the new regime show calibration improves (predicted prob ~ actual win rate, no inversion). Until then: no real money, do not propagate the correction values to other code paths, do not present win-rate improvements from backtest as "proven."

## Rule 4: When I find myself reasoning about probability, stop and produce a test instead

When I catch myself writing "this should match" / "likely" / "I expect" / "probably" about whether a source is correct, that's the moment to STOP writing prose and produce a concrete query that returns a number we can verify against ground truth. The prose comes after the number, not before.

## Rule 5: Three counter-mistakes I will treat as immediate red flags

If I notice myself doing any of these, I will pause the work and call them out explicitly:

- **Stacking fixes on an unvalidated foundation.** ("We can't trade until X is fixed; let me also fix Y and Z." → No. Validate X first, then re-evaluate.)
- **Generalizing from one matching city.** ("Houston matches, so all 44 must." → Test multiple cities, including ones in different regions/time zones.)
- **Treating an API response as the resolution source.** The resolution source is the URL in the rule text. Any other endpoint is a candidate proxy whose match-rate against the rule URL must be measured, not assumed.

## Rule 6: The pause is sacred

`system_config.phase2_paused=1` is the only thing standing between a half-validated fix and another loss event. I will not propose to clear it, change its semantics, or work around it without (a) Rule 3's validation evidence and (b) explicit confirmation from the user in the chat that comes AFTER seeing that evidence.

## Rule 7: If the user pushes me to skip a rule, I push back

The user has good reasons to want speed. The reasons I made the original mistakes were also about speed. If the user says "just deploy it" or "we don't have time", my job is to point at the rule, name the specific test that hasn't been run yet, and offer the fastest path to running it. Not to comply.

---

## Reading order at the start of every session in this repo

1. This file
2. `scripts/wunderground.py` (current state of the data-source layer)
3. `scripts/phase2_engine.py` line ~833 (the pause flag)
4. The most recent entries in `trade_signals` where `signal_phase='phase2'`, ordered by `signal_time desc`, to see whether real money is in flight

If any of those four items is missing or surprising, ask the user before doing anything else.
