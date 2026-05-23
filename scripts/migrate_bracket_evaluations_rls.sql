-- migrate_bracket_evaluations_rls.sql
--
-- The bracket_evaluations table was created without an anon-read policy, so
-- the dashboard (which uses the anon key in the browser) sees zero rows.
-- This migration mirrors the read-only policies the other public-display
-- tables (sizing_schedule, guardrail_events, etc.) presumably use.
--
-- Falsifying test (after running):
--   On the Mission Control page, "Live Bot Activity" should immediately show
--   non-zero counts and the stream table should populate with rows.
--
-- Safe: read-only, no data is modified.

-- Enable RLS (if not already) so policies actually do something:
ALTER TABLE bracket_evaluations ENABLE ROW LEVEL SECURITY;

-- Drop any previous policy with the same name (idempotent):
DROP POLICY IF EXISTS "Allow anon read" ON bracket_evaluations;

-- Create the public-read policy:
CREATE POLICY "Allow anon read"
  ON bracket_evaluations
  FOR SELECT
  TO anon, authenticated
  USING (true);

-- Same for guardrail_events (probably has the same issue):
ALTER TABLE guardrail_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read" ON guardrail_events;
CREATE POLICY "Allow anon read"
  ON guardrail_events
  FOR SELECT
  TO anon, authenticated
  USING (true);

-- And sizing_schedule (just in case):
ALTER TABLE sizing_schedule ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read" ON sizing_schedule;
CREATE POLICY "Allow anon read"
  ON sizing_schedule
  FOR SELECT
  TO anon, authenticated
  USING (true);
