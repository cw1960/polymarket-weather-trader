-- migrate_ladder.sql
-- Adds ladder tracking columns to trade_signals and creates the ladders table.
-- Run once against your Supabase project (SQL Editor or psql).
-- Safe to re-run: uses IF NOT EXISTS / DO blocks throughout.

-- ── 1. Extend trade_signals ─────────────────────────────────────────────────

alter table trade_signals
    add column if not exists ladder_id       uuid,
    add column if not exists rung_type       text,        -- 'core' | 'wing'
    add column if not exists distance_sigma  decimal;     -- σ distance from forecast mean

-- Index for fast per-ladder lookups
create index if not exists idx_trade_signals_ladder_id
    on trade_signals (ladder_id)
    where ladder_id is not null;

-- ── 2. Create ladders table ─────────────────────────────────────────────────

create table if not exists ladders (
    id              uuid primary key default gen_random_uuid(),
    city            text        not null,
    forecast_date   date        not null,
    event_slug      text,
    mean_high       decimal,    -- corrected forecast mean, °C (always)
    std_high        decimal,    -- corrected forecast std,  °C (always)
    unit            text        not null default 'C',  -- display unit: 'C' | 'F'
    num_rungs       int         not null default 0,
    num_core        int         not null default 0,
    num_wings       int         not null default 0,
    total_cost_usd  decimal     not null default 0,
    is_paper        boolean     not null default true,
    status          text        not null default 'open',  -- 'open' | 'resolved' | 'cancelled'
    created_at      timestamptz not null default now()
);

-- Composite index for the most common query pattern (dashboard per-city/date)
create index if not exists idx_ladders_city_date
    on ladders (city, forecast_date desc);

-- ── 3. RLS for ladders ──────────────────────────────────────────────────────

alter table ladders enable row level security;

drop policy if exists "anon_read_ladders" on ladders;
create policy "anon_read_ladders"
    on ladders for select
    to anon
    using (true);

-- ── 4. Real-time for ladders ────────────────────────────────────────────────

-- Adds ladders to the supabase_realtime publication so the dashboard
-- receives INSERT/UPDATE events without polling.
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and tablename = 'ladders'
    ) then
        alter publication supabase_realtime add table ladders;
    end if;
end;
$$;
