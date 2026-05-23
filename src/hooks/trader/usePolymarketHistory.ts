// usePolymarketHistory — pulls full-history price series for every bracket
// of an event from Polymarket's CLOB prices-history endpoint. Same data
// that powers the chart on polymarket.com/event/.../highest-temp-...
//
// Endpoint: GET https://clob.polymarket.com/prices-history
//             ?market={YES_tokenId}&interval=all&fidelity={minutes}
// Returns: { history: [{ t: unix_seconds, p: 0..1 }] }
//
// We make one request per bracket (11 events × ~5KB each), in parallel.
// CORS is open on clob.polymarket.com (it's how the Polymarket frontend
// renders its own charts).

import { useCallback, useEffect, useState } from 'react'
import { buildEventSlug, parseBracketLabel } from '../../lib/polymarketSlugs'

export interface PMHistoryPoint {
  ms: number       // unix ms (converted from server's unix seconds)
  p: number        // YES price 0..1
}

export interface PMBracketHistory {
  bracket_label: string
  condition_id: string
  yes_token_id: string
  bracket_low_native: number | null
  bracket_high_native: number | null
  points: PMHistoryPoint[]
}

export interface PMHistoryData {
  brackets: PMBracketHistory[]       // ordered by bracket_low_native asc
  loading: boolean
  error: string | null
  lastFetched: Date | null
  interval: string
  fidelityMin: number
}


export type PMInterval = '1h' | '6h' | '1d' | '1w' | '1m' | 'max'

// Polymarket's intervals + fidelity (minutes per point). Lower fidelity =
// more granular chart. Empirically (probe 2026-05-22):
//   • fidelity=1 works fine up to 1D (1,438 points for 24h, all brackets fine)
//   • fidelity=1 returns HTTP 400 for 1W / 1M (too many points)
//   • interval=max ignores fidelity, server picks ~10min cadence
const INTERVAL_FIDELITY: Record<PMInterval, { interval: string; fidelity: number }> = {
  '1h':  { interval: '1h',  fidelity: 1   },     // 60 pts
  '6h':  { interval: '6h',  fidelity: 1   },     // 360 pts
  '1d':  { interval: '1d',  fidelity: 1   },     // 1,440 pts — minute-level for the whole day
  '1w':  { interval: '1w',  fidelity: 10  },     // ~1,000 pts
  '1m':  { interval: '1m',  fidelity: 30  },     // ~1,400 pts
  'max': { interval: 'max', fidelity: 1   },     // server-capped
}


// Refresh every 30s. CLOB history is aggregated server-side; sub-30s
// polling won't surface fresh ticks unless we drop fidelity to 1 min.
const REFRESH_MS = 30_000


export function usePolymarketHistory(
  city: string | null,
  forecastDate: string | null,
  intervalKey: PMInterval = 'max',
): PMHistoryData {
  const [brackets, setBrackets] = useState<PMBracketHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)
  const cfg = INTERVAL_FIDELITY[intervalKey]

  const fetchAll = useCallback(async () => {
    if (!city || !forecastDate) {
      setBrackets([])
      setLoading(false)
      return
    }
    const slug = buildEventSlug(city, forecastDate)
    if (!slug) {
      setError('no slug')
      setLoading(false)
      return
    }
    try {
      // 1) Resolve the event → list of markets + YES tokenIds.
      const evResp = await fetch(`https://gamma-api.polymarket.com/events/slug/${slug}`)
      if (!evResp.ok) {
        setError(`gamma HTTP ${evResp.status}`)
        setLoading(false)
        return
      }
      const ev = await evResp.json()

      type MarketStub = {
        bracket_label: string
        condition_id: string
        yes_token_id: string
        bracket_low_native: number | null
        bracket_high_native: number | null
      }
      const markets: MarketStub[] = []
      for (const m of (ev?.markets ?? []) as Record<string, unknown>[]) {
        const q = (m.question as string) ?? ''
        const label = parseBracketLabel(q)
        if (!label) continue
        let tokenIds: unknown = m.clobTokenIds
        if (typeof tokenIds === 'string') {
          try { tokenIds = JSON.parse(tokenIds) } catch { tokenIds = [] }
        }
        const yesId = Array.isArray(tokenIds) && tokenIds.length > 0 ? String(tokenIds[0]) : ''
        if (!yesId) continue
        markets.push({
          bracket_label: label,
          condition_id: (m.conditionId as string) ?? '',
          yes_token_id: yesId,
          bracket_low_native: null,        // not needed for chart ordering — we sort by parsing label
          bracket_high_native: null,
        })
      }

      // 2) Fetch history for every market in parallel.
      const histResults = await Promise.all(
        markets.map(async (mkt) => {
          const url = `https://clob.polymarket.com/prices-history?market=${mkt.yes_token_id}&interval=${cfg.interval}&fidelity=${cfg.fidelity}`
          try {
            const r = await fetch(url)
            if (!r.ok) return { mkt, points: [] as PMHistoryPoint[] }
            const j = await r.json()
            const hist = (j?.history as { t: number; p: number }[] | undefined) ?? []
            const pts: PMHistoryPoint[] = hist
              .filter((h) => Number.isFinite(h.t) && Number.isFinite(h.p))
              .map((h) => ({ ms: h.t * 1000, p: h.p }))
              .sort((a, b) => a.ms - b.ms)
            return { mkt, points: pts }
          } catch {
            return { mkt, points: [] as PMHistoryPoint[] }
          }
        }),
      )

      // 3) Sort brackets by parsing low temp out of the label so the chart
      //    legend / line order matches the Watchlist + table.
      const labelLowTemp = (label: string): number => {
        const m1 = label.match(/(-?\d+)-(-?\d+)/)
        if (m1) return parseInt(m1[1], 10)
        const m2 = label.match(/(?:≤|≥)?(-?\d+)/)
        if (m2) return parseInt(m2[1], 10)
        return 0
      }
      const out: PMBracketHistory[] = histResults
        .map(({ mkt, points }) => ({
          bracket_label: mkt.bracket_label,
          condition_id: mkt.condition_id,
          yes_token_id: mkt.yes_token_id,
          bracket_low_native: labelLowTemp(mkt.bracket_label),
          bracket_high_native: null,
          points,
        }))
        .sort((a, b) => (a.bracket_low_native ?? 0) - (b.bracket_low_native ?? 0))

      setBrackets(out)
      setError(null)
      setLastFetched(new Date())
      setLoading(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setLoading(false)
    }
  }, [city, forecastDate, cfg.interval, cfg.fidelity])

  useEffect(() => {
    setLoading(true)
    fetchAll()
    const id = setInterval(fetchAll, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchAll])

  return {
    brackets,
    loading,
    error,
    lastFetched,
    interval: cfg.interval,
    fidelityMin: cfg.fidelity,
  }
}
