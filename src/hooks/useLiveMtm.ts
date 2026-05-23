import { useState, useEffect, useCallback } from 'react'

/**
 * Polls Polymarket's data-api /positions endpoint for our funder wallet
 * every 5 minutes and returns a Map<condition_id|side, currentPrice>.
 *
 * Used by the dashboard Positions panel to render an "Unrealized" column
 * that marks each open position to current market.  curPrice from the
 * data-api is the live price of the token the user actually holds (YES
 * or NO), so we can compute MTM directly: unrealized_$ = position_size_$
 * × (curPrice / entry_price − 1).
 *
 * No auth required — /positions is public for any wallet address.
 */
const DATA_API = 'https://data-api.polymarket.com'
const REFRESH_MS = 5 * 60 * 1000   // 5 minutes; matches reconciler cadence

const FUNDER = (import.meta.env.VITE_POLY_FUNDER_ADDRESS as string | undefined)?.toLowerCase() ?? ''

interface PmPosition {
  conditionId: string
  outcome: string         // 'Yes' | 'No'
  curPrice: number
  size: number
  avgPrice: number
}

/** Build the lookup key used by both the hook and consumers. */
export function mtmKey(condition_id: string | null | undefined, side: string): string {
  return `${(condition_id ?? '').toLowerCase()}|${side.toUpperCase()}`
}

export function useLiveMtm() {
  const [prices,        setPrices]        = useState<Map<string, number>>(new Map())
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const [error,         setError]         = useState<string | null>(null)

  const fetchPrices = useCallback(async () => {
    if (!FUNDER) {
      setError('VITE_POLY_FUNDER_ADDRESS not set')
      return
    }
    try {
      const r = await fetch(`${DATA_API}/positions?user=${FUNDER}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data: PmPosition[] = await r.json()
      const map = new Map<string, number>()
      for (const p of data) {
        if (!p.conditionId) continue
        const side = p.outcome === 'Yes' ? 'YES' : 'NO'
        map.set(mtmKey(p.conditionId, side), Number(p.curPrice))
      }
      setPrices(map)
      setLastRefreshed(new Date())
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'fetch failed')
    }
  }, [])

  useEffect(() => {
    fetchPrices()
    const id = setInterval(fetchPrices, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchPrices])

  return { prices, lastRefreshed, error }
}
