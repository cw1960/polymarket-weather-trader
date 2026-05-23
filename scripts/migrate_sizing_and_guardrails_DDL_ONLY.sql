-- migrate_sizing_and_guardrails_DDL_ONLY.sql
--
-- Run this in the Supabase SQL Editor. The system_config rows from the full
-- migration have already been inserted programmatically; this file only
-- contains the two table DDLs + the week-1 sizing row.
--
-- Assumption (CLAUDE.md Rule 2):
--   Running this produces two new tables (sizing_schedule, guardrail_events)
--   and a single row in sizing_schedule for week 1.
--
-- Falsifying test:
--   SELECT count(*) FROM sizing_schedule;     -- expect 1
--   SELECT count(*) FROM guardrail_events;    -- expect 0
--   SELECT * FROM sizing_schedule;            -- expect week_1 row, $3 YES / $5 NO

CREATE TABLE IF NOT EXISTS sizing_schedule (
  id                            SERIAL PRIMARY KEY,
  week_label                    TEXT        NOT NULL,
  start_date                    DATE        NOT NULL,
  end_date                      DATE        NOT NULL,
  phase2_yes_size_usd           NUMERIC(10,2) NOT NULL,
  phase2_no_sweep_size_usd      NUMERIC(10,2) NOT NULL,
  phase2_no_sweep_max_per_city  INTEGER     NOT NULL DEFAULT 3,
  deployment_cap_pct            NUMERIC(5,2) NOT NULL DEFAULT 50.0,
  kelly_fraction                NUMERIC(5,3) NOT NULL DEFAULT 0.0,
  notes                         TEXT,
  created_at                    TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT sizing_schedule_date_range CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS sizing_schedule_dates
  ON sizing_schedule (start_date, end_date);

INSERT INTO sizing_schedule
  (week_label, start_date, end_date,
   phase2_yes_size_usd, phase2_no_sweep_size_usd,
   phase2_no_sweep_max_per_city, deployment_cap_pct, kelly_fraction,
   notes)
VALUES
  ('week_1', '2026-05-20', '2026-05-26',
   3.00,    -- YES lock size (small; YES side untested by backtest)
   5.00,    -- NO sweep per-bracket size
   3,       -- max NO brackets per city
   30.0,    -- deploy at most 30% of bankroll at any moment in week 1
   0.000,   -- flat sizing (no kelly)
   'Week 1: data collection. Flat $5 NO / $3 YES. Validate calibration.')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS guardrail_events (
  id            SERIAL PRIMARY KEY,
  fired_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  guardrail     TEXT        NOT NULL,
  details_json  JSONB,
  resolved_at   TIMESTAMPTZ,
  resolved_by   TEXT
);
