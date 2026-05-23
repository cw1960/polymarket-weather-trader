import { useState, useEffect, useCallback } from 'react'
import supabase from '../lib/supabase'
import { Trade } from '../types'

const STARTING_BANKROLL = 1000

export interface DailyPoint {
  date: string          // 'Apr 28'
  value: number         // cumulative bankroll
  dailyPnl: number      // that day's net P&L
}

export interface CityStats {
  city: string
  pnl: number
  wins: number
  losses: number
  trades: number
  winRate: number
  deployed: number
}

// ── New-model normalization ───────────────────────────────────────────────────
// Post-backtest strategy (May 8 2026):
//   - Calibrated cities (delta_samples ≥ 3) + price < 30¢ → $45/trade
//   - Everything else → $0.01 observation (keeps delta calibration alive)
// DB values are now pre-adjusted to match this strategy, so normalization
// is a passthrough. Kept for backward compatibility with the toggle.
function normalizeTrade(t: Trade): Trade {
  return t
}

function signalToTrade(s: Record<string, unknown>): Trade {
  const pnl_usd  = s.pnl_usd as number | null
  const resolved = pnl_usd !== null && pnl_usd !== undefined
  // Prefer the ACTUAL executed values when present so partial fills don't
  // misrepresent risk. The Houston 2026-05-17 incident: order placed at
  // $15 intent, only $3.06 actually filled; recommended_position stayed
  // at $15 so the dashboard overstated exposure 5×. fill_price /
  // filled_size_usd are written by the executor on fill detection (or by
  // the reconciler for manual buys), so they're the truthful basis.
  const filledSize  = s.filled_size_usd as number | null | undefined
  const fillPrice   = s.fill_price       as number | null | undefined
  const price       = (fillPrice != null ? fillPrice : (s.market_price as number)) ?? 0
  const size        = (filledSize != null ? filledSize : (s.recommended_position as number)) ?? 0
  return {
    id:                s.id as string,
    signal_id:         s.id as string,
    city:              s.city as string,
    market_id:         (s.market_id as string) ?? '',
    outcome:           s.outcome as string,
    side:              (s.side as string) ?? 'YES',
    entry_price:       price,
    position_size:     size,
    shares:            price > 0 ? size / price : 0,
    kelly_fraction:    0,
    bankroll_at_trade: 0,
    status:            resolved ? 'resolved' : 'open',
    exit_price:        resolved ? (pnl_usd! > 0 ? 1.0 : 0.0) : null,
    pnl:               pnl_usd ?? null,
    created_at:        (s.signal_time as string) ?? (s.created_at as string),
    resolved_at:       (s.resolved_at as string) ?? null,
    is_paper:          true,
    // Extended signal fields
    forecast_date:     (s.forecast_date as string) ?? null,
    signal_phase:      (s.signal_phase as string) ?? null,
    rung_type:         (s.rung_type as string) ?? null,
    confidence:        (s.confidence as number) ?? null,
    edge_val:          (s.edge as number) ?? null,
    model_prob:        (s.model_probability as number) ?? null,
    market_question:   (s.market_question as string) ?? null,
    event_slug:        (s.event_slug as string) ?? null,
    order_status:      (s.order_status as string) ?? null,
    winning_bracket:   (s.winning_bracket as string) ?? null,
    condition_id:      (s.condition_id as string) ?? null,
  }
}

function formatLabel(isoDate: string): string {
  const dt = new Date(isoDate + 'T12:00:00Z')
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

export function useTrades() {
  const [openTrades,      setOpenTrades]      = useState<Trade[]>([])
  const [tradeHistory,    setTradeHistory]    = useState<Trade[]>([])
  const [totalPnl,        setTotalPnl]        = useState(0)
  const [todayPnl,        setTodayPnl]        = useState(0)
  const [winRate,         setWinRate]         = useState(0)
  const [dailySeries,     setDailySeries]     = useState<DailyPoint[]>([])
  const [cityStats,       setCityStats]       = useState<CityStats[]>([])
  const [loading,         setLoading]         = useState(true)
  const [lastRefreshed,   setLastRefreshed]   = useState<Date | null>(null)

  // Normalized (flat $1 Phase 1) variants
  const [normTradeHistory, setNormTradeHistory] = useState<Trade[]>([])
  const [normTotalPnl,     setNormTotalPnl]     = useState(0)
  const [normTodayPnl,     setNormTodayPnl]     = useState(0)
  const [normWinRate,      setNormWinRate]      = useState(0)
  const [normDailySeries,  setNormDailySeries]  = useState<DailyPoint[]>([])

  const fetch = useCallback(async () => {
    try {
      // Use local calendar date so "today" matches the user's clock, not UTC.
      // toLocaleDateString('en-CA') gives YYYY-MM-DD in local time.
      const today = new Date().toLocaleDateString('en-CA')

      // Open positions: look back 7 days so signals from yesterday aren't invisible
      // during the ~3h window between midnight UTC and the next resolver run (03:30 UTC).
      // Resolved signals are excluded via pnl_usd IS NULL regardless of date.
      const sevenDaysAgo = new Date()
      sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7)

      // History window: 365 days so full-year metrics never silently drop off.
      const yearAgo = new Date()
      yearAgo.setDate(yearAgo.getDate() - 365)

      const [openRes, historyRes, liveConfigRes, liveBankrollRes] = await Promise.all([
        // Open: unresolved signals from last 7 days (catches overnight limbo)
        supabase
          .from('trade_signals')
          .select('*')
          .gte('forecast_date', sevenDaysAgo.toISOString().slice(0, 10))
          .is('pnl_usd', null),
        // History: last 365 days, resolved (pnl_usd not null), oldest first for chart
        supabase
          .from('trade_signals')
          .select('*')
          .gte('signal_time', yearAgo.toISOString())
          .not('pnl_usd', 'is', null)
          .order('resolved_at', { ascending: false })
          .limit(2000),
        // Live mode marker: if set, filter metrics to live trades only
        supabase
          .from('system_config')
          .select('value')
          .eq('key', 'live_start_date')
          .maybeSingle(),
        // Reference bankroll for the P&L chart's cumulative series (live mode)
        supabase
          .from('system_config')
          .select('value')
          .eq('key', 'live_starting_bankroll')
          .maybeSingle(),
      ])

      const liveStartDate         = liveConfigRes.data?.value ?? null
      const liveStartingBankroll  = liveBankrollRes.data?.value != null
        ? parseFloat(liveBankrollRes.data.value)
        : STARTING_BANKROLL
      const openRaw    = openRes.data    ?? []
      const historyRaw = historyRes.data ?? []

      // In live mode, restrict metrics to live trades since live_start_date.
      // Resolved history: only FILLED trades count (failed orders had no P&L).
      // Open positions: PENDING + FILLED both count (real-money positions awaiting resolution).
      // Paper/observation trades continue feeding calibration but don't show in dashboard P&L.
      const filterResolved = (rows: Record<string, unknown>[]) => {
        if (!liveStartDate) return rows
        return rows.filter((r) => {
          const fd = r.forecast_date as string | null
          const status = r.order_status as string | null
          if (!fd || fd < liveStartDate) return false
          // 'sold' = manually closed via Polymarket UI (record_manual_sale.py).
          // 'filled' = filled and either still open or resolved by the resolver.
          return status === 'filled' || status === 'sold'
        })
      }
      const filterOpen = (rows: Record<string, unknown>[]) => {
        if (!liveStartDate) return rows
        return rows.filter((r) => {
          const fd = r.forecast_date as string | null
          const status = r.order_status as string | null
          if (!fd || fd < liveStartDate) return false
          return status === 'pending' || status === 'filled'
        })
      }

      const open    = filterOpen(openRaw).map(signalToTrade)
      const history = filterResolved(historyRaw).map(signalToTrade)

      setOpenTrades(open)
      setTradeHistory(history)

      // Aggregate P&L by resolved date — use local date so the chart and
      // "today" bucket both reflect the user's calendar, not UTC.
      const byDate: Record<string, number> = {}
      for (const t of history) {
        const d = new Date(t.resolved_at ?? t.created_at).toLocaleDateString('en-CA')
        byDate[d] = (byDate[d] ?? 0) + (t.pnl ?? 0)
      }

      // Build cumulative daily series.
      // Anchor at live_starting_bankroll so the chart represents the actual
      // bankroll trajectory in live mode, not a paper-era $1,000 phantom.
      const sortedDates = Object.keys(byDate).sort()
      let cumulative = liveStartingBankroll
      const series: DailyPoint[] = sortedDates.map((d) => {
        cumulative += byDate[d]
        return { date: formatLabel(d), value: Math.round(cumulative * 100) / 100, dailyPnl: Math.round(byDate[d] * 100) / 100 }
      })
      setDailySeries(series)

      const total = history.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
      const wins  = history.filter((t) => (t.pnl ?? 0) > 0).length
      setTotalPnl(Math.round(total * 100) / 100)
      setTodayPnl(Math.round((byDate[today] ?? 0) * 100) / 100)
      setWinRate(history.length > 0 ? (wins / history.length) * 100 : 0)

      // ── Normalized (flat $1 Phase 1) metrics ──────────────────────────────
      const normHistory = history.map(normalizeTrade)

      const normByDate: Record<string, number> = {}
      for (const t of normHistory) {
        const d = new Date(t.resolved_at ?? t.created_at).toLocaleDateString('en-CA')
        normByDate[d] = (normByDate[d] ?? 0) + (t.pnl ?? 0)
      }
      let normCumulative = liveStartingBankroll
      const normSeries: DailyPoint[] = Object.keys(normByDate).sort().map((d) => {
        normCumulative += normByDate[d]
        return { date: formatLabel(d), value: Math.round(normCumulative * 100) / 100, dailyPnl: Math.round(normByDate[d] * 100) / 100 }
      })
      const normTotal = normHistory.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
      const normWins  = normHistory.filter((t) => (t.pnl ?? 0) > 0).length
      setNormTradeHistory(normHistory)
      setNormTotalPnl(Math.round(normTotal * 100) / 100)
      setNormTodayPnl(Math.round((normByDate[today] ?? 0) * 100) / 100)
      setNormWinRate(normHistory.length > 0 ? (normWins / normHistory.length) * 100 : 0)
      setNormDailySeries(normSeries)

      // Per-city breakdown
      const cityMap: Record<string, { pnl: number; wins: number; losses: number; deployed: number }> = {}
      for (const t of history) {
        const c = t.city
        if (!cityMap[c]) cityMap[c] = { pnl: 0, wins: 0, losses: 0, deployed: 0 }
        cityMap[c].pnl      += t.pnl ?? 0
        cityMap[c].deployed += t.position_size
        if ((t.pnl ?? 0) > 0) cityMap[c].wins   += 1
        else                   cityMap[c].losses += 1
      }
      const stats: CityStats[] = Object.entries(cityMap)
        .map(([city, s]) => ({
          city,
          pnl:      Math.round(s.pnl      * 100) / 100,
          wins:     s.wins,
          losses:   s.losses,
          trades:   s.wins + s.losses,
          winRate:  s.wins + s.losses > 0 ? (s.wins / (s.wins + s.losses)) * 100 : 0,
          deployed: Math.round(s.deployed * 100) / 100,
        }))
        .sort((a, b) => b.pnl - a.pnl)  // best performers first
      setCityStats(stats)
      setLastRefreshed(new Date())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const interval = setInterval(fetch, 60_000)
    return () => clearInterval(interval)
  }, [fetch])

  return {
    openTrades, tradeHistory, totalPnl, todayPnl, winRate, dailySeries, cityStats, loading, lastRefreshed,
    normTradeHistory, normTotalPnl, normTodayPnl, normWinRate, normDailySeries,
  }
}
