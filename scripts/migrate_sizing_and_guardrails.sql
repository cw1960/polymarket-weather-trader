-- migrate_sizing_and_guardrails.sql
-- Schema for week-by-week sizing + four operational guardrails.

-- ── sizing_schedule table ─────────────────────────────────────────────────
-- One row per calendar week describing trade sizes and deployment caps.
-- Phase2 engine reads the row whose [start_date, end_date] contains today.
CREATE TABLE IF NOT EXISTS sizing_schedule (
  id                            SERIAL PRIMARY KEY,
  week_label                    TEXT        NOT NULL,
  start_date                    DATE        NOT NULL,
  end_date                      DATE        NOT NULL,
  phase2_yes_size_usd           NUMERIC(10,2) NOT NULL,
  phase2_no_sweep_size_usd      NUMERIC(10,2) NOT NULL,
  phase2_no_sweep_max_per_city  INTEGER     NOT NULL DEFAULT 3,
  deployment_cap_pct            NUMERIC(5,2) NOT NULL DEFAULT 50.0,
  kelly_fraction                NUMERIC(5,3) NOT NULL DEFAULT 0.0,   -- 0 = flat sizing
  notes                         TEXT,
  created_at                    TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT sizing_schedule_date_range CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS sizing_schedule_dates
  ON sizing_schedule (start_date, end_date);

-- Week 1 only.  Weeks 2-6 are intentionally left blank until week 1
-- live data validates the gate; see CLAUDE.md Rule 5.
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
   0.000,   -- flat sizing in week 1 (no kelly)
   'Week 1: data collection.  Flat $5 NO / $3 YES.  Validate calibration.')
ON CONFLICT DO NOTHING;

-- ── guardrail / new-gate config rows ──────────────────────────────────────
INSERT INTO system_config (key, value) VALUES
  ('phase2_yes_locks_enabled',   '0'),     -- master switch for YES locks; flip to '1' after first week
  ('phase2_min_edge',            '0.08'),  -- 8 percentage points
  ('phase2_min_model_prob_gate', '0.55'),  -- model_prob floor for new gate
  ('min_bankroll_usd_trading',   '1500'),  -- auto-pause floor (covers week-2+ bankroll levels)
  ('max_daily_loss_pct',         '0.08'),  -- 8% of bankroll loss → halt trading for the day
  ('min_3day_win_rate',          '0.45'),  -- below this over rolling 3 days → auto-pause
  ('min_3day_resolved_trades',   '15'),    -- need this many resolved trades before win-rate check applies
  ('today_loss_paused_date',     ''),      -- iso date written when daily loss limit hits
  ('auto_pause_reason',          '')       -- last reason auto-pause fired (human-readable)
ON CONFLICT (key) DO NOTHING;

-- ── audit table: every time auto-pause fires, log who/why ─────────────────
CREATE TABLE IF NOT EXISTS guardrail_events (
  id            SERIAL PRIMARY KEY,
  fired_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  guardrail     TEXT        NOT NULL,   -- 'bankroll_floor', 'daily_loss', '3day_win_rate'
  details_json  JSONB,
  resolved_at   TIMESTAMPTZ,
  resolved_by   TEXT
);
