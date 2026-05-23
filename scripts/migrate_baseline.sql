-- Set the performance baseline date.
-- All trades resolved on or after this date count as "official" results.
-- Trades before this date exist for reference only (pre-fix / duplicate-contaminated runs).
--
-- Run this ONCE before your first clean signal_engine run.
-- Change the date below if you want a different baseline.

INSERT INTO settings (key, value)
VALUES ('baseline_date', '2026-04-30')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
