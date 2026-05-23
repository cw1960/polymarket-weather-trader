import { useState, useEffect } from 'react'
import supabase from '../lib/supabase'

export interface FillRecord {
  id:               string
  city:             string
  forecast_date:    string
  outcome:          string
  intended_price:   number | null
  bid_at_signal:    number | null
  ask_at_signal:    number | null
  mid_at_signal:    number | null
  fill_price:       number | null
  fill_latency_ms:  number | null
  order_status:     string | null
  created_at:       string
}

export interface FillStats {
  fills:                FillRecord[]
  totalFills:           number
  avgSlippageCents:     number    // (fill_price - mid_at_signal) * 100
  avgLatencyMs:         number
  avgIntendedVsFill:    number    // (fill - intended) * 100
  worstSlippage:        number
  maxLatency:           number
}

export function useExecutionTelemetry() {
  const [stats, setStats] = useState<FillStats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let mounted = true

    async function fetchTelemetry() {
      try {
        // Live filled trades only
        const { data: liveStartRes } = await supabase
          .from('system_config').select('value').eq('key','live_start_date').maybeSingle()
        const liveStartDate = liveStartRes?.value ?? null

        if (!liveStartDate) {
          setStats(null)
          setLoading(false)
          return
        }

        const { data } = await supabase
          .from('trade_signals')
          .select('id, city, forecast_date, outcome, intended_price, bid_at_signal, ask_at_signal, mid_at_signal, fill_price, fill_latency_ms, order_status, created_at')
          .gte('forecast_date', liveStartDate)
          // 'filled' = filled by the bot; 'sold' = manually closed via Polymarket UI
          // (record_manual_sale.py).  Both represent real fills worth measuring.
          .in('order_status', ['filled', 'sold'])
          .not('fill_price', 'is', null)
          .order('created_at', { ascending: false })
          .limit(100)

        if (!mounted) return
        const fills = (data ?? []) as FillRecord[]

        if (fills.length === 0) {
          setStats({ fills: [], totalFills: 0, avgSlippageCents: 0,
                     avgLatencyMs: 0, avgIntendedVsFill: 0,
                     worstSlippage: 0, maxLatency: 0 })
          setLoading(false)
          return
        }

        // Slippage = fill_price - mid_at_signal (negative is good for buyer)
        const withMid = fills.filter((f) => f.fill_price != null && f.mid_at_signal != null)
        const slippages = withMid.map((f) =>
          (Number(f.fill_price) - Number(f.mid_at_signal)) * 100
        )

        const withIntended = fills.filter((f) => f.fill_price != null && f.intended_price != null)
        const intendedDeltas = withIntended.map((f) =>
          (Number(f.fill_price) - Number(f.intended_price)) * 100
        )

        const withLatency = fills.filter((f) => f.fill_latency_ms != null)
        const latencies = withLatency.map((f) => Number(f.fill_latency_ms))

        const avg = (arr: number[]) => arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0

        setStats({
          fills,
          totalFills: fills.length,
          avgSlippageCents:  avg(slippages),
          avgLatencyMs:      avg(latencies),
          avgIntendedVsFill: avg(intendedDeltas),
          worstSlippage:     slippages.length > 0 ? Math.max(...slippages) : 0,
          maxLatency:        latencies.length > 0 ? Math.max(...latencies) : 0,
        })
        setLoading(false)
      } catch (e) {
        console.error('useExecutionTelemetry error', e)
        setLoading(false)
      }
    }

    fetchTelemetry()
    const interval = setInterval(fetchTelemetry, 60_000)
    return () => { mounted = false; clearInterval(interval) }
  }, [])

  return { stats, loading }
}
