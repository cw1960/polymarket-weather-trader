-- ============================================================
-- Weather Trader — full schema reset
-- Safe to re-run: drops everything and recreates from scratch.
-- Run this entire block in Supabase SQL Editor → New Query.
-- ============================================================

-- Drop in dependency order (trades references trade_signals)
drop table if exists trades cascade;
drop table if exists trade_signals cascade;
drop table if exists ensemble_forecasts cascade;
drop table if exists delta_matrix cascade;
drop table if exists bankroll_snapshots cascade;
drop table if exists settings cascade;
drop table if exists resolution_stations cascade;

-- Resolution stations (which station Polymarket uses per city)
create table resolution_stations (
  id uuid primary key default gen_random_uuid(),
  city text not null,
  station_id text not null,
  station_name text not null,
  source text not null,
  lat decimal not null,
  lon decimal not null,
  unit text not null default 'C',   -- 'C' = Celsius, 'F' = Fahrenheit
  polymarket_slug text,
  created_at timestamptz default now()
);

-- Historical temperature delta between resolution station and comparison stations
create table delta_matrix (
  id uuid primary key default gen_random_uuid(),
  city text not null,
  resolution_station text not null,
  comparison_station text not null,
  month integer not null check (month between 1 and 12),
  delta_mean decimal not null,
  delta_std decimal not null,
  sample_count integer not null,
  created_at timestamptz default now()
);

-- GFS ensemble forecasts (stored in °C, unit conversion happens at signal time)
create table ensemble_forecasts (
  id uuid primary key default gen_random_uuid(),
  city text not null,
  forecast_date date not null,
  model_run timestamptz not null,
  model text not null,
  mean_high decimal,
  std_high decimal,
  min_high decimal,
  max_high decimal,
  member_count integer,
  raw_members jsonb,
  created_at timestamptz default now(),
  unique (city, forecast_date, model_run)
);

-- Every edge detected (traded or not) — source of truth for Brier scoring
create table trade_signals (
  id uuid primary key default gen_random_uuid(),
  city text not null,
  market_id text not null,
  outcome text not null,
  side text not null check (side in ('YES', 'NO')),
  market_price decimal not null,
  model_probability decimal not null,
  corrected_probability decimal not null,
  edge decimal,
  delta_mean decimal,
  delta_std decimal,
  confidence decimal,
  signal_time timestamptz default now(),
  traded boolean default false,
  trade_id uuid,
  actual_outcome boolean,
  brier_score decimal
);

-- Actual positions taken (paper and live)
create table trades (
  id uuid primary key default gen_random_uuid(),
  signal_id uuid references trade_signals(id),
  city text not null,
  market_id text not null,
  outcome text not null,
  side text not null,
  entry_price decimal not null,
  position_size decimal not null,
  shares decimal not null,
  kelly_fraction decimal,
  bankroll_at_trade decimal,
  status text default 'open' check (status in ('open', 'resolved', 'sold')),
  exit_price decimal,
  pnl decimal,
  created_at timestamptz default now(),
  resolved_at timestamptz,
  is_paper boolean default true
);

-- Daily bankroll snapshots for P&L curve
create table bankroll_snapshots (
  id uuid primary key default gen_random_uuid(),
  snapshot_date date not null,
  total_value decimal not null,
  active_positions decimal,
  cash decimal,
  daily_pnl decimal,
  is_paper boolean default true,
  created_at timestamptz default now()
);

-- Key/value config store (trading_mode, kill_switch)
create table settings (
  key text primary key,
  value text
);

insert into settings (key, value) values
  ('trading_mode', 'paper'),
  ('kill_switch', 'true');
