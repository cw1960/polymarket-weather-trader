-- ── Execution layer columns for trade_signals ────────────────────────────────
-- Adds order tracking so executor.py can record CLOB order IDs and fill status.
--
-- order_id      — Polymarket CLOB order ID (null until order is placed)
-- order_status  — lifecycle: 'paper' | 'pending' | 'filled' | 'failed' | 'cancelled' | 'skipped_too_small'
-- fill_price    — actual fill price (set when order is filled or on paper simulation)
--
-- Run once in Supabase SQL editor:
--   Copy-paste this file and click Run.

ALTER TABLE trade_signals
  ADD COLUMN IF NOT EXISTS order_id     text    DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS order_status text    DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS fill_price   decimal DEFAULT NULL;

-- Index for fast fill-check queries (check_and_update_orders polls this column)
CREATE INDEX IF NOT EXISTS idx_trade_signals_order_status
  ON trade_signals (order_status)
  WHERE order_status = 'pending';

-- Verify
SELECT
  column_name,
  data_type,
  column_default
FROM information_schema.columns
WHERE table_name = 'trade_signals'
  AND column_name IN ('order_id', 'order_status', 'fill_price')
ORDER BY column_name;
