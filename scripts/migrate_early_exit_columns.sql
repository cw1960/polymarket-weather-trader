-- migrate_early_exit_columns.sql
--
-- Add columns to trade_signals for early-exit recoveries (selling a stale YES
-- position before its market resolves to $0).
--
-- Falsifying test:
--   SELECT column_name FROM information_schema.columns
--     WHERE table_name='trade_signals'
--       AND column_name IN ('sold_at','sold_price','sold_size_usd','recovered_usd');
--   -- Expect 4 rows.

ALTER TABLE trade_signals
  ADD COLUMN IF NOT EXISTS sold_at         TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS sold_price      NUMERIC(6,4),     -- price we sold the tokens at
  ADD COLUMN IF NOT EXISTS sold_size_usd   NUMERIC(10,4),    -- USD value of tokens at sale
  ADD COLUMN IF NOT EXISTS recovered_usd   NUMERIC(10,4);    -- net recovered = sold_size_usd

-- Index for finding open positions (filled, not yet sold, not yet resolved)
CREATE INDEX IF NOT EXISTS trade_signals_open_yes_positions
  ON trade_signals (forecast_date, city, side)
  WHERE order_status = 'filled'
    AND sold_at IS NULL
    AND winning_bracket IS NULL;
