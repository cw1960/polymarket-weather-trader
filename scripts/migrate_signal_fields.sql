-- ============================================================
-- Add display-quality fields to trade_signals
-- Run in Supabase SQL Editor after migrate_dashboard.sql
-- ============================================================

alter table trade_signals
  add column if not exists forecast_date  date,
  add column if not exists market_question text,
  add column if not exists event_slug     text,
  add column if not exists mean_high      decimal,
  add column if not exists std_high       decimal;
