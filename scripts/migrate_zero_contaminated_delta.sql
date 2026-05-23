-- migrate_zero_contaminated_delta.sql
--
-- One-shot data cleanup: zero out resolution_stations.delta_c values that
-- were learned from Polymarket-oracle-bug-affected resolutions (2026-05-17
-- weather markets that resolved to wrong MINIMUM brackets per Polymarket's
-- 2026-05-18/19 disclosure).
--
-- Assumption (write before running, per CLAUDE.md Rule 2):
--   The current delta_c values for São Paulo, Seoul, and Hong Kong are
--   contaminated. São Paulo (+7.0°C, n=2) and Seoul (-0.297°C, n=3) and
--   Hong Kong (-0.12°C, n=5) all updated via resolver._update_city_delta()
--   off resolutions that Polymarket has now disclosed were oracle errors.
--
-- Falsifying test:
--   1. Run the SELECT block below BEFORE the UPDATE — record the three rows.
--   2. Run the UPDATE.
--   3. Run the SELECT block again — confirm those three rows are now
--      (delta_c=0, delta_samples=0) and no OTHER rows changed.
--   4. Spot-check Moscow / Istanbul / Tel Aviv are untouched (these use
--      weather.gov / HKO sources, not WU, so they are NOT in the oracle
--      bug list and their nonzero deltas are legitimate station offsets).

-- BEFORE state — paste this output into the chat before running UPDATE:
SELECT city, station_id, delta_c, delta_samples
FROM resolution_stations
WHERE city IN ('São Paulo', 'Seoul', 'Hong Kong', 'Moscow', 'Istanbul', 'Tel Aviv')
ORDER BY city;

-- The cleanup:
UPDATE resolution_stations
SET delta_c = 0.0,
    delta_samples = 0
WHERE city IN ('São Paulo', 'Seoul', 'Hong Kong');

-- AFTER state — paste this output too:
SELECT city, station_id, delta_c, delta_samples
FROM resolution_stations
WHERE city IN ('São Paulo', 'Seoul', 'Hong Kong', 'Moscow', 'Istanbul', 'Tel Aviv')
ORDER BY city;
