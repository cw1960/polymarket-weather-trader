-- Migration: execution telemetry columns on trade_signals
-- Purpose: capture fill quality, slippage, and execution latency for live orders
-- Date: 2026-05-12

ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS intended_price  NUMERIC;
ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS bid_at_signal   NUMERIC;
ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS ask_at_signal   NUMERIC;
ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS mid_at_signal   NUMERIC;
ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS fill_time       TIMESTAMPTZ;
ALTER TABLE trade_signals ADD COLUMN IF NOT EXISTS fill_latency_ms INTEGER;

COMMENT ON COLUMN trade_signals.intended_price  IS 'YES price the executor decided to bid at order placement time';
COMMENT ON COLUMN trade_signals.bid_at_signal   IS 'Best bid in the order book at order placement time';
COMMENT ON COLUMN trade_signals.ask_at_signal   IS 'Best ask in the order book at order placement time';
COMMENT ON COLUMN trade_signals.mid_at_signal   IS '(bid+ask)/2 at order placement time. Compare to fill_price for slippage analysis.';
COMMENT ON COLUMN trade_signals.fill_time       IS 'Timestamp the order transitioned to filled state';
COMMENT ON COLUMN trade_signals.fill_latency_ms IS 'Milliseconds between order placement and fill confirmation';
