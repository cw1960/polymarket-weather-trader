import { useState, useEffect, useCallback } from 'react'
import supabase from '../lib/supabase'

// ── Types ───────────────────────────────────────────────────────────────────

export interface GuardrailStatus {
  name: string                // 'phase2_paused' | 'bankroll_floor' | 'daily_loss' | '3day_win_rate'
  label: string               // human label
  blocked: boolean            // true if this guardrail would block a trade right now
  reason: string              // human-readable why
  metric?: string             // optional numeric summary, e.g. "$60.21 / $1500"
}

export interface FlagState {
  key: string
  label: string
  value: string | null
  description: string
}

export interface SizingRow {
  week_label: string
  start_date: string
  end_date: string
  phase2_yes_size_usd: number
  phase2_no_sweep_size_usd: number
  phase2_no_sweep_max_per_city: number
  deployment_cap_pct: number
  kelly_fraction: number
  notes: string | null
}

export interface DecisionRow {
  id: string
  signal_time: string
  city: string
  side: 'YES' | 'NO'
  outcome: string
  signal_phase: string | null
  recommended_position: number | null
  filled_size_usd: number | null
  market_price: number | null
  model_probability: number | null
  edge: number | null
  order_status: string | null
  pnl_usd: number | null
  inferredAction: 'real-money' | 'observation' | 'failed' | 'pending'
}

export interface GuardrailEvent {
  id: number
  fired_at: string
  guardrail: string
  details_json: Record<string, unknown> | null
}

export interface PaperTestingStats {
  baselineIso: string                            // start of testing window
  totalSignals: number                           // signals fired since baseline (all phases)
  phase1Signals: number
  phase2YesSignals: number
  phase2SweepSignals: number
  resolvedSignals: number                        // resolved subset
  resolvedWins: number                           // wins among resolved
  winRate: number | null                         // wins / resolved, or null if 0
  calibrationBins: { bucket: number; n: number; wins: number; rate: number }[]
  citiesCovered: number                          // unique cities with at least one signal since baseline
}

export interface LiveActivity {
  lastActivityIso: string | null         // most recent bracket_evaluations row
  secondsSinceActivity: number | null    // computed at fetch time
  lastHourEvals: number                  // bracket evaluations in last 60 min
  lastHourGatePass: number               // of those, how many passed gate
  lastHourSelected: number               // top-N selected for trades (paper or real)
  todayEvals: number                     // since UTC midnight
  todayGatePass: number
  todaySelected: number
  recentStream: LiveActivityRow[]        // last 25 evaluations (gate-pass first, then most recent)
}

export interface LiveActivityRow {
  id: number
  evaluated_at: string
  city: string
  cycle: string                          // 'phase2_sweep' | 'phase1' | etc.
  bracket_label: string
  model_prob_no: number | null
  no_price: number | null
  edge_no: number | null
  gate_passed: boolean
  side_selected: string | null           // 'YES' | 'NO' | null
  ranked_position: number | null
  size_usd: number | null
  guardrail_block: string | null
}

export interface ResultRow {
  id: string
  resolved_at: string
  signal_time: string
  city: string
  side: 'YES' | 'NO'
  signal_phase: string | null            // 'phase1' | 'phase2' | 'phase2_sweep'
  outcome: string                        // bracket label
  market_price: number                   // price of the side traded
  model_probability: number | null
  won: boolean                           // true if our side won
  simulated_pnl: number                  // P&L at week-1 sizing ($5 NO sweep, $3 YES, $0.10 phase1)
}

export interface EvBucket {
  bucket: number | string                // e.g. 0.3, 0.5 for prob; "20¢", "60¢" for price; "NYC" for city
  n: number
  wins: number
  totalSize: number
  totalPnl: number
  evPerDollar: number                    // totalPnl / totalSize
  winRate: number                        // wins / n
}

export interface LiveResults {
  totalResolved: number
  totalWins: number
  winRate: number | null
  simulatedCumulativePnl: number
  simulatedCumulativePnlNoOnly: number
  bestTradePnl: number
  worstTradePnl: number
  recentStream: ResultRow[]
  // NEW (2026-05-21 per senior-dev review): EV-based metrics
  realizedEvPerDollar: number | null            // aggregate: totalPnl / totalSize across all resolved
  realizedEvPerDollarNoOnly: number | null      // NO-side only — the strategy's actual signal
  realizedEvPerDollarLast50: number | null      // rolling 50-trade window (NO side) — used by EV guardrail
  evByProbBucket: EvBucket[]                    // for NO-side trades, bucketed by model_prob_no
  evByPriceBucket: EvBucket[]                   // for NO-side trades, bucketed by no_price
  evByCity: EvBucket[]                          // for NO-side trades, by city
}

export interface MissionControlData {
  guardrails: GuardrailStatus[]
  flags: FlagState[]
  sizing: SizingRow | null
  recentDecisions: DecisionRow[]
  recentGuardrailEvents: GuardrailEvent[]
  bankroll: number | null
  bankrollFloor: number | null
  todayPnl: number
  dailyLossLimit: number | null
  threeDayWinRate: { wins: number; n: number; rate: number } | null
  minThreeDayWinRate: number | null
  min3DayResolved: number | null
  paperTesting: PaperTestingStats | null
  liveActivity: LiveActivity | null              // real-time bracket-evaluations feed
  liveResults: LiveResults | null                // resolved paper trades + simulated P&L
  effectiveTradingMode: 'PAPER' | 'LIVE'         // derived from guardrails, not the static settings flag
  loading: boolean
  lastRefreshed: Date | null
}

// ── Hook ────────────────────────────────────────────────────────────────────

const FLAG_KEYS = [
  { key: 'phase2_paused',             label: 'Phase 2 Paused (master kill switch)',
    description: 'When 1, ALL real-money Phase 2 trades become $0.01 observations.' },
  { key: 'phase2_yes_locks_enabled',  label: 'YES Locks Enabled',
    description: 'When 0, the YES-lock side is suppressed (observation only). Week 1 should keep this 0.' },
  { key: 'bankroll_reconcile_paused', label: 'Bankroll Reconcile Paused',
    description: 'When 1, the nightly bankroll reconcile no-ops. Set during Polymarket-archive refund window.' },
]

const CONFIG_KEYS_NEEDED = [
  'phase2_paused',
  'phase2_yes_locks_enabled',
  'bankroll_reconcile_paused',
  'bankroll_usd',
  'min_bankroll_usd_trading',
  'max_daily_loss_pct',
  'min_3day_win_rate',
  'min_3day_resolved_trades',
  'today_loss_paused_date',
  'auto_pause_reason',
  'phase2_min_edge',
  'phase2_min_model_prob_gate',
  'testing_baseline_iso',
]

function fmtMoney(n: number | null): string {
  if (n == null || isNaN(n)) return '—'
  return `$${n.toFixed(2)}`
}

function todayUtcIso(): string {
  const d = new Date()
  return d.toISOString().slice(0, 10)
}

function nowUtcMinus36h(): string {
  const d = new Date(Date.now() - 36 * 3600 * 1000)
  return d.toISOString()
}

function nowUtcMinus3d(): string {
  const d = new Date(Date.now() - 3 * 24 * 3600 * 1000)
  return d.toISOString()
}

export function useMissionControl(): MissionControlData & { refresh: () => void } {
  const [data, setData] = useState<MissionControlData>({
    guardrails: [],
    flags: [],
    sizing: null,
    recentDecisions: [],
    recentGuardrailEvents: [],
    bankroll: null,
    bankrollFloor: null,
    todayPnl: 0,
    dailyLossLimit: null,
    threeDayWinRate: null,
    minThreeDayWinRate: null,
    min3DayResolved: null,
    paperTesting: null,
    liveActivity: null,
    liveResults: null,
    effectiveTradingMode: 'PAPER',
    loading: true,
    lastRefreshed: null,
  })

  const fetch = useCallback(async () => {
    setData((p) => ({ ...p, loading: true }))
    try {
      const today = todayUtcIso()
      const hourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString()
      const todayStart = new Date(); todayStart.setUTCHours(0, 0, 0, 0)
      const todayStartIso = todayStart.toISOString()

      const [cfgRes, sizingRes, decisionsRes, eventsRes, pnlRes, recentRes,
             evalHourRes, evalTodayRes, evalStreamRes, evalLastRes] = await Promise.all([
        supabase.from('system_config').select('key,value').in('key', CONFIG_KEYS_NEEDED),
        supabase
          .from('sizing_schedule')
          .select('*')
          .lte('start_date', today)
          .gte('end_date',   today)
          .order('start_date', { ascending: false })
          .limit(1),
        supabase
          .from('trade_signals')
          .select('id,signal_time,city,side,outcome,signal_phase,recommended_position,filled_size_usd,market_price,model_probability,edge,order_status,pnl_usd')
          .in('signal_phase', ['phase2', 'phase2_sweep'])
          .order('signal_time', { ascending: false })
          .limit(15),
        supabase
          .from('guardrail_events')
          .select('id,fired_at,guardrail,details_json')
          .order('fired_at', { ascending: false })
          .limit(10),
        // Today's resolved P&L (last 36h window matches guardrails.py)
        supabase
          .from('trade_signals')
          .select('pnl_usd,signal_phase,order_status,resolved_at')
          .gte('resolved_at', nowUtcMinus36h())
          .in('signal_phase', ['phase2', 'phase2_sweep'])
          .eq('order_status', 'filled')
          .not('pnl_usd', 'is', null),
        // 3-day resolved sample for win-rate display
        supabase
          .from('trade_signals')
          .select('pnl_usd,resolved_at')
          .gte('resolved_at', nowUtcMinus3d())
          .in('signal_phase', ['phase2', 'phase2_sweep'])
          .eq('order_status', 'filled')
          .not('pnl_usd', 'is', null),
        // Live activity — counts in last hour
        supabase
          .from('bracket_evaluations')
          .select('id,gate_passed,side_selected', { count: 'exact' })
          .gte('evaluated_at', hourAgo)
          .limit(10000),
        // Today counts (since UTC midnight)
        supabase
          .from('bracket_evaluations')
          .select('id,gate_passed,side_selected', { count: 'exact' })
          .gte('evaluated_at', todayStartIso)
          .limit(20000),
        // Live stream — most recent 50 rows
        supabase
          .from('bracket_evaluations')
          .select('id,evaluated_at,city,cycle,bracket_label,model_prob_no,no_price,edge_no,gate_passed,side_selected,ranked_position,size_usd,guardrail_block')
          .order('evaluated_at', { ascending: false })
          .limit(50),
        // Last activity timestamp (for heartbeat)
        supabase
          .from('bracket_evaluations')
          .select('evaluated_at')
          .order('evaluated_at', { ascending: false })
          .limit(1),
      ])

      // ── Config dict ───────────────────────────────────────────────
      const cfg: Record<string, string | null> = {}
      for (const r of cfgRes.data ?? []) cfg[r.key as string] = (r as { value: string | null }).value
      const getF = (k: string, d: number) => {
        const v = cfg[k]
        const n = v != null && v !== '' ? Number(v) : NaN
        return Number.isFinite(n) ? n : d
      }
      const bankroll      = cfg.bankroll_usd != null ? Number(cfg.bankroll_usd) : null
      const bankrollFloor = getF('min_bankroll_usd_trading', 1500)
      const lossPct       = getF('max_daily_loss_pct', 0.08)
      const minWinRate    = getF('min_3day_win_rate', 0.45)
      const minResolved   = getF('min_3day_resolved_trades', 15)
      const dailyLossLimit = bankroll != null ? -lossPct * bankroll : null

      const todayPnl = (pnlRes.data ?? []).reduce(
        (s, r) => s + Number((r as { pnl_usd: number | null }).pnl_usd ?? 0),
        0,
      )

      const recentRows = (recentRes.data ?? []) as { pnl_usd: number | null }[]
      const n3 = recentRows.length
      const w3 = recentRows.filter((r) => Number(r.pnl_usd ?? 0) > 0).length
      const threeDayWinRate = n3 > 0 ? { wins: w3, n: n3, rate: w3 / n3 } : null

      // ── Guardrails state ──────────────────────────────────────────
      const guardrails: GuardrailStatus[] = []
      // G1 — phase2_paused
      const paused = cfg.phase2_paused === '1'
      guardrails.push({
        name: 'phase2_paused',
        label: 'Master Kill Switch',
        blocked: paused,
        reason: paused ? 'phase2_paused = 1' : 'OK',
        metric: cfg.phase2_paused ?? '(absent)',
      })
      // G2 — bankroll floor
      const bankrollBlocked = bankroll != null && bankroll < bankrollFloor
      guardrails.push({
        name: 'bankroll_floor',
        label: 'Bankroll Floor',
        blocked: bankrollBlocked,
        reason: bankrollBlocked
          ? `${fmtMoney(bankroll)} < floor ${fmtMoney(bankrollFloor)}`
          : 'OK',
        metric: `${fmtMoney(bankroll)} / ${fmtMoney(bankrollFloor)}`,
      })
      // G3 — daily loss
      const lossBlocked =
        cfg.today_loss_paused_date === today
        || (dailyLossLimit != null && todayPnl <= dailyLossLimit)
      guardrails.push({
        name: 'daily_loss',
        label: 'Daily Loss Limit',
        blocked: !!lossBlocked,
        reason: lossBlocked
          ? (cfg.today_loss_paused_date === today
              ? 'limit fired earlier today'
              : `today P&L ${fmtMoney(todayPnl)} ≤ limit ${fmtMoney(dailyLossLimit)}`)
          : 'OK',
        metric: dailyLossLimit != null
          ? `${fmtMoney(todayPnl)} / ${fmtMoney(dailyLossLimit)}`
          : '—',
      })
      // G4 — 3-day win rate
      const winBlocked =
        threeDayWinRate != null
        && threeDayWinRate.n >= minResolved
        && threeDayWinRate.rate < minWinRate
      guardrails.push({
        name: '3day_win_rate',
        label: '3-Day Win Rate',
        blocked: !!winBlocked,
        reason: !threeDayWinRate
          ? 'no resolved Phase 2 trades in window'
          : threeDayWinRate.n < minResolved
            ? `only ${threeDayWinRate.n} resolved (need ${minResolved}+) — not enforced yet`
            : winBlocked
              ? `${(threeDayWinRate.rate * 100).toFixed(1)}% < floor ${(minWinRate * 100).toFixed(0)}%`
              : 'OK',
        metric: threeDayWinRate
          ? `${threeDayWinRate.wins}/${threeDayWinRate.n} = ${(threeDayWinRate.rate * 100).toFixed(0)}%`
          : '—',
      })

      // ── Flags ─────────────────────────────────────────────────────
      const flags: FlagState[] = FLAG_KEYS.map((f) => ({
        key:         f.key,
        label:       f.label,
        description: f.description,
        value:       cfg[f.key] ?? null,
      }))

      // ── Sizing ────────────────────────────────────────────────────
      const sizingRow = (sizingRes.data?.[0] ?? null) as SizingRow | null

      // ── Decisions ─────────────────────────────────────────────────
      const decisions: DecisionRow[] = ((decisionsRes.data ?? []) as DecisionRow[])
        .map((r) => {
          const size = Number(r.filled_size_usd ?? r.recommended_position ?? 0)
          let inferredAction: DecisionRow['inferredAction'] = 'observation'
          if (r.order_status === 'failed') inferredAction = 'failed'
          else if (r.order_status === 'pending') inferredAction = 'pending'
          else if (size > 1) inferredAction = 'real-money'
          return { ...r, inferredAction }
        })

      const events = ((eventsRes.data ?? []) as GuardrailEvent[])

      // ── Paper-testing window: signals since baseline ─────────────────
      const baselineIso = cfg.testing_baseline_iso || '2026-05-20T00:00:00+00:00'
      const paperRes = await supabase
        .from('trade_signals')
        .select('id,signal_time,resolved_at,city,side,outcome,model_probability,market_price,actual_outcome,winning_bracket,signal_phase,recommended_position')
        .gte('signal_time', baselineIso)
        .limit(10000)
      const paperRows = (paperRes.data ?? []) as Array<{
        id: string
        signal_time: string
        resolved_at: string | null
        city: string
        side: 'YES' | 'NO'
        signal_phase: string | null
        outcome: string
        market_price: number | null
        actual_outcome: string | boolean | null
        winning_bracket: string | null
        model_probability: number | null
      }>
      const totalSignals = paperRows.length
      const phase1Signals       = paperRows.filter((r) => r.signal_phase === 'phase1').length
      const phase2YesSignals    = paperRows.filter((r) => r.signal_phase === 'phase2').length
      const phase2SweepSignals  = paperRows.filter((r) => r.signal_phase === 'phase2_sweep').length

      // Resolved rows: have winning_bracket AND actual_outcome.
      const resolvedRows = paperRows.filter(
        (r) => r.winning_bracket != null && r.actual_outcome != null,
      )
      const resolvedSignals = resolvedRows.length
      // For YES: actual_outcome 'true' means win. For NO: 'false' means win.
      const wonRows = resolvedRows.filter((r) => {
        const a = String(r.actual_outcome)
        return r.side === 'YES' ? a === 'true' : a === 'false'
      })
      const resolvedWins = wonRows.length
      const winRate      = resolvedSignals > 0 ? resolvedWins / resolvedSignals : null

      // Calibration table — bin by model_prob for the traded side
      const bins = new Map<number, { n: number; wins: number }>()
      for (const r of resolvedRows) {
        let p = Number(r.model_probability ?? 0)
        if (r.side === 'NO') p = 1 - p
        const bucket = Math.round(p * 10) / 10
        const cur = bins.get(bucket) ?? { n: 0, wins: 0 }
        cur.n += 1
        const a = String(r.actual_outcome)
        const won = r.side === 'YES' ? a === 'true' : a === 'false'
        if (won) cur.wins += 1
        bins.set(bucket, cur)
      }
      const calibrationBins = [...bins.entries()]
        .map(([bucket, v]) => ({ bucket, n: v.n, wins: v.wins, rate: v.n ? v.wins / v.n : 0 }))
        .sort((a, b) => a.bucket - b.bucket)

      const citiesCovered = new Set(paperRows.map((r) => r.city)).size

      // ── Live activity (bracket_evaluations real-time feed) ────────────
      const hourRows  = (evalHourRes.data  ?? []) as Array<{ gate_passed?: boolean; side_selected?: string | null }>
      const todayRows = (evalTodayRes.data ?? []) as Array<{ gate_passed?: boolean; side_selected?: string | null }>
      const streamRaw = (evalStreamRes.data ?? []) as LiveActivityRow[]
      const lastIsoArr = (evalLastRes.data ?? []) as Array<{ evaluated_at: string }>
      const lastActivityIso = lastIsoArr[0]?.evaluated_at ?? null
      const secondsSinceActivity = lastActivityIso
        ? Math.max(0, Math.floor((Date.now() - new Date(lastActivityIso).getTime()) / 1000))
        : null
      // Reorder stream: gate-pass rows first, then most-recent-first within each group
      const passers   = streamRaw.filter((r) => r.gate_passed)
      const nonPassers= streamRaw.filter((r) => !r.gate_passed)
      const recentStream = [...passers.slice(0, 10), ...nonPassers.slice(0, 15)]
      const liveActivity: LiveActivity = {
        lastActivityIso,
        secondsSinceActivity,
        lastHourEvals:   hourRows.length,
        lastHourGatePass: hourRows.filter((r) => r.gate_passed).length,
        lastHourSelected: hourRows.filter((r) => r.side_selected).length,
        todayEvals:   todayRows.length,
        todayGatePass: todayRows.filter((r) => r.gate_passed).length,
        todaySelected: todayRows.filter((r) => r.side_selected).length,
        recentStream,
      }

      // ── Live results (simulated P&L on resolved paper trades) ────────
      // Use the sizing_schedule row for the strategy's intended sizes.
      // V1: hardcoded week-1 sizes ($5 NO sweep, $3 YES lock, $0 for Phase 1 paper).
      // Future: pull from sizingRow.
      const SIM_SIZE_NO    = 5.0
      const SIM_SIZE_YES   = 3.0
      const SIM_SIZE_PHASE1 = 0.0   // Phase 1 stays paper-only forever
      const simSize = (row: { signal_phase: string | null; side: string }) => {
        if (row.signal_phase === 'phase2_sweep') return SIM_SIZE_NO
        if (row.signal_phase === 'phase2' && row.side === 'YES') return SIM_SIZE_YES
        return SIM_SIZE_PHASE1
      }
      const resultRows: ResultRow[] = resolvedRows.map((r) => {
        const a = String(r.actual_outcome)
        const won = r.side === 'YES' ? a === 'true' : a === 'false'
        const sz = simSize(r)
        const p  = Number(r.market_price ?? 0)
        let pnl  = 0
        if (sz > 0 && p > 0 && p < 1) {
          pnl = won ? sz * (1 - p) / p : -sz
        }
        return {
          id: r.id,
          resolved_at: r.resolved_at ?? r.signal_time,
          signal_time: r.signal_time,
          city: r.city,
          side: r.side,
          signal_phase: r.signal_phase,
          outcome: r.outcome,
          market_price: p,
          model_probability: r.model_probability,
          won,
          simulated_pnl: pnl,
        }
      })
      // Most-recent-first for display, take last 30 for the stream
      resultRows.sort((a, b) => (b.resolved_at || '').localeCompare(a.resolved_at || ''))
      const recentStreamR = resultRows.slice(0, 30)
      const noOnly = resultRows.filter((r) => r.signal_phase === 'phase2_sweep')
      const cumPnl   = resultRows.reduce((s, r) => s + r.simulated_pnl, 0)
      const cumPnlNo = noOnly.reduce((s, r) => s + r.simulated_pnl, 0)
      const sortedPnl = resultRows.map((r) => r.simulated_pnl).sort((a, b) => a - b)
      const worst = sortedPnl[0] ?? 0
      const best  = sortedPnl[sortedPnl.length - 1] ?? 0
      const winsResolved = resultRows.filter((r) => r.won).length

      // ── Realized EV per dollar risked (new headline metric) ──────────
      // Per the 2026-05-21 senior-dev review, EV-per-dollar is what really
      // drives profitability — not win rate. A 70% win rate at 90¢ entries
      // is mediocre; a 55% win rate at 30¢ entries is gold. Aggregate as
      // sum(pnl) / sum(size) = realized return on deployed capital.
      const sizedRows   = resultRows.filter((r) => simSize(r) > 0)
      const sizedNoRows = sizedRows.filter((r) => r.signal_phase === 'phase2_sweep')
      const totalSize   = sizedRows.reduce((s, r) => s + simSize(r), 0)
      const totalSizeNo = sizedNoRows.reduce((s, r) => s + simSize(r), 0)
      const totalPnlAll = sizedRows.reduce((s, r) => s + r.simulated_pnl, 0)
      const totalPnlNo  = sizedNoRows.reduce((s, r) => s + r.simulated_pnl, 0)
      const realizedEvPerDollar       = totalSize   > 0 ? totalPnlAll / totalSize   : null
      const realizedEvPerDollarNoOnly = totalSizeNo > 0 ? totalPnlNo  / totalSizeNo : null

      // Rolling last-50 NO-side window — input to the EV guardrail.
      const last50No = sizedNoRows
        .slice()
        .sort((a, b) => (b.resolved_at || '').localeCompare(a.resolved_at || ''))
        .slice(0, 50)
      const last50Size = last50No.reduce((s, r) => s + simSize(r), 0)
      const last50Pnl  = last50No.reduce((s, r) => s + r.simulated_pnl, 0)
      const realizedEvPerDollarLast50 = last50Size > 0 ? last50Pnl / last50Size : null

      // ── EV by bucket (NO-side only — that's the actual strategy) ─────
      function bucketize<T>(rows: ResultRow[], keyFn: (r: ResultRow) => T): Map<T, EvBucket> {
        const m = new Map<T, EvBucket>()
        for (const r of rows) {
          const k = keyFn(r)
          const cur = m.get(k) ?? { bucket: k as never, n: 0, wins: 0, totalSize: 0, totalPnl: 0, evPerDollar: 0, winRate: 0 }
          cur.n += 1
          if (r.won) cur.wins += 1
          cur.totalSize += simSize(r)
          cur.totalPnl  += r.simulated_pnl
          m.set(k, cur)
        }
        for (const v of m.values()) {
          v.evPerDollar = v.totalSize > 0 ? v.totalPnl / v.totalSize : 0
          v.winRate     = v.n > 0 ? v.wins / v.n : 0
        }
        return m
      }
      // Bucket prob_no in 0.1 steps; market_price (no_price) in 0.1 steps
      const probMap  = bucketize(sizedNoRows, (r) => Math.round((1 - (r.model_probability ?? 0)) * 10) / 10)
      const priceMap = bucketize(sizedNoRows, (r) => Math.round(r.market_price * 10) / 10)
      const cityMap  = bucketize(sizedNoRows, (r) => r.city)
      const evByProbBucket  = [...probMap.values()].sort((a, b) => (a.bucket as number) - (b.bucket as number))
      const evByPriceBucket = [...priceMap.values()].sort((a, b) => (a.bucket as number) - (b.bucket as number))
      const evByCity        = [...cityMap.values()].sort((a, b) => b.evPerDollar - a.evPerDollar)

      const liveResults: LiveResults = {
        totalResolved: resultRows.length,
        totalWins: winsResolved,
        winRate: resultRows.length ? winsResolved / resultRows.length : null,
        simulatedCumulativePnl:       cumPnl,
        simulatedCumulativePnlNoOnly: cumPnlNo,
        bestTradePnl:  best,
        worstTradePnl: worst,
        recentStream:  recentStreamR,
        realizedEvPerDollar,
        realizedEvPerDollarNoOnly,
        realizedEvPerDollarLast50,
        evByProbBucket,
        evByPriceBucket,
        evByCity,
      }

      // ── Effective trading mode (derived from guardrails) ─────────────
      // PAPER if any guardrail is blocking or if all real-money flags are off.
      // This is the authoritative source — header badge should NOT rely on the
      // manual settings.trading_mode flag, which can drift.
      const anyBlocked = guardrails.some((g) => g.blocked)
      const yesEnabled = cfg.phase2_yes_locks_enabled === '1'
      const effectiveTradingMode: 'PAPER' | 'LIVE' =
        anyBlocked || !yesEnabled ? 'PAPER' : 'LIVE'

      setData({
        guardrails,
        flags,
        sizing:                sizingRow,
        recentDecisions:       decisions,
        recentGuardrailEvents: events,
        bankroll,
        bankrollFloor,
        todayPnl,
        dailyLossLimit,
        threeDayWinRate,
        minThreeDayWinRate:    minWinRate,
        min3DayResolved:       minResolved,
        paperTesting: {
          baselineIso,
          totalSignals,
          phase1Signals,
          phase2YesSignals,
          phase2SweepSignals,
          resolvedSignals,
          resolvedWins,
          winRate,
          calibrationBins,
          citiesCovered,
        },
        liveActivity,
        liveResults,
        effectiveTradingMode,
        loading: false,
        lastRefreshed: new Date(),
      })
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('useMissionControl: fetch failed', e)
      setData((p) => ({ ...p, loading: false }))
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, 10_000)   // poll every 10s for a live-feel
    return () => clearInterval(id)
  }, [fetch])

  return { ...data, refresh: fetch }
}
