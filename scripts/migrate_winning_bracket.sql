-- Add a text column to store the winning bracket's question for every resolved signal.
-- This lets us compare our forecast bracket vs. what actually won.
--
-- Run once in Supabase SQL editor.

ALTER TABLE trade_signals
  ADD COLUMN IF NOT EXISTS winning_bracket text;

-- Also fix actual_outcome which was incorrectly typed as boolean.
-- We repurpose it as text to store whether the market resolved YES/NO for our specific bracket.
ALTER TABLE trade_signals
  ALTER COLUMN actual_outcome TYPE text USING NULL;
