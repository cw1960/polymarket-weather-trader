-- migrate_historical_backfill.sql
--
-- Tables for the historical backfill that powers the Trader app's backtest
-- harness. Kept completely separate from the live `bracket_price_history`
-- table so a re-backfill never corrupts realtime data.
--
-- Run in Supabase SQL Editor. Idempotent.

-- ── Per-bracket price history (CLOB prices-history series) ─────────────
CREATE TABLE IF NOT EXISTS historical_bracket_prices (
  city                  TEXT          NOT NULL,
  forecast_date         DATE          NOT NULL,
  bracket_label         TEXT          NOT NULL,
  bracket_unit          TEXT,
  bracket_low_native    NUMERIC(7,2),
  bracket_high_native   NUMERIC(7,2),
  condition_id          TEXT          NOT NULL,
  yes_token_id          TEXT,
  recorded_at           TIMESTAMPTZ   NOT NULL,
  yes_price             NUMERIC(6,4)  NOT NULL,
  -- One row per (bracket, timestamp). Repeated backfills overwrite.
  PRIMARY KEY (condition_id, recorded_at)
);
CREATE INDEX IF NOT EXISTS hbp_city_date
  ON historical_bracket_prices (city, forecast_date, recorded_at);

-- ── Per-event resolution outcomes ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS historical_event_resolutions (
  city                    TEXT          NOT NULL,
  forecast_date           DATE          NOT NULL,
  winning_bracket_label   TEXT,
  winning_condition_id    TEXT,
  day_max_temp_c          NUMERIC(6,3),
  day_max_temp_f          NUMERIC(6,2),
  -- Closest hour the day-max was observed at, in city-local time
  day_max_local_hour      SMALLINT,
  source                  TEXT,           -- e.g. 'wunderground','gamma_outcome'
  fetched_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (city, forecast_date)
);

-- ── Per-city hourly observation backfill ───────────────────────────────
-- One row per WU hourly observation we pulled. Same schema lets the
-- backtest replay "what did the trader see at hour H?"
CREATE TABLE IF NOT EXISTS historical_temp_observations (
  city          TEXT          NOT NULL,
  observed_at   TIMESTAMPTZ   NOT NULL,
  temp_f        NUMERIC(6,2)  NOT NULL,
  temp_c        NUMERIC(6,3)  NOT NULL,
  dew_pt_f      NUMERIC(6,2),
  wind_mph      NUMERIC(6,2),
  pressure_mbar NUMERIC(8,2),
  source        TEXT          NOT NULL DEFAULT 'wunderground',
  station_icao  TEXT,
  PRIMARY KEY (city, observed_at, source)
);
CREATE INDEX IF NOT EXISTS hto_city_obs
  ON historical_temp_observations (city, observed_at);

-- Public-read for the dashboard's backtest UI
ALTER TABLE historical_bracket_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE historical_event_resolutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE historical_temp_observations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read" ON historical_bracket_prices;
DROP POLICY IF EXISTS "Allow anon read" ON historical_event_resolutions;
DROP POLICY IF EXISTS "Allow anon read" ON historical_temp_observations;
CREATE POLICY "Allow anon read" ON historical_bracket_prices         FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Allow anon read" ON historical_event_resolutions      FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Allow anon read" ON historical_temp_observations      FOR SELECT TO anon, authenticated USING (true);
