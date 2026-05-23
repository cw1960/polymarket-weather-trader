import { useState, useEffect } from 'react'
import supabase from '../lib/supabase'

export interface PrecisionMetrics {
  // Overall
  avgMissDistance:   number     // °C
  resolvedRealCount: number
  exactCount:        number     // miss = 0
  oneOffCount:       number     // miss = 1°C
  twoOffCount:       number     // miss = 2°C
  // Recent (last 7 days)
  recentAvgMiss:     number
  recentCount:       number
  // Calibration state
  calibratedCities:  number
  uncalibratedCities: number
}

export interface CityCalibration {
  city:           string
  delta_c:        number
  delta_samples:  number
  calibrated:     boolean
  // Variance / K (computed client-side from observed deltas)
  sigma_c:        number | null
  k_adj:          number | null
  buffer_active:  boolean | null
  recent_misses:  number[]   // last 5 miss_distance values
  avg_miss:       number | null
}

const STABILITY_THRESHOLD = 0.3
const K_BASE = 5
const CALIB_MIN = 2   // hierarchical Bayesian: n>=2 qualifies with informed prior

function median(arr: number[]): number {
  if (arr.length === 0) return 0
  const sorted = [...arr].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid]
}

function stdev(arr: number[]): number {
  if (arr.length < 2) return 0
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length
  const variance = arr.reduce((s, x) => s + (x - mean) ** 2, 0) / (arr.length - 1)
  return Math.sqrt(variance)
}

export function usePrecisionMetrics() {
  const [metrics, setMetrics]        = useState<PrecisionMetrics | null>(null)
  const [calibration, setCalibration] = useState<CityCalibration[]>([])
  const [loading, setLoading]         = useState(true)

  useEffect(() => {
    let mounted = true

    async function fetchAll() {
      try {
        // 1. Pull all resolved Phase 2 trades with miss_distance + mean_high
        const { data: trades } = await supabase
          .from('trade_signals')
          .select('city, mean_high, winning_bracket, miss_distance_c, recommended_position, resolved_at')
          .eq('signal_phase', 'phase2')
          .not('pnl_usd', 'is', null)
          .not('mean_high', 'is', null)
          .order('resolved_at', { ascending: false })
          .limit(500)

        // 2. Pull station calibration data
        const { data: stations } = await supabase
          .from('resolution_stations')
          .select('city, delta_c, delta_samples')

        if (!mounted) return

        const allTrades = trades ?? []
        const realTrades = allTrades.filter(
          (t) => Number(t.recommended_position ?? 0) > 1
        )

        // ── Overall miss-distance metrics ──
        const missValues = realTrades
          .map((t) => Number(t.miss_distance_c))
          .filter((m) => !isNaN(m) && m !== null)

        const avgMiss = missValues.length > 0
          ? missValues.reduce((a, b) => a + b, 0) / missValues.length
          : 0
        const exactCount  = missValues.filter((m) => m === 0).length
        const oneOffCount = missValues.filter((m) => Math.abs(m - 1) < 0.001).length
        const twoOffCount = missValues.filter((m) => m >= 1.5).length

        // ── Recent (last 7 days) ──
        const sevenDaysAgo = new Date()
        sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7)
        const recent = realTrades.filter((t) => {
          if (!t.resolved_at) return false
          return new Date(t.resolved_at) >= sevenDaysAgo
        })
        const recentMiss = recent
          .map((t) => Number(t.miss_distance_c))
          .filter((m) => !isNaN(m) && m !== null)
        const recentAvg = recentMiss.length > 0
          ? recentMiss.reduce((a, b) => a + b, 0) / recentMiss.length
          : 0

        // ── Per-city σ from observed deltas (in °C) ──
        const deltasByCity = new Map<string, number[]>()
        for (const t of allTrades) {
          if (t.mean_high == null || !t.winning_bracket) continue
          const meanHigh = Number(t.mean_high)
          if (meanHigh === 0) continue
          // Extract first integer from winning_bracket
          const m = String(t.winning_bracket).match(/-?\d+/)
          if (!m) continue
          // F→C conversion: detect by checking if city is US (we don't have CITY_UNITS here,
          // approximate by checking range — bracket numbers >50 are likely °F)
          const native = Number(m[0])
          const isF = native > 50   // pragmatic heuristic
          const actualC = isF ? (native - 32) * 5 / 9 : native
          const observedDelta = actualC - meanHigh
          const arr = deltasByCity.get(t.city) ?? []
          arr.push(observedDelta)
          deltasByCity.set(t.city, arr)
        }

        // Per-city sigmas
        const sigmaMap = new Map<string, number>()
        for (const [c, deltas] of deltasByCity.entries()) {
          if (deltas.length >= CALIB_MIN) {
            sigmaMap.set(c, stdev(deltas))
          }
        }
        const sigmaGlobal = sigmaMap.size > 0
          ? median(Array.from(sigmaMap.values()))
          : 0.5

        // ── Per-city miss distances (last 5) ──
        const missByCity = new Map<string, number[]>()
        for (const t of realTrades) {
          if (t.miss_distance_c == null) continue
          const arr = missByCity.get(t.city) ?? []
          arr.push(Number(t.miss_distance_c))
          missByCity.set(t.city, arr)
        }

        // ── Build calibration table ──
        const calib: CityCalibration[] = (stations ?? []).map((s) => {
          const sigma = sigmaMap.get(s.city) ?? null
          const samples = Number(s.delta_samples ?? 0)
          const calibrated = samples >= CALIB_MIN
          let kAdj: number | null = null
          let bufferActive: boolean | null = null
          if (calibrated) {
            if (sigma != null && sigmaGlobal > 0) {
              kAdj = Math.max(1, Math.min(10, K_BASE * (sigma / sigmaGlobal)))
              bufferActive = sigma >= STABILITY_THRESHOLD - 1e-6
            } else {
              kAdj = K_BASE
              bufferActive = true
            }
          }
          const cityMisses = missByCity.get(s.city) ?? []
          const recentMissesCity = cityMisses.slice(0, 5)
          const avg = cityMisses.length > 0
            ? cityMisses.reduce((a, b) => a + b, 0) / cityMisses.length
            : null
          return {
            city: s.city,
            delta_c: Number(s.delta_c ?? 0),
            delta_samples: samples,
            calibrated,
            sigma_c: sigma,
            k_adj: kAdj,
            buffer_active: bufferActive,
            recent_misses: recentMissesCity,
            avg_miss: avg,
          }
        })

        setMetrics({
          avgMissDistance:   avgMiss,
          resolvedRealCount: realTrades.length,
          exactCount,
          oneOffCount,
          twoOffCount,
          recentAvgMiss:     recentAvg,
          recentCount:       recent.length,
          calibratedCities:  calib.filter((c) => c.calibrated).length,
          uncalibratedCities: calib.filter((c) => !c.calibrated).length,
        })
        setCalibration(calib.sort((a, b) => a.city.localeCompare(b.city)))
        setLoading(false)
      } catch (e) {
        console.error('usePrecisionMetrics error', e)
        setLoading(false)
      }
    }

    fetchAll()
    const interval = setInterval(fetchAll, 60_000)
    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [])

  return { metrics, calibration, loading }
}
