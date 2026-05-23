-- Migration: add miss_distance_c column to trade_signals
-- Purpose: track precision of bracket selection for each resolved trade.
--          0 = bet on correct bracket; 1 = off by one bracket; etc.
-- Date:    2026-05-10

ALTER TABLE trade_signals
  ADD COLUMN IF NOT EXISTS miss_distance_c NUMERIC;

COMMENT ON COLUMN trade_signals.miss_distance_c IS
  'Distance in C between bet bracket midpoint and resolution bracket midpoint. NULL for tail brackets.';
