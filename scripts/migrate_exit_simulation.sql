-- Migration: exit_simulation table for tracking hypothetical post-lock exits.
-- Records bust events (undershoots) and late-day decays (overshoots) so we can
-- evaluate Strategy E (sell + switch with fresh capital) in shadow mode.
-- Date: 2026-05-12

CREATE TABLE IF NOT EXISTS exit_simulation (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id       UUID NOT NULL,                       -- the original phase2 signal
  city            TEXT NOT NULL,
  forecast_date   DATE NOT NULL,
  detection_type  TEXT NOT NULL,                        -- 'bust' | 'late_decay'
  detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Original trade snapshot
  bet_bracket     TEXT NOT NULL,
  bet_lock_price  NUMERIC,                              -- price at original lock
  bet_stake       NUMERIC DEFAULT 45,
  bet_running_max NUMERIC,                              -- running_max at detection

  -- Bust-specific: what we'd switch to
  new_bracket     TEXT,
  busted_yes_price NUMERIC,                             -- YES price on bet bracket at detection
  new_yes_price    NUMERIC,                             -- YES price on next bracket up

  -- Resolution outcomes (NULL until resolved)
  actual_winning_bracket TEXT,
  bet_won         BOOLEAN,
  new_won         BOOLEAN,

  -- Hypothetical strategies P&L (computed at resolution)
  hold_pnl                  NUMERIC,
  sell_only_pnl             NUMERIC,
  switch_fresh_pnl          NUMERIC,
  sell_switch_proceeds_pnl  NUMERIC,
  sell_switch_fresh_pnl     NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_exit_sim_signal ON exit_simulation(signal_id);
CREATE INDEX IF NOT EXISTS idx_exit_sim_date   ON exit_simulation(forecast_date);
CREATE INDEX IF NOT EXISTS idx_exit_sim_unresolved
  ON exit_simulation(forecast_date) WHERE actual_winning_bracket IS NULL;

COMMENT ON TABLE exit_simulation IS
  'Shadow simulation of post-lock exit strategies. No real trades executed.';
