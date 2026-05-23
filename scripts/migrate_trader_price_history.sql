-- migrate_trader_price_history.sql
--
-- New table for the manual-trading dashboard ("Trader" app). Stores a
-- time series of bracket prices, orderbook snapshots, and weather context
-- for every active Polymarket weather market. Powers:
--   • Page 1 watchlist sparklines and at-a-glance state
--   • Page 2 trade station charts with technical indicators
--   • Backtests for the technical-indicator strategy
--
-- This table is purely additive — it does not affect the existing bot's
-- behavior. Existing bot tables (trade_signals, bracket_evaluations, etc.)
-- are untouched.
--
-- Falsifying test after running:
--   SELECT count(*) FROM bracket_price_history;     -- expect 0
--   -- Then the trader_price_collector cron starts populating this every minute.

CREATE TABLE IF NOT EXISTS bracket_price_history (
  id                          BIGSERIAL PRIMARY KEY,
  recorded_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  city                        TEXT         NOT NULL,
  forecast_date               DATE         NOT NULL,
  condition_id                TEXT         NOT NULL,
  market_id                   TEXT,                                 -- alias for condition_id; some endpoints use one or the other
  bracket_label               TEXT         NOT NULL,                -- e.g. "86-87°F" or "24°C"
  bracket_unit                TEXT,                                 -- 'F' or 'C'
  bracket_low_native          NUMERIC(7,2),                         -- low bound in market's native unit
  bracket_high_native          NUMERIC(7,2),                        -- high bound in market's native unit

  yes_price                   NUMERIC(6,4),                         -- current last-trade YES price (0..1)
  no_price                    NUMERIC(6,4),                         -- 1 - yes_price typically
  best_bid                    NUMERIC(6,4),                         -- best bid for YES (orderbook snapshot if available)
  best_ask                    NUMERIC(6,4),                         -- best ask for YES
  bid_size_usd                NUMERIC(10,2),                        -- depth at best bid
  ask_size_usd                NUMERIC(10,2),                        -- depth at best ask
  spread_pct                  NUMERIC(6,4),                         -- (best_ask - best_bid) / midpoint

  observed_temp_c             NUMERIC(6,3),                         -- city's current temperature in °C
  observed_running_max_c      NUMERIC(6,3),                         -- running daily high °C (corrected source — hourly obs)
  local_hour                  SMALLINT,                             -- hour-of-day in city's timezone

  time_to_resolution_minutes  INTEGER,                              -- minutes until market resolves
  market_closed               BOOLEAN      NOT NULL DEFAULT FALSE,  -- true if event.closed=true at snapshot time

  CONSTRAINT bph_price_range CHECK (yes_price IS NULL OR (yes_price >= 0 AND yes_price <= 1))
);

CREATE INDEX IF NOT EXISTS bph_city_date_time
  ON bracket_price_history (city, forecast_date, recorded_at DESC);

CREATE INDEX IF NOT EXISTS bph_condition_time
  ON bracket_price_history (condition_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS bph_recorded_at
  ON bracket_price_history (recorded_at DESC);

-- Public-read RLS policy (anon key reads from dashboard)
ALTER TABLE bracket_price_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read" ON bracket_price_history;
CREATE POLICY "Allow anon read"
  ON bracket_price_history
  FOR SELECT
  TO anon, authenticated
  USING (true);
