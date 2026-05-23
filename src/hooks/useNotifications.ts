import { useState, useEffect, useCallback } from 'react'
import supabase from '../lib/supabase'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface NotificationTrade {
  city:         string
  outcome:      string
  pnl:          number   // actual P&L
  normPnl:      number   // normalized to $150 budget / $20 cap
  confidence:   number
  marketPrice:  number
  positionSize: number
  won:          boolean
}

export interface NotificationBatch {
  batchKey:   string              // YYYY-MM-DDTHH:MM — used as stable key
  resolvedAt: string              // ISO timestamp of first trade in batch
  trades:     NotificationTrade[]
  wins:       number
  losses:     number
  normPnl:    number              // batch total normalized P&L
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Mirror of resolver.py _phase2_normalized_size(): $150 budget, $20 cap. */
function normSize(confidence: number): number {
  const pct =
    confidence >= 0.95 ? 0.20 :
    confidence >= 0.90 ? 0.15 :
    confidence >= 0.80 ? 0.10 : 0.06
  return Math.min(150 * pct, 20)
}

function toNormPnl(pnl: number, size: number, confidence: number): number {
  if (size <= 0) return 0
  return Math.round(pnl * (normSize(confidence) / size) * 100) / 100
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useNotifications() {
  const [batches,       setBatches]       = useState<NotificationBatch[]>([])
  const [loading,       setLoading]       = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  const fetch = useCallback(async () => {
    try {
      const thirtyDaysAgo = new Date()
      thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30)

      // Once live trading has started, the notifications feed should ONLY
      // show real-money resolutions — paper-era trades shouldn't appear in
      // the user's "what happened today" stream.
      const liveStartRes = await supabase
        .from('system_config')
        .select('value')
        .eq('key', 'live_start_date')
        .maybeSingle()
      const liveStartDate = liveStartRes.data?.value ?? null

      let q = supabase
        .from('trade_signals')
        .select('city, outcome, pnl_usd, recommended_position, confidence, market_price, resolved_at, forecast_date, order_status')
        .eq('signal_phase', 'phase2')
        .not('pnl_usd', 'is', null)
        .gte('resolved_at', thirtyDaysAgo.toISOString())
        .order('resolved_at', { ascending: false })
        .limit(500)
      if (liveStartDate) {
        q = q.gte('forecast_date', liveStartDate)
             .in('order_status', ['filled', 'sold'])
      }
      const { data } = await q

      const rows = data ?? []

      // Group by resolved_at truncated to the minute
      const groups: Record<string, typeof rows> = {}
      for (const row of rows) {
        const key = (row.resolved_at as string).slice(0, 16) // "YYYY-MM-DDTHH:MM"
        if (!groups[key]) groups[key] = []
        groups[key].push(row)
      }

      const result: NotificationBatch[] = Object.entries(groups)
        .sort(([a], [b]) => b.localeCompare(a))   // reverse chrono
        .map(([key, trades]) => {
          const notifTrades: NotificationTrade[] = trades
            .map(r => ({
              city:         r.city as string,
              outcome:      r.outcome as string,
              pnl:          r.pnl_usd as number,
              normPnl:      toNormPnl(r.pnl_usd as number, r.recommended_position as number, r.confidence as number),
              confidence:   r.confidence as number,
              marketPrice:  r.market_price as number,
              positionSize: r.recommended_position as number,
              won:          (r.pnl_usd as number) > 0,
            }))
            .sort((a, b) => b.normPnl - a.normPnl)   // wins first within batch

          const normPnl = Math.round(notifTrades.reduce((s, t) => s + t.normPnl, 0) * 100) / 100

          return {
            batchKey:   key,
            resolvedAt: trades[0].resolved_at as string,
            trades:     notifTrades,
            wins:       notifTrades.filter(t => t.won).length,
            losses:     notifTrades.filter(t => !t.won).length,
            normPnl,
          }
        })

      setBatches(result)
      setLastRefreshed(new Date())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, 60_000)
    return () => clearInterval(id)
  }, [fetch])

  return { batches, loading, lastRefreshed, refresh: fetch }
}
