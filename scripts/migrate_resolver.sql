-- Resolver migration: add outcome / P&L columns to trade_signals and ladders.
-- Run once in the Supabase SQL editor before deploying resolver.py.

ALTER TABLE trade_signals
  ADD COLUMN IF NOT EXISTS outcome      TEXT,         -- 'YES', 'NO', 'VOID'
  ADD COLUMN IF NOT EXISTS pnl_usd      FLOAT,        -- realised P&L in $
  ADD COLUMN IF NOT EXISTS resolved_at  TIMESTAMPTZ;  -- when outcome was fetched

ALTER TABLE ladders
  ADD COLUMN IF NOT EXISTS total_pnl_usd  FLOAT,
  ADD COLUMN IF NOT EXISTS winning_rungs  INT  DEFAULT 0,
  ADD COLUMN IF NOT EXISTS losing_rungs   INT  DEFAULT 0;

COMMENT ON COLUMN trade_signals.outcome     IS 'YES = bet won, NO = bet lost, VOID = market voided';
COMMENT ON COLUMN trade_signals.pnl_usd     IS 'Realised P&L: (1/price-1)*size if YES, -size if NO';
COMMENT ON COLUMN trade_signals.resolved_at IS 'UTC timestamp when Polymarket confirmed resolution';
COMMENT ON COLUMN ladders.total_pnl_usd     IS 'Sum of pnl_usd across all rungs in this ladder';
COMMENT ON COLUMN ladders.winning_rungs     IS 'Count of rungs that resolved YES';
COMMENT ON COLUMN ladders.losing_rungs      IS 'Count of rungs that resolved NO';
