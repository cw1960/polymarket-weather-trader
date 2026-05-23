-- Bracket Blacklist — prevents YES trades on brackets that high-conviction
-- external traders have heavily shorted via NO buys at ≥$0.95.
--
-- Background: the trader analyzer has confirmed Weatherstappen
-- (0xb9012e0d9b60d3920286309328b935cdfa609fc4) wins 99.7% of >=0.95 NO bets
-- across 937 resolved trades.  Their NO position at $0.99 is the market
-- saying "this bracket has 1% probability."  We should never put real money
-- on YES against that signal.
--
-- This table is rebuilt hourly by scripts/sync_bracket_blacklist.py.
-- The phase2_engine consults it before placing any real-money YES order.
CREATE TABLE IF NOT EXISTS bracket_blacklist (
  -- Primary key: the market's conditionId.  Matches our trade_signals.condition_id.
  condition_id        TEXT PRIMARY KEY,

  -- Human-readable identification of what's blacklisted
  market_question     TEXT,
  city                TEXT,
  bracket_label       TEXT,
  market_date         DATE,

  -- Which tracker wallet caused the entry (so we can attribute / audit)
  source_wallet       TEXT NOT NULL,
  source_label        TEXT,        -- e.g. "Weatherstappen"

  -- Their position details — what they paid for being on the NO side
  source_side         TEXT NOT NULL CHECK (source_side IN ('NO', 'YES')),
  source_price        NUMERIC NOT NULL,
  source_size_tokens  NUMERIC,
  source_cost_usd     NUMERIC,

  -- Bookkeeping
  blacklisted_at      TIMESTAMPTZ DEFAULT NOW(),
  last_confirmed_at   TIMESTAMPTZ DEFAULT NOW(),
  reason              TEXT          -- human-readable explanation
);

CREATE INDEX IF NOT EXISTS bracket_blacklist_market_date_idx
  ON bracket_blacklist (market_date);

CREATE INDEX IF NOT EXISTS bracket_blacklist_city_idx
  ON bracket_blacklist (city);

COMMENT ON TABLE bracket_blacklist IS
  'Brackets where high-conviction external traders have shorted NO at ≥$0.95. '
  'Phase 2 engine refuses to place real-money YES orders on these conditionIds.';

-- One audit row per phase-2 block, so we can later quantify the strategy savings
CREATE TABLE IF NOT EXISTS bracket_blacklist_blocks (
  id                  BIGSERIAL PRIMARY KEY,
  blocked_at          TIMESTAMPTZ DEFAULT NOW(),
  condition_id        TEXT NOT NULL,
  city                TEXT,
  outcome             TEXT,
  intended_size_usd   NUMERIC,
  intended_price      NUMERIC,
  source_wallet       TEXT,
  source_price        NUMERIC,
  signal_id           UUID
);

COMMENT ON TABLE bracket_blacklist_blocks IS
  'Audit log: every Phase 2 trade that was downgraded because the conditionId '
  'was on the blacklist.  Used to measure the strategy improvement over time.';
