import { useState, useEffect, useCallback } from 'react'
import supabase from '../lib/supabase'

// ── Normalization (mirrors resolver.py / useTrades) ───────────────────────────

function normP2Size(conf: number): number {
  const pct = conf >= 0.95 ? 0.20 : conf >= 0.90 ? 0.15 : conf >= 0.80 ? 0.10 : 0.06
  return Math.min(150 * pct, 20)
}

function toNorm(pnl: number, size: number, conf: number): number {
  return size > 0 ? Math.round(pnl * (normP2Size(conf) / size) * 100) / 100 : 0
}

// ── Raw DB row ────────────────────────────────────────────────────────────────

interface RawRow {
  city:                 string
  outcome:              string
  pnl_usd:              number
  recommended_position: number
  confidence:           number
  market_price:         number
  model_probability:    number | null
  signal_time:          string
  resolved_at:          string
}

// ── Exported types ────────────────────────────────────────────────────────────

export interface CalibrationBin {
  label:     string
  predicted: number   // confidence midpoint (0–1)
  actual:    number   // real win rate (0–1)
  count:     number
}

export interface BrierPoint {
  date:    string
  brier7d:  number | null
  brier30d: number | null
}

export interface EdgeDecayBin {
  label:      string
  winRate:    number
  count:      number
  avgNormPnl: number
}

export interface CityEdgeRow {
  city:         string
  wins:         number
  losses:       number
  trades:       number
  winRate:      number
  avgNormPnl:   number
  totalNormPnl: number
  brier:        number
}

export interface PriceBucket {
  label:  string
  wins:   number
  losses: number
}

export interface OutcomeRow {
  label:   string
  wins:    number
  losses:  number
  winRate: number
}

export interface TierRow {
  label:        string
  wins:         number
  losses:       number
  trades:       number
  winRate:      number
  avgNormPnl:   number
  totalNormPnl: number
}

export interface HourPoint {
  hour:       string
  avgNormPnl: number
  trades:     number
}

export interface DaysPoint {
  label:      string
  avgNormPnl: number
  winRate:    number
  trades:     number
}

export interface DrawdownPoint {
  date:    string
  cumPnl:  number
  drawdown: number
}

export interface BudgetPoint {
  date:     string
  deployed: number
}

export interface ScatterPoint {
  modelProb:   number
  marketPrice: number
  won:         boolean
  city:        string
  normPnl:     number
}

export interface EdgeBucket {
  label:   string
  count:   number
  winRate: number
}

export interface FunnelStats {
  phase1Total:    number
  phase2Total:    number
  phase2Resolved: number
  conversionPct:  number
}

export interface AnalyticsData {
  // Section 1 — Model Quality
  calibration:   CalibrationBin[]
  brierTrend:    BrierPoint[]
  edgeDecay:     EdgeDecayBin[]
  // Section 2 — City & Market
  cityEdge:      CityEdgeRow[]
  priceDistrib:  PriceBucket[]
  outcomeBias:   OutcomeRow[]
  // Section 3 — P&L Attribution
  pnlByTier:     TierRow[]
  pnlByHour:     HourPoint[]
  pnlByDays:     DaysPoint[]
  // Section 4 — Risk & Exposure
  drawdown:      DrawdownPoint[]
  budgetUtil:    BudgetPoint[]
  // Section 5 — Signal Health
  scatter:       ScatterPoint[]
  edgeHistogram: EdgeBucket[]
  funnel:        FunnelStats
  totalResolved: number
}

// ── Computation helpers ───────────────────────────────────────────────────────

function computeCalibration(rows: RawRow[]): CalibrationBin[] {
  const defs = [
    { label: '70–79%', min: 0.70, max: 0.80, mid: 0.75  },
    { label: '80–84%', min: 0.80, max: 0.85, mid: 0.82  },
    { label: '85–89%', min: 0.85, max: 0.90, mid: 0.875 },
    { label: '90–94%', min: 0.90, max: 0.95, mid: 0.925 },
    { label: '95%+',   min: 0.95, max: 1.01, mid: 0.975 },
  ]
  return defs
    .map(d => {
      const bin  = rows.filter(r => r.confidence >= d.min && r.confidence < d.max)
      const wins = bin.filter(r => r.pnl_usd > 0).length
      return { label: d.label, predicted: d.mid, actual: bin.length ? wins / bin.length : 0, count: bin.length }
    })
    .filter(b => b.count > 0)
}

function computeBrierTrend(rows: RawRow[]): BrierPoint[] {
  // Build set of unique dates (ascending)
  const dateSet = new Set(rows.map(r => r.resolved_at.slice(0, 10)))
  const dates   = [...dateSet].sort()

  function brierWindow(endDate: string, days: number): number | null {
    const end   = new Date(endDate).getTime()
    const start = end - days * 86_400_000
    const win   = rows.filter(r => {
      const t = new Date(r.resolved_at.slice(0, 10)).getTime()
      return t >= start && t <= end
    })
    if (win.length < 3) return null
    const bs = win.reduce((s, r) => {
      const won = r.pnl_usd > 0 ? 1 : 0
      return s + (r.confidence - won) ** 2
    }, 0) / win.length
    return Math.round(bs * 10_000) / 10_000
  }

  return dates.map(d => ({
    date:    new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }),
    brier7d:  brierWindow(d, 7),
    brier30d: brierWindow(d, 30),
  }))
}

function computeEdgeDecay(rows: RawRow[]): EdgeDecayBin[] {
  const bins = [
    { label: '0–6 h',  min: 0,   max: 6   },
    { label: '6–12 h', min: 6,   max: 12  },
    { label: '12–24 h',min: 12,  max: 24  },
    { label: '1–2 d',  min: 24,  max: 48  },
    { label: '2–4 d',  min: 48,  max: 96  },
    { label: '4–7 d',  min: 96,  max: 168 },
    { label: '7 d+',   min: 168, max: Infinity },
  ]
  return bins
    .map(b => {
      const bin  = rows.filter(r => {
        const h = (new Date(r.resolved_at).getTime() - new Date(r.signal_time).getTime()) / 3_600_000
        return h >= b.min && h < b.max
      })
      const wins = bin.filter(r => r.pnl_usd > 0).length
      const normPnls = bin.map(r => toNorm(r.pnl_usd, r.recommended_position, r.confidence))
      return {
        label:      b.label,
        winRate:    bin.length ? wins / bin.length : 0,
        count:      bin.length,
        avgNormPnl: bin.length ? Math.round(normPnls.reduce((s, v) => s + v, 0) / bin.length * 100) / 100 : 0,
      }
    })
    .filter(b => b.count > 0)
}

function computeCityEdge(rows: RawRow[]): CityEdgeRow[] {
  const map = new Map<string, { wins: number; losses: number; normPnls: number[]; brierSum: number }>()
  for (const r of rows) {
    if (!map.has(r.city)) map.set(r.city, { wins: 0, losses: 0, normPnls: [], brierSum: 0 })
    const s   = map.get(r.city)!
    const won = r.pnl_usd > 0
    won ? s.wins++ : s.losses++
    s.normPnls.push(toNorm(r.pnl_usd, r.recommended_position, r.confidence))
    s.brierSum += (r.confidence - (won ? 1 : 0)) ** 2
  }
  return [...map.entries()]
    .map(([city, s]) => {
      const trades = s.wins + s.losses
      const total  = s.normPnls.reduce((a, b) => a + b, 0)
      return {
        city,
        wins:         s.wins,
        losses:       s.losses,
        trades,
        winRate:      trades ? s.wins / trades : 0,
        avgNormPnl:   trades ? Math.round(total / trades * 100) / 100 : 0,
        totalNormPnl: Math.round(total * 100) / 100,
        brier:        trades ? Math.round(s.brierSum / trades * 10_000) / 10_000 : 0,
      }
    })
    .sort((a, b) => b.totalNormPnl - a.totalNormPnl)
}

function computePriceDistrib(rows: RawRow[]): PriceBucket[] {
  return Array.from({ length: 10 }, (_, i) => {
    const min = i * 0.10
    const max = min + 0.10
    const bin = rows.filter(r => r.market_price >= min && r.market_price < max)
    return {
      label:  `${i * 10}–${(i + 1) * 10}¢`,
      wins:   bin.filter(r => r.pnl_usd > 0).length,
      losses: bin.filter(r => r.pnl_usd <= 0).length,
    }
  }).filter(b => b.wins + b.losses > 0)
}

function computeOutcomeBias(rows: RawRow[]): OutcomeRow[] {
  return ['YES', 'NO'].map(label => {
    const bin  = rows.filter(r => r.outcome === label)
    const wins = bin.filter(r => r.pnl_usd > 0).length
    return { label, wins, losses: bin.length - wins, winRate: bin.length ? wins / bin.length : 0 }
  }).filter(r => r.wins + r.losses > 0)
}

function computePnlByTier(rows: RawRow[]): TierRow[] {
  const tiers = [
    { label: '70–79%', min: 0.70, max: 0.80 },
    { label: '80–89%', min: 0.80, max: 0.90 },
    { label: '90–94%', min: 0.90, max: 0.95 },
    { label: '95%+',   min: 0.95, max: 1.01 },
  ]
  return tiers
    .map(t => {
      const bin      = rows.filter(r => r.confidence >= t.min && r.confidence < t.max)
      const wins     = bin.filter(r => r.pnl_usd > 0).length
      const normPnls = bin.map(r => toNorm(r.pnl_usd, r.recommended_position, r.confidence))
      const total    = normPnls.reduce((s, v) => s + v, 0)
      return {
        label:        t.label,
        wins,
        losses:       bin.length - wins,
        trades:       bin.length,
        winRate:      bin.length ? wins / bin.length : 0,
        avgNormPnl:   bin.length ? Math.round(total / bin.length * 100) / 100 : 0,
        totalNormPnl: Math.round(total * 100) / 100,
      }
    })
    .filter(t => t.trades > 0)
}

function computePnlByHour(rows: RawRow[]): HourPoint[] {
  const map = new Map<number, { total: number; count: number }>()
  for (const r of rows) {
    const h = new Date(r.signal_time).getUTCHours()
    if (!map.has(h)) map.set(h, { total: 0, count: 0 })
    const s = map.get(h)!
    s.total += toNorm(r.pnl_usd, r.recommended_position, r.confidence)
    s.count++
  }
  return [...map.entries()]
    .sort(([a], [b]) => a - b)
    .map(([h, s]) => ({
      hour:       `${String(h).padStart(2, '0')}:00`,
      avgNormPnl: Math.round(s.total / s.count * 100) / 100,
      trades:     s.count,
    }))
}

function computePnlByDays(rows: RawRow[]): DaysPoint[] {
  const bins = [
    { label: 'Same day', min: 0, max: 1  },
    { label: '1 d',      min: 1, max: 2  },
    { label: '2 d',      min: 2, max: 3  },
    { label: '3 d',      min: 3, max: 4  },
    { label: '4 d',      min: 4, max: 5  },
    { label: '5–7 d',    min: 5, max: 7  },
    { label: '7 d+',     min: 7, max: Infinity },
  ]
  return bins
    .map(b => {
      const bin  = rows.filter(r => {
        const d = (new Date(r.resolved_at).getTime() - new Date(r.signal_time).getTime()) / 86_400_000
        return d >= b.min && d < b.max
      })
      const wins     = bin.filter(r => r.pnl_usd > 0).length
      const normPnls = bin.map(r => toNorm(r.pnl_usd, r.recommended_position, r.confidence))
      const total    = normPnls.reduce((s, v) => s + v, 0)
      return {
        label:      b.label,
        avgNormPnl: bin.length ? Math.round(total / bin.length * 100) / 100 : 0,
        winRate:    bin.length ? wins / bin.length : 0,
        trades:     bin.length,
      }
    })
    .filter(b => b.trades > 0)
}

function computeDrawdown(rows: RawRow[]): DrawdownPoint[] {
  const byDate = new Map<string, number>()
  for (const r of rows) {
    const d = r.resolved_at.slice(0, 10)
    byDate.set(d, (byDate.get(d) ?? 0) + toNorm(r.pnl_usd, r.recommended_position, r.confidence))
  }
  let cumPnl = 0
  let peak   = 0
  return [...byDate.keys()]
    .sort()
    .map(d => {
      cumPnl += byDate.get(d)!
      if (cumPnl > peak) peak = cumPnl
      return {
        date:     new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }),
        cumPnl:   Math.round(cumPnl * 100) / 100,
        drawdown: Math.round(Math.min(0, cumPnl - peak) * 100) / 100,
      }
    })
}

function computeBudgetUtil(rows: { signal_time: string; recommended_position: number }[]): BudgetPoint[] {
  const byDate = new Map<string, number>()
  for (const r of rows) {
    const d = r.signal_time.slice(0, 10)
    byDate.set(d, (byDate.get(d) ?? 0) + r.recommended_position)
  }
  return [...byDate.keys()]
    .sort()
    .slice(-30) // last 30 days
    .map(d => ({
      date:     new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }),
      deployed: Math.round(byDate.get(d)! * 100) / 100,
    }))
}

function computeScatter(rows: RawRow[]): ScatterPoint[] {
  return rows
    .filter(r => r.model_probability != null)
    .map(r => ({
      modelProb:   Math.round(r.model_probability! * 1000) / 1000,
      marketPrice: Math.round(r.market_price       * 1000) / 1000,
      won:         r.pnl_usd > 0,
      city:        r.city,
      normPnl:     toNorm(r.pnl_usd, r.recommended_position, r.confidence),
    }))
}

function computeEdgeHistogram(rows: RawRow[]): EdgeBucket[] {
  const bins = [
    { label: '< 0%',    min: -1,   max: 0    },
    { label: '0–5%',    min: 0,    max: 0.05 },
    { label: '5–10%',   min: 0.05, max: 0.10 },
    { label: '10–15%',  min: 0.10, max: 0.15 },
    { label: '15–20%',  min: 0.15, max: 0.20 },
    { label: '20–30%',  min: 0.20, max: 0.30 },
    { label: '30%+',    min: 0.30, max: Infinity },
  ]
  return bins
    .map(b => {
      const bin  = rows.filter(r => {
        if (r.model_probability == null) return false
        const edge = r.model_probability - r.market_price
        return edge >= b.min && edge < b.max
      })
      const wins = bin.filter(r => r.pnl_usd > 0).length
      return { label: b.label, count: bin.length, winRate: bin.length ? wins / bin.length : 0 }
    })
    .filter(b => b.count > 0)
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAnalytics() {
  const [data,          setData]          = useState<AnalyticsData | null>(null)
  const [loading,       setLoading]       = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  const fetch = useCallback(async () => {
    try {
      const ninetyDaysAgo = new Date()
      ninetyDaysAgo.setDate(ninetyDaysAgo.getDate() - 90)
      const thirtyDaysAgo = new Date()
      thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30)

      const [resolvedRes, budgetRes, p1Res, p2Res, p2ResolvedRes] = await Promise.all([
        supabase
          .from('trade_signals')
          .select('city, outcome, pnl_usd, recommended_position, confidence, market_price, model_probability, signal_time, resolved_at')
          .eq('signal_phase', 'phase2')
          .not('pnl_usd', 'is', null)
          .gte('signal_time', ninetyDaysAgo.toISOString())
          .order('resolved_at', { ascending: true })
          .limit(2000),

        supabase
          .from('trade_signals')
          .select('signal_time, recommended_position')
          .eq('signal_phase', 'phase2')
          .gte('signal_time', thirtyDaysAgo.toISOString()),

        supabase.from('trade_signals')
          .select('id', { count: 'exact', head: true })
          .eq('signal_phase', 'phase1')
          .gte('signal_time', ninetyDaysAgo.toISOString()),

        supabase.from('trade_signals')
          .select('id', { count: 'exact', head: true })
          .eq('signal_phase', 'phase2')
          .gte('signal_time', ninetyDaysAgo.toISOString()),

        supabase.from('trade_signals')
          .select('id', { count: 'exact', head: true })
          .eq('signal_phase', 'phase2')
          .not('pnl_usd', 'is', null)
          .gte('signal_time', ninetyDaysAgo.toISOString()),
      ])

      const rows       = (resolvedRes.data ?? []) as RawRow[]
      const budgetRows = (budgetRes.data ?? []) as { signal_time: string; recommended_position: number }[]
      const p1Total    = p1Res.count    ?? 0
      const p2Total    = p2Res.count    ?? 0
      const p2Resolved = p2ResolvedRes.count ?? 0

      setData({
        calibration:   computeCalibration(rows),
        brierTrend:    computeBrierTrend(rows),
        edgeDecay:     computeEdgeDecay(rows),
        cityEdge:      computeCityEdge(rows),
        priceDistrib:  computePriceDistrib(rows),
        outcomeBias:   computeOutcomeBias(rows),
        pnlByTier:     computePnlByTier(rows),
        pnlByHour:     computePnlByHour(rows),
        pnlByDays:     computePnlByDays(rows),
        drawdown:      computeDrawdown(rows),
        budgetUtil:    computeBudgetUtil(budgetRows),
        scatter:       computeScatter(rows),
        edgeHistogram: computeEdgeHistogram(rows),
        funnel: {
          phase1Total:    p1Total,
          phase2Total:    p2Total,
          phase2Resolved: p2Resolved,
          conversionPct:  p1Total > 0 ? p2Total / p1Total : 0,
        },
        totalResolved: rows.length,
      })
      setLastRefreshed(new Date())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, 120_000)
    return () => clearInterval(id)
  }, [fetch])

  return { data, loading, lastRefreshed, refresh: fetch }
}
