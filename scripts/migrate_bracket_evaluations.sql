-- migrate_bracket_evaluations.sql
--
-- Per-bracket-per-cycle log. The point of this table (per the 2026-05-20
-- senior dev review) is to capture the FULL opportunity universe — every
-- bracket evaluated, not just the ones that fired a trade signal. Without
-- this, we cannot distinguish "model has real edge" from "gate cherry-
-- picked a lucky subset of brackets."
--
-- Writers:
--   scripts/phase2_engine.py::_execute_no_sweep   — one row per bracket considered
--   scripts/signal_engine.py                       — one row per bracket evaluated (future)
--
-- Reader:
--   scripts/resolver.py    — backfills winning_bracket / actual_*_win after
--                             the market resolves
--   Mission Control dashboard (future) — calibration scatterplot
--
-- Assumption (CLAUDE.md Rule 2):
--   Every (cycle, city, forecast_date, bracket_label) gets exactly one row
--   per writer invocation. Multiple rows in a day are expected (5-min
--   temp_monitor cycles, multiple signal_engine runs).
--
-- Falsifying test after running:
--   SELECT count(*) FROM bracket_evaluations;                                    -- expect 0 immediately
--   -- Wait for next temp_monitor cycle past 14:00 local for any city:
--   SELECT count(*), cycle, city FROM bracket_evaluations
--     GROUP BY cycle, city ORDER BY count DESC LIMIT 10;
--   -- Expect: rows from cycle='phase2_sweep' with multiple brackets per city.

CREATE TABLE IF NOT EXISTS bracket_evaluations (
  id              BIGSERIAL PRIMARY KEY,
  evaluated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  cycle           TEXT         NOT NULL,                      -- 'phase1' | 'phase2_sweep' | 'phase2_lock'
  city            TEXT         NOT NULL,
  forecast_date   DATE         NOT NULL,
  condition_id    TEXT,
  market_id       TEXT,
  bracket_label   TEXT         NOT NULL,
  bracket_low_c   NUMERIC(8,3),
  bracket_high_c  NUMERIC(8,3),
  yes_price       NUMERIC(6,4),
  no_price        NUMERIC(6,4),
  model_prob_yes  NUMERIC(6,4),
  model_prob_no   NUMERIC(6,4),
  edge_yes        NUMERIC(6,4),
  edge_no         NUMERIC(6,4),
  pass_min_prob   BOOLEAN,
  pass_edge       BOOLEAN,
  gate_passed     BOOLEAN,                                    -- pass_min_prob AND pass_edge
  side_selected   TEXT,                                       -- 'YES' | 'NO' | null
  ranked_position INTEGER,                                    -- nth in top-N selection (1-based); null if not selected
  size_usd        NUMERIC(10,2),                              -- proposed size; 0.01 = observation/paper, 0 = not selected
  signal_id       UUID,                                       -- FK to trade_signals.id if a row was actually written
  guardrail_block TEXT,                                       -- name of blocking guardrail, or null

  -- Resolved later by resolver.py:
  winning_bracket TEXT,
  actual_yes_win  BOOLEAN,
  actual_no_win   BOOLEAN,
  resolved_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS bracket_evaluations_city_date_cycle
  ON bracket_evaluations (city, forecast_date, cycle);

CREATE INDEX IF NOT EXISTS bracket_evaluations_evaluated_at
  ON bracket_evaluations (evaluated_at);

CREATE INDEX IF NOT EXISTS bracket_evaluations_unresolved
  ON bracket_evaluations (forecast_date, resolved_at)
  WHERE resolved_at IS NULL;
