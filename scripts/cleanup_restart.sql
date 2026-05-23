-- ── FULL CLEAN RESTART ────────────────────────────────────────────────────────
-- Wipes ALL ladder + signal data from May 1, 2, and 3.
-- These were generated under the buggy engine (DB writes before scaling)
-- and the old $150 budget cap. Starting clean from May 4 onwards.

-- 1. Delete all trade_signals linked to ladders on May 1, 2, 3
DELETE FROM trade_signals
WHERE forecast_date IN ('2026-05-01', '2026-05-02', '2026-05-03');

-- 2. Delete all ladders on May 1, 2, 3
DELETE FROM ladders
WHERE forecast_date IN ('2026-05-01', '2026-05-02', '2026-05-03');

-- 3. Delete any Phase 2 signals from those dates (they reference forecast_date too)
-- (already covered above if signal_phase = 'phase2', but belt-and-suspenders)
DELETE FROM trade_signals
WHERE created_at < '2026-05-04T00:00:00+00:00';

-- 4. Clear temp_readings from those days (stale monitor data)
DELETE FROM temp_readings
WHERE reading_date IN ('2026-05-01', '2026-05-02', '2026-05-03');

-- 5. Verify what's left
SELECT
    forecast_date,
    COUNT(DISTINCT l.id) AS ladders,
    COUNT(ts.id)         AS signals,
    ROUND(SUM(ts.recommended_position)::numeric, 2) AS total_usd
FROM ladders l
LEFT JOIN trade_signals ts ON ts.ladder_id = l.id
GROUP BY forecast_date
ORDER BY forecast_date DESC;
