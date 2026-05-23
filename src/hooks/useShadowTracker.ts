import { useState, useEffect } from 'react'
import supabase from '../lib/supabase'

const CALIB_MIN = 2   // hierarchical Bayesian: n>=2 qualifies
const REAL_STAKE = 15.0

export interface ShadowTier {
  label:        string  // "30-40¢", "40-50¢", etc.
  minPrice:     number
  maxPrice:     number
  // Counts
  totalTrades:  number
  resolved:     number
  open:         number
  wins:         number
  losses:       number
  winRate:      number
  // Hypothetical P&L (as if stake was $45 instead of $0.01)
  hypotheticalPnl:    number
  realisticBreakeven: number  // win rate needed for net-zero at this band
}

export interface ShadowSummary {
  tiers:           ShadowTier[]
  totalShadowPnl:  number     // sum of hypothetical P&L across all expanded tiers
  totalResolved:   number     // resolved shadow trades counted
  currentCapPnl:   number     // real P&L under the active 30¢ cap (for context)
  startedAt:       string     // date of earliest shadow trade
}

function midpoint(min: number, max: number): number {
  return (min + max) / 2
}

export function useShadowTracker() {
  const [shadow, setShadow] = useState<ShadowSummary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let mounted = true

    async function fetchShadow() {
      try {
        // 1. Get calibrated cities
        const { data: stations } = await supabase
          .from('resolution_stations')
          .select('city, delta_samples')

        const calibratedCities = new Set(
          (stations ?? [])
            .filter((s) => Number(s.delta_samples ?? 0) >= CALIB_MIN)
            .map((s) => s.city)
        )

        // 2. Pull ALL Phase 2 signals for calibrated cities
        const { data: signals } = await supabase
          .from('trade_signals')
          .select('city, outcome, market_price, recommended_position, actual_outcome, pnl_usd, forecast_date, miss_distance_c')
          .eq('signal_phase', 'phase2')
          .order('forecast_date', { ascending: true })
          .limit(2000)

        if (!mounted) return

        const calibratedSignals = (signals ?? []).filter((s) =>
          calibratedCities.has(s.city)
        )

        // 3. Real P&L under current 30¢ cap (size > 1 = real-money trade)
        const realMoney = calibratedSignals.filter(
          (s) => Number(s.recommended_position ?? 0) > 1
        )
        const currentCapPnl = realMoney
          .filter((s) => s.pnl_usd != null)
          .reduce((acc, s) => acc + Number(s.pnl_usd), 0)

        // 4. Define shadow tiers (calibrated city + observation due to price)
        const tierBands: { label: string, min: number, max: number }[] = [
          { label: '30-40¢',  min: 0.30, max: 0.40 },
          { label: '40-50¢',  min: 0.40, max: 0.50 },
          { label: '50-60¢',  min: 0.50, max: 0.60 },
          { label: '60-70¢',  min: 0.60, max: 0.70 },
          { label: '70¢+',    min: 0.70, max: 1.00 },
        ]

        const tiers: ShadowTier[] = tierBands.map((band) => {
          const inBand = calibratedSignals.filter((s) => {
            const p = Number(s.market_price)
            const size = Number(s.recommended_position ?? 0)
            return p >= band.min && p < band.max && size <= 1  // observation only
          })

          const resolved = inBand.filter((s) => s.pnl_usd != null)
          const open     = inBand.filter((s) => s.pnl_usd == null)

          // Count wins/losses based on actual_outcome (string 'true'/'false' from DB)
          let wins = 0
          let losses = 0
          let hypotheticalPnl = 0
          for (const s of resolved) {
            const won = String(s.actual_outcome).toLowerCase() === 'true'
            const price = Number(s.market_price)
            if (won) {
              wins += 1
              hypotheticalPnl += REAL_STAKE * (1 / price - 1)
            } else {
              losses += 1
              hypotheticalPnl -= REAL_STAKE
            }
          }
          const n = wins + losses
          return {
            label:    band.label,
            minPrice: band.min,
            maxPrice: band.max,
            totalTrades: inBand.length,
            resolved:    resolved.length,
            open:        open.length,
            wins,
            losses,
            winRate:           n > 0 ? wins / n * 100 : 0,
            hypotheticalPnl:   Math.round(hypotheticalPnl * 100) / 100,
            realisticBreakeven: midpoint(band.min, band.max) * 100,
          }
        })

        const totalShadowPnl = tiers.reduce((acc, t) => acc + t.hypotheticalPnl, 0)
        const totalResolved = tiers.reduce((acc, t) => acc + t.resolved, 0)
        const startedAt = calibratedSignals.length > 0
          ? calibratedSignals[0].forecast_date ?? ''
          : ''

        setShadow({
          tiers,
          totalShadowPnl: Math.round(totalShadowPnl * 100) / 100,
          totalResolved,
          currentCapPnl,
          startedAt,
        })
        setLoading(false)
      } catch (e) {
        console.error('useShadowTracker error', e)
        setLoading(false)
      }
    }

    fetchShadow()
    const interval = setInterval(fetchShadow, 60_000)
    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [])

  return { shadow, loading }
}
