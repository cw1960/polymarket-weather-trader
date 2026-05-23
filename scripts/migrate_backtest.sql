-- migrate_backtest.sql
-- Tables for backtesting the ladder strategy.
-- Run once in Supabase SQL Editor before running backtest_fetch.py.

-- ── 1. Historical GFS day-ahead forecasts ───────────────────────────────────

create table if not exists backtest_forecasts (
    city            text    not null,
    forecast_date   date    not null,
    -- Raw GFS day-ahead prediction (deterministic, °C)
    raw_forecast_c  decimal not null,
    -- Empirical std: rolling 90-day RMSE of GFS vs actual for this city/month
    -- Populated in a second pass once actuals are fetched
    empirical_std_c decimal,
    primary key (city, forecast_date)
);

-- ── 2. Historical NOAA actual high temperatures ──────────────────────────────

create table if not exists backtest_actuals (
    city        text    not null,
    date        date    not null,
    actual_c    decimal not null,   -- always °C
    station_id  text,
    primary key (city, date)
);

-- ── 3. Historical Polymarket bracket prices + resolution ─────────────────────
-- One row per bracket per market date per city.
-- yes_price: pre-resolution market price (NULL if not available from API;
--            backtest_run.py will substitute a price model in that case).
-- resolved_yes: true for the one bracket that actually won.

create table if not exists backtest_markets (
    city            text    not null,
    market_date     date    not null,
    label           text    not null,
    low             decimal not null,
    high            decimal not null,
    unit            text    not null default 'C',
    yes_price       decimal,            -- NULL means use price model
    resolved_yes    boolean not null default false,
    event_slug      text,
    condition_id    text,
    primary key (city, market_date, label)
);

-- ── 4. Backtest simulation results ───────────────────────────────────────────
-- One row per rung in each simulated ladder run.

create table if not exists backtest_results (
    id              uuid    primary key default gen_random_uuid(),
    run_tag         text    not null,   -- e.g. 'pass1_2024-01-01_2026-04-20'
    city            text    not null,
    market_date     date    not null,
    label           text    not null,
    rung_type       text    not null,   -- 'core' | 'wing'
    distance_sigma  decimal not null,
    model_prob      decimal not null,
    yes_price       decimal not null,   -- price used in simulation
    size_usd        decimal not null,
    resolved_yes    boolean not null,
    pnl             decimal not null,   -- positive if won, negative if lost
    price_source    text    not null,   -- 'polymarket' | 'model'
    created_at      timestamptz not null default now()
);

create index if not exists idx_backtest_results_run_tag
    on backtest_results (run_tag);

create index if not exists idx_backtest_results_city_date
    on backtest_results (city, market_date);

-- ── 5. RLS (read-only for anon) ──────────────────────────────────────────────

alter table backtest_forecasts enable row level security;
alter table backtest_actuals    enable row level security;
alter table backtest_markets    enable row level security;
alter table backtest_results    enable row level security;

drop policy if exists "anon_read_backtest_forecasts" on backtest_forecasts;
create policy "anon_read_backtest_forecasts" on backtest_forecasts for select to anon using (true);

drop policy if exists "anon_read_backtest_actuals" on backtest_actuals;
create policy "anon_read_backtest_actuals" on backtest_actuals for select to anon using (true);

drop policy if exists "anon_read_backtest_markets" on backtest_markets;
create policy "anon_read_backtest_markets" on backtest_markets for select to anon using (true);

drop policy if exists "anon_read_backtest_results" on backtest_results;
create policy "anon_read_backtest_results" on backtest_results for select to anon using (true);
