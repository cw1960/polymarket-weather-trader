-- Trader Analyzer: personal annotations on ANY wallet
-- ────────────────────────────────────────────────────────────────
-- Stored separately from analyzer_watchlist so a user can attach
-- a headline + notes to any analyzed trader without first having
-- to follow them.  Replaces the earlier headline/notes columns
-- on analyzer_watchlist (those will be ignored going forward; you
-- can drop them later with the optional commands below).
CREATE TABLE IF NOT EXISTS analyzer_annotations (
  wallet      TEXT PRIMARY KEY,
  headline    TEXT,
  notes       TEXT,
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE analyzer_annotations IS
  'Personal user notes on any analyzed trader, keyed by wallet. '
  'Independent of analyzer_watchlist — does not require following.';

-- Optional cleanup after migration (run when convenient):
--   ALTER TABLE analyzer_watchlist DROP COLUMN IF EXISTS headline;
--   ALTER TABLE analyzer_watchlist DROP COLUMN IF EXISTS notes;
