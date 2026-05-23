export interface Identity {
  address: string
  username: string
  pseudonym: string
  bio: string
}

export interface ProfileStats {
  total_trades: number
  buy_count: number
  sell_count: number
  unique_markets: number
  closed_positions: number
  open_positions: number
  truly_open_positions?: number
  unredeemed_positions?: number
  roundtrip_rate: number
  total_volume_usd: number
  buy_volume_usd: number
  sell_volume_usd: number
  net_cashflow_usd: number
  avg_buy_size_usd: number
  median_buy_price: number
  buy_price_buckets: Record<string, number>
  avg_hold_hours: number
  median_hold_hours: number
  weather_share: number
  category_counts: Record<string, number>
}

export interface ByDayRow {
  date: string
  buys: number
  closed: number
  open: number
  wins: number
  losses: number
  spent: number
  pnl: number
  roi_pct: number
  avg_hold_hours: number
}

export interface OpenPosition {
  conditionId: string
  title: string
  slug: string
  outcome: string
  size: number
  cost_basis_usd: number
  avg_entry_price: number
  entered_at: number
  market_date?: string | null
  unredeemed_post_resolution?: boolean
}

export interface Strategy {
  label: string
  confidence: number
  reasons: string[]
}

export interface WeatherCity {
  city: string
  trades: number
  buy_volume: number
}

export interface PriceBucketRow {
  bucket: string
  n_resolved: number
  n_open: number
  wins: number
  win_rate_pct: number
  cost_usd: number
  payout_usd: number
  pnl_usd: number
  roi_pct: number
  // ── Open-position bracketing (added 2026-05-16) ──
  // Three numbers that replace the previous "open: 29" black box with
  // concrete bounds on the trader's true P&L for open positions.
  open_cost_usd?:    number
  open_mtm_pnl?:     number   // mark-to-market P&L at current best bid
  open_best_pnl?:    number   // P&L if all open positions win at $1
  open_worst_pnl?:   number   // P&L if all open positions lose
  // Combined honest estimate: resolved P&L + open mark-to-market.
  true_pnl_estimate?: number
}

export interface WeatherDissection {
  weather_trades?: number
  gfs_phase_histogram?: Record<string, number>
  cities?: WeatherCity[]
  price_bucket_pnl?: PriceBucketRow[]
  hold_hours_distribution?: { n: number; p10: number; p50: number; p90: number; max: number }
  error?: string
}

export interface AnalyzerResponse {
  run_id: number
  from_cache: boolean
  fetched_at?: string
  identity: Identity
  stats: ProfileStats
  strategy: Strategy
  by_day: ByDayRow[]
  open_positions: OpenPosition[]
  weather_dissection?: WeatherDissection
  precise_pnl?: unknown
  meta: {
    fetched_at: string
    fetch_ms: number
    trade_count: number
    weather_count: number
    raw_activity_count: number
    activity_truncated?: boolean
  }
}

export interface RunSummary {
  wallet:            string
  username:          string
  pseudonym:         string
  fetched_at:        string | null
  total_trades:      number
  unique_markets:    number
  weather_share:     number
  net_cashflow_usd:  number
  open_positions:    number
  strategy_label:    string
  trade_count:       number
  /**
   * Short personal tag (rendered on every trader card).  Stored in
   * `analyzer_annotations` keyed by wallet — works for any analyzed
   * trader, no follow required.
   */
  headline?:         string
  /**
   * Long-form personal notes (shown in the trader detail view).
   */
  notes?:            string
}

export interface WatchlistEntry extends RunSummary {
  label:        string
  added_at:     string | null
  last_polled:  string | null
}

export interface BucketStat {
  bucket:       string
  n_resolved:   number
  win_rate_pct: number
  pnl_usd:      number
  roi_pct:      number | null
  note?:        string
  // Added 2026-05-16 — populated by Claude when the price_bucket_pnl
  // rows include the new mark-to-market fields.  pnl_usd is the
  // resolved-only number; true_pnl_estimate folds in open MTM.
  open_mtm_pnl?:      number
  true_pnl_estimate?: number
}

export interface MonitorPosition {
  market_title:        string
  condition_id:        string
  trader_side:         string
  trader_entry_price:  number
  trader_cost_usd:     number
  fade_trigger:        string
  urgency:             'low' | 'medium' | 'high' | string
}

export interface SpeculativeOpenBet {
  bucket:         string
  n_open:         number
  open_mtm_pnl:   number
  open_best_pnl:  number
  open_worst_pnl: number
  note:           string
}

export interface SharedCityBreakdown {
  city:            string
  best_bucket:     string
  best_pnl_usd:    number
  worst_bucket:    string
  worst_pnl_usd:   number
  verdict:         'profitable' | 'losing' | 'mixed' | 'thin' | string
  note:            string
}

export interface AntiPrecedentRanking {
  class_label:                   string
  n_priors_analyzed:             number
  priors_aggregate_cashflow_usd: number
  this_traders_cashflow_usd:     number
  percentile_in_class:           'best' | 'top_quartile' | 'median' | 'bottom_quartile' | 'worst' | 'only_one' | string
  interpretation:                string
}

export interface Replicability {
  score:             'copyable' | 'partial' | 'not_replicable' | string
  blocking_factors:  string[]
  explanation:       string
}

export interface StructuredCommentary {
  strategy_summary: string
  /**
   * Reconciliation between the per-bucket true-P&L estimate and the
   * trader's net cashflow. If the two agree (consistent), the
   * per-bucket numbers can be trusted as a basis for decisions.
   * Added 2026-05-16 alongside the mark-to-market enrichment.
   */
  consistency_check?: {
    true_pnl_estimate_total_usd: number
    net_cashflow_usd:            number
    match_quality:               'consistent' | 'inflated' | 'deflated' | 'insufficient_data' | string
    interpretation:              string
  }
  /**
   * Recent-vs-lifetime trajectory.  See backend trajectory field for
   * the underlying data this summarises.
   */
  trajectory_summary?: string
  wins:             BucketStat[]
  losses:           BucketStat[]
  /**
   * Buckets with only open exposure (no resolved track record).  These
   * are speculations, not proven edge — kept separate from wins/losses.
   */
  speculative_open_bets?: SpeculativeOpenBet[]
  /**
   * Per-city profitability slice for shared cities only.
   */
  shared_city_breakdown?: SharedCityBreakdown[]
  /**
   * Where this trader ranks among prior traders of the same strategy class.
   */
  anti_precedent_ranking?: AntiPrecedentRanking
  /**
   * Whether their strategy works at our scale + infrastructure.
   */
  replicability?: Replicability
  /**
   * Concrete action items (3-5).  Required.
   */
  lessons_for_us?: string[]
  /**
   * Plain-English explanation of WHY the recommendation is what it is.
   */
  recommendation_explainer?: string
  overlap: {
    verdict:                      'no_overlap' | 'partial' | 'full' | string
    shared_cities:                string[]
    our_validated_resolved_count: number
    explanation:                  string
  }
  recommendation: 'disqualify' | 'ignore' | 'learn' | 'measure_first' | 'counter' | 'copy' | string
  gates: {
    a_validated_zone:        boolean
    b_supporting_stat_cited: boolean
    c_kelly_row_matches:     boolean | null
    explanation:             string
  }
  validation_plan:    string
  kelly_sizing_row:   number | null
  monitor_positions?: MonitorPosition[]
  adversarial_check:  string
}

export interface JobStatus {
  id:            string
  wallet:        string
  status:        'queued' | 'running' | 'done' | 'error' | string
  progress_pct:  number
  stage:         string
  detail:        string
  created_at:    number
  finished_at:   number | null
  error:         string | null
  result?:       AnalyzerResponse
}

export interface AnalyzeStartResponse {
  job_id:     string | null   // null when served from cache inline
  wallet?:    string
  status?:    string
  from_cache: boolean
  // When from_cache=true, this also contains the full AnalyzerResponse shape:
  [key: string]: unknown
}

export interface CommentaryResponse {
  run_id:      number
  structured:  StructuredCommentary | null
  markdown:    string
  parse_error: string | null
  model_used:  string
  mode:        string
  cost_usd:    number
  tokens?:     { input: number; output: number; cache_read: number; cache_write: number }
  error?:      string
}
