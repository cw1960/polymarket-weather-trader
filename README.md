# Polymarket Weather Trader

A research and trading system for Polymarket's high-temperature
resolution markets. Identifies expected-value opportunities by
comparing live weather data and forecasts against market-implied
probabilities, with automated signal generation and a hard pause-
flag safety system.

Built solo using AI-assisted development (Claude Code, Cursor).
Deployed to a Linux VPS.

## What it does

- Pulls Polymarket market metadata via the Gamma API and reads the
  resolution rule text directly for each market (station, source,
  methodology, unit).
- Ingests current and forecast weather data from multiple sources
  per city, including Weather Underground and NOAA/METAR feeds.
- Computes calibrated probabilities for each market's resolution
  brackets and flags positive-expected-value entries.
- Runs Phase 1 (forecast-driven entries) and Phase 2 (intraday
  observation-driven entries) engines with separate gating and
  staking logic.
- Records every signal, fill, and resolution to a SQLite database
  for post-hoc calibration analysis.

## Engineering discipline

This project taught me that data-source assumptions are the silent
killer in any system that touches money. The repo's `CLAUDE.md`
file documents the guardrails I now require of myself and of any
AI coding assistant working in the codebase:

1. Read the market's resolution rule text **before** writing the
   data-fetching code — not after.
2. No deploy without a falsifying test against real data. "Syntax
   OK" is not a test.
3. A change is "candidate," not "fixed," until at least one
   resolved market confirms the new prediction matches reality.
4. When tempted to reason in probabilities ("this should match"),
   sto
