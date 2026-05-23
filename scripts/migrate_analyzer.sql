-- Polymarket Trader Analyzer — caching layer
-- Run once against the existing Supabase project.

CREATE TABLE IF NOT EXISTS analyzer_runs (
  id           BIGSERIAL PRIMARY KEY,
  wallet       TEXT NOT NULL,
  username     TEXT,
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  stats_json   JSONB NOT NULL,
  -- audit columns: lets us see how long fetches take and whether truncated
  fetch_ms     INTEGER,
  trade_count  INTEGER,
  truncated    BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS analyzer_runs_wallet_time_idx
  ON analyzer_runs (wallet, fetched_at DESC);

CREATE TABLE IF NOT EXISTS analyzer_commentary (
  id           BIGSERIAL PRIMARY KEY,
  run_id       BIGINT NOT NULL REFERENCES analyzer_runs(id) ON DELETE CASCADE,
  model        TEXT NOT NULL,
  mode         TEXT NOT NULL,                  -- 'standard' | 'deep'
  markdown     TEXT NOT NULL,
  prompt_hash  TEXT,                            -- to dedupe identical prompts
  cost_usd     NUMERIC(10, 4),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS analyzer_commentary_run_idx
  ON analyzer_commentary (run_id, created_at DESC);

-- Watchlist (Phase 3 — table created now, populated later)
CREATE TABLE IF NOT EXISTS analyzer_watchlist (
  wallet       TEXT PRIMARY KEY,
  label        TEXT,                            -- human note, e.g. "weather specialist"
  added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_polled  TIMESTAMPTZ,
  active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- Persistent cache of Polymarket market metadata. Once we've fetched a
-- conditionId's resolution status, we never need to fetch it again — saves
-- 1-2 minutes on whale-wallet re-analyses where most markets are settled.
CREATE TABLE IF NOT EXISTS analyzer_market_cache (
  condition_id    TEXT PRIMARY KEY,
  slug            TEXT,
  closed          BOOLEAN NOT NULL,
  outcome_prices  TEXT,
  end_date        TIMESTAMPTZ,
  fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS analyzer_market_cache_closed_idx
  ON analyzer_market_cache (closed);
