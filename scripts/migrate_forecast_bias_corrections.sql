-- forecast_bias_corrections — replaces the misused delta_matrix for
-- forecast-mean bias correction.
--
-- BACKGROUND: delta_matrix was computing resolution_station_temp -
-- comparison_station_temp (station-to-station temperature offset on
-- historical days).  That value was then incorrectly added to the
-- forecast mean in fetch_forecasts.py as if it were a forecast bias.
-- For several US cities the sign was wrong, making cold-biased
-- forecasts even colder and inverting Phase 1's calibration.
--
-- This table holds the CORRECT signal: median of
-- (actual_winning_bracket_mid - forecast_mean) per city from historical
-- resolved markets.  Applied in fetch_forecasts.py as
--   corrected_mean = forecast_mean + bias_c
--
-- See CLAUDE.md and the discussion log from 2026-05-18.
--
-- Run once in Supabase SQL editor.

CREATE TABLE IF NOT EXISTS forecast_bias_corrections (
    city          TEXT PRIMARY KEY,
    bias_c        FLOAT NOT NULL DEFAULT 0,
    raw_median_c  FLOAT,
    stdev_c       FLOAT,
    n_samples     INT,
    note          TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index on updated_at for stale-correction detection
CREATE INDEX IF NOT EXISTS forecast_bias_corrections_updated_idx
    ON forecast_bias_corrections (updated_at DESC);
