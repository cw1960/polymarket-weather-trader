-- Migration: create run_reports table for 4x-daily health snapshots
-- Run once in Supabase SQL editor.

CREATE TABLE IF NOT EXISTS run_reports (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_time              timestamptz NOT NULL DEFAULT now(),
  run_slot              text,                         -- '03:30' | '09:30' | '15:30' | '21:30'
  health_score          text NOT NULL DEFAULT 'yellow', -- 'green' | 'yellow' | 'red'
  summary               text NOT NULL DEFAULT '',

  -- Execution metrics (this run window)
  signals_generated     int  DEFAULT 0,
  orders_placed         int  DEFAULT 0,
  orders_filled         int  DEFAULT 0,
  orders_queued         int  DEFAULT 0,
  orders_failed         int  DEFAULT 0,
  cities_no_signals     jsonb DEFAULT '[]',           -- [city, ...]
  phase1_signals        int  DEFAULT 0,
  phase2_signals        int  DEFAULT 0,
  phase2_fires          jsonb DEFAULT '[]',           -- [{city, confidence, bracket}]

  -- Delta calibration
  avg_delta_c           float,
  cities_uncalibrated   jsonb DEFAULT '[]',           -- cities still at delta_c = 0

  -- 7-day rolling performance
  win_rate_7d           float,
  roi_7d                float,
  win_rate_phase1_7d    float,
  win_rate_phase2_7d    float,
  resolved_count_7d     int  DEFAULT 0,

  -- 30-day go-live criteria
  total_predictions_30d int  DEFAULT 0,
  brier_score_30d       float,
  worst_city_brier      float,
  worst_city_name       text,
  win_rate_30d          float,
  criteria_met          int  DEFAULT 0,               -- 0-4
  projected_go_live     date,

  -- Per-city detail (JSON array)
  -- [{city, delta_c, delta_samples, win_rate_7d, roi_7d, signals_7d, pnl_7d, status, flag_review}]
  city_metrics          jsonb DEFAULT '[]',

  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_reports_run_time ON run_reports(run_time DESC);

-- Enable RLS (read-only for anon key used by dashboard)
ALTER TABLE run_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anon read run_reports"
  ON run_reports FOR SELECT
  TO anon
  USING (true);

CREATE POLICY "service write run_reports"
  ON run_reports FOR INSERT
  TO service_role
  WITH CHECK (true);
