-- Migration: add recent_temps_json column to temp_readings
-- Purpose: track last N raw readings to detect rising-temperature trends
--          and prevent "premature lock" losses.
-- Date:    2026-05-10

ALTER TABLE temp_readings
  ADD COLUMN IF NOT EXISTS recent_temps_json TEXT;

ALTER TABLE temp_readings
  ADD COLUMN IF NOT EXISTS sky_condition TEXT;

COMMENT ON COLUMN temp_readings.recent_temps_json IS
  'JSON array of last 12 raw temp_c readings (most recent last). Used to compute dT/dt.';

COMMENT ON COLUMN temp_readings.sky_condition IS
  'METAR sky cover code: SKC, FEW, SCT, BKN, OVC. NULL if unavailable.';
