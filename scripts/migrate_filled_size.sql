-- ── filled_size_usd: actual dollars deployed at fill time ──────────────────
-- Why: when a partial fill happens (e.g. Cape Town: 5 tokens at 30¢ on a
-- $15 order, only $1.50 actually filled), the resolver was computing P&L
-- as if the full $15 was deployed.  filled_size_usd stores the real
-- post-fill cost basis so resolver math matches what actually happened.
ALTER TABLE trade_signals
  ADD COLUMN IF NOT EXISTS filled_size_usd NUMERIC;

COMMENT ON COLUMN trade_signals.filled_size_usd IS
  'Actual USD deployed at fill: size_matched_tokens × avg_fill_price. '
  'Set by executor.check_and_update_orders when a fill is detected. '
  'If null, resolver falls back to recommended_position (paper rows).';
