-- Phase 2 migration: real-time temperature monitoring + bankroll management
-- Run once in Supabase SQL editor before first use.

-- ── Temperature readings ──────────────────────────────────────────────────────
-- Stores every observed temperature per city per day.
-- running_max_c tracks the daily high seen so far.
-- stable_readings = consecutive 5-min intervals where running_max hasn't increased.
-- bracket_locked = true once we're confident the daily high bracket is determined.
CREATE TABLE IF NOT EXISTS temp_readings (
    id              uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    city            text NOT NULL,
    reading_date    date NOT NULL,
    observed_at     timestamptz NOT NULL DEFAULT now(),
    temp_c          float NOT NULL,
    running_max_c   float NOT NULL,
    source          text NOT NULL,          -- 'metar' | 'openmeteo' | 'hko' | 'jma'
    stable_readings int  NOT NULL DEFAULT 0, -- # consecutive readings at current running_max
    local_hour      int  NOT NULL DEFAULT 0, -- local hour at time of reading (0-23)
    confidence      float NOT NULL DEFAULT 0, -- 0.0-1.0 bracket lock confidence
    bracket_locked  bool NOT NULL DEFAULT false,
    locked_bracket  text,                   -- e.g. "24°C" or "82-83°F"
    phase2_triggered bool NOT NULL DEFAULT false
);

-- Unique: one running_max record per city per day (upserted on each reading)
CREATE UNIQUE INDEX IF NOT EXISTS temp_readings_city_date_uidx
    ON temp_readings (city, reading_date);

-- ── System config (bankroll + tunable parameters) ─────────────────────────────
CREATE TABLE IF NOT EXISTS system_config (
    key         text PRIMARY KEY,
    value       text NOT NULL,
    updated_at  timestamptz DEFAULT now()
);

-- Seed initial values (won't overwrite if already set)
INSERT INTO system_config (key, value) VALUES
    ('bankroll_usd',        '1000.00'),
    ('daily_bankroll_pct',  '0.10'),
    ('phase1_budget_pct',   '0.35'),
    ('phase2_budget_pct',   '0.65'),
    ('min_bankroll_usd',    '700.00'),   -- pause trading below this floor
    ('phase2_min_confidence', '0.70'),   -- minimum lock confidence to trade
    ('phase2_max_yes_price',  '0.85')    -- don't buy Phase 2 above 85¢
ON CONFLICT (key) DO NOTHING;

-- ── trade_signals: add signal_phase + created_at columns ─────────────────────
ALTER TABLE trade_signals
    ADD COLUMN IF NOT EXISTS signal_phase text NOT NULL DEFAULT 'phase1';

ALTER TABLE trade_signals
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

-- Index for daily budget queries (Phase 2 deployed today)
CREATE INDEX IF NOT EXISTS trade_signals_phase_date_idx
    ON trade_signals (signal_phase, created_at);
