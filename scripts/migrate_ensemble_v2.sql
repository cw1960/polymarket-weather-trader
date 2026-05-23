-- Migration: add ECMWF ensemble + multi-model consensus columns
-- Run this in the Supabase SQL editor before deploying fetch_forecasts.py v2

ALTER TABLE ensemble_forecasts
  ADD COLUMN IF NOT EXISTS ecmwf_members      JSONB,
  ADD COLUMN IF NOT EXISTS consensus_spread_c  FLOAT,
  ADD COLUMN IF NOT EXISTS model_means         JSONB;

COMMENT ON COLUMN ensemble_forecasts.ecmwf_members      IS 'ECMWF IFS025 51-member daily high temps (°C)';
COMMENT ON COLUMN ensemble_forecasts.consensus_spread_c  IS 'Max spread across 6 deterministic models (°C); high = uncertain';
COMMENT ON COLUMN ensemble_forecasts.model_means         IS 'JSONB map of model_name → forecast high (°C) for consensus check';
