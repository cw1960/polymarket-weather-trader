-- ============================================================
-- Dashboard wiring migration
-- Run in Supabase SQL Editor AFTER schema.sql has been applied.
-- Safe to re-run.
-- ============================================================

-- 1. Add missing columns to trade_signals
alter table trade_signals
  add column if not exists condition_id text,
  add column if not exists recommended_position decimal;

-- 2. Enable real-time for the tables the dashboard subscribes to
alter publication supabase_realtime add table trade_signals;
alter publication supabase_realtime add table bankroll_snapshots;

-- 3. Enable RLS
alter table trade_signals       enable row level security;
alter table trades              enable row level security;
alter table bankroll_snapshots  enable row level security;
alter table settings            enable row level security;
alter table ensemble_forecasts  enable row level security;
alter table delta_matrix        enable row level security;
alter table resolution_stations enable row level security;

-- 4. RLS read policies for anon key (drop first so re-runs are safe)
drop policy if exists "anon read trade_signals"      on trade_signals;
drop policy if exists "anon read trades"             on trades;
drop policy if exists "anon read bankroll_snapshots" on bankroll_snapshots;
drop policy if exists "anon read settings"           on settings;
drop policy if exists "anon read ensemble_forecasts" on ensemble_forecasts;
drop policy if exists "anon read resolution_stations" on resolution_stations;

create policy "anon read trade_signals"
  on trade_signals for select using (true);

create policy "anon read trades"
  on trades for select using (true);

create policy "anon read bankroll_snapshots"
  on bankroll_snapshots for select using (true);

create policy "anon read settings"
  on settings for select using (true);

create policy "anon read ensemble_forecasts"
  on ensemble_forecasts for select using (true);

create policy "anon read resolution_stations"
  on resolution_stations for select using (true);

-- 5. Allow scripts (service role bypasses RLS, but explicit policies for completeness)
drop policy if exists "service write trade_signals"      on trade_signals;
drop policy if exists "service write bankroll_snapshots" on bankroll_snapshots;
drop policy if exists "service write settings"           on settings;

create policy "service write trade_signals"
  on trade_signals for all using (true);

create policy "service write bankroll_snapshots"
  on bankroll_snapshots for all using (true);

create policy "service write settings"
  on settings for all using (true);

-- 6. Allow anon key to update settings (kill_switch / trading_mode from dashboard)
drop policy if exists "anon update settings" on settings;
create policy "anon update settings"
  on settings for update using (true);
