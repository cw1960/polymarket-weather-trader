export interface TradeSignal {
  id: string
  city: string
  market_id: string
  condition_id: string | null
  outcome: string
  side: 'YES' | 'NO'
  market_price: number
  model_probability: number
  corrected_probability: number
  edge: number
  delta_mean: number
  delta_std: number
  confidence: number
  recommended_position: number | null
  forecast_date: string | null
  market_question: string | null
  event_slug: string | null
  mean_high: number | null
  std_high: number | null
  signal_time: string
  traded: boolean
  trade_id: string | null
  actual_outcome: boolean | null
  brier_score: number | null
  pnl_usd: number | null
  resolved_at: string | null
  signal_phase: string | null   // 'phase1' | 'phase2' | null
  rung_type: string | null      // 'core' | 'wing' | 'no' | 'phase2' | null
  order_status: string | null   // 'paper' | 'pending' | 'filled' | 'failed' | null
  winning_bracket: string | null
}

export interface Trade {
  id: string
  signal_id: string
  city: string
  market_id: string
  outcome: string
  side: string
  entry_price: number
  position_size: number
  shares: number
  kelly_fraction: number
  bankroll_at_trade: number
  status: 'open' | 'resolved' | 'sold'
  exit_price: number | null
  pnl: number | null
  created_at: string
  resolved_at: string | null
  is_paper: boolean
  // Extended from trade_signals — populated by signalToTrade
  forecast_date:    string | null
  signal_phase:     string | null   // 'phase1' | 'phase2'
  rung_type:        string | null   // 'core' | 'wing' | 'no' | 'phase2'
  confidence:       number | null
  edge_val:         number | null
  model_prob:       number | null
  market_question:  string | null
  event_slug:       string | null
  order_status:     string | null
  winning_bracket:  string | null
  condition_id:     string | null
}

export interface BankrollSnapshot {
  id: string
  snapshot_date: string
  total_value: number
  active_positions: number
  cash: number
  daily_pnl: number
  is_paper: boolean
  created_at: string
}

export interface EnsembleForecast {
  id: string
  city: string
  forecast_date: string
  model_run: string
  model: string
  mean_high: number
  std_high: number
  min_high: number
  max_high: number
  member_count: number
  raw_members: number[]
  created_at: string
}

export interface Ladder {
  id: string
  city: string
  forecast_date: string
  event_slug: string | null
  mean_high: number | null
  std_high: number | null
  unit: string
  num_rungs: number
  num_core: number
  num_wings: number
  total_cost_usd: number
  total_pnl_usd: number | null
  winning_rungs: number | null
  losing_rungs: number | null
  is_paper: boolean
  status: string
  created_at: string
}

export interface CityMetric {
  city: string
  delta_c: number
  delta_samples: number
  win_rate_7d: number | null
  roi_7d: number | null
  signals_7d: number
  pnl_7d: number
  status: 'green' | 'yellow' | 'red' | 'gray'
  flag_review: boolean
}

export interface Phase2Fire {
  city: string
  confidence: number
  bracket: string
}

export interface RunReport {
  id: string
  run_time: string
  run_slot: string | null           // '03:30' | '09:30' | '15:30' | '21:30'
  health_score: 'green' | 'yellow' | 'red'
  summary: string

  // Execution
  signals_generated: number
  orders_placed: number
  orders_filled: number
  orders_queued: number
  orders_failed: number
  cities_no_signals: string[]
  phase1_signals: number
  phase2_signals: number
  phase2_fires: Phase2Fire[]

  // Calibration
  avg_delta_c: number | null
  cities_uncalibrated: string[]

  // 7-day rolling
  win_rate_7d: number | null
  roi_7d: number | null
  win_rate_phase1_7d: number | null
  win_rate_phase2_7d: number | null
  resolved_count_7d: number

  // 30-day go-live
  total_predictions_30d: number
  brier_score_30d: number | null
  worst_city_brier: number | null
  worst_city_name: string | null
  win_rate_30d: number | null
  criteria_met: number
  projected_go_live: string | null  // ISO date

  city_metrics: CityMetric[]
  created_at: string
}

export interface ResolutionStation {
  id: string
  city: string
  station_id: string
  station_name: string
  source: string
  lat: number
  lon: number
  polymarket_slug: string
  created_at: string
}
