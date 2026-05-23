-- Trader Analyzer: add personal annotations to watchlist entries
-- ───────────────────────────────────────────────────────────────
-- headline  — short tag (~60 chars) shown directly on the trader card
--             so you can scan a list and remember what each one is for
-- notes     — long-form personal notes shown only in the detail view
--             (no length limit; markdown-ish text)
--
-- Both fields are independent of `label` (which the system uses for
-- short watch-list display names) so renaming a wallet doesn't wipe
-- your headline or notes.
ALTER TABLE analyzer_watchlist
  ADD COLUMN IF NOT EXISTS headline TEXT,
  ADD COLUMN IF NOT EXISTS notes    TEXT;

COMMENT ON COLUMN analyzer_watchlist.headline IS
  'Short personal tag shown directly on the trader card in the analyzer UI.';
COMMENT ON COLUMN analyzer_watchlist.notes IS
  'Long-form personal notes for this trader; shown in the detail view.';
