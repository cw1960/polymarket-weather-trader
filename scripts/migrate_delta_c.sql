-- ── Add delta_c to resolution_stations ────────────────────────────────────────
-- delta_c = resolution_station_tmax - metar_tmax (°C)
-- Positive means the resolution station reads HIGHER than our METAR source,
-- so we add the delta to running_max before bracket matching.
--
-- Initial values: hand-estimated from Phase 2 live observations on May 1, 2026.
-- London METAR ~22°C, Polymarket resolved to 23°C bracket → delta ≈ +1.0°C
-- Milan/Paris similar cold bias observed (0.1¢ on "exact" bracket = wrong bracket)
--
-- Update these values daily as resolution data accumulates.
-- The resolver.py script will eventually auto-update delta_c from confirmed trades.

ALTER TABLE resolution_stations
  ADD COLUMN IF NOT EXISTS delta_c decimal DEFAULT 0.0;

-- delta_samples: number of resolved days used to compute delta_c.
-- Used by the auto-updater to weight new observations appropriately:
-- fewer samples → more weight on new data; more samples → more stable average.
ALTER TABLE resolution_stations
  ADD COLUMN IF NOT EXISTS delta_samples integer DEFAULT 0;

-- Seed confirmed biases from live Phase 2 observations (May 1, 2026).
-- ONLY cities where we directly observed the bracket mismatch are seeded here.
-- All other cities default to 0.0 and will be auto-updated by the resolver
-- as resolution data accumulates (see _update_city_delta in resolver.py).
UPDATE resolution_stations SET delta_c = 1.0 WHERE city = 'London';
UPDATE resolution_stations SET delta_c = 1.0 WHERE city = 'Paris';
UPDATE resolution_stations SET delta_c = 1.0 WHERE city = 'Milan';

-- Verify
SELECT city, station_name, delta_c, delta_samples
FROM resolution_stations
WHERE delta_c != 0
ORDER BY city;
