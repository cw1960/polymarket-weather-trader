// useLivePolymarketEvent — pulls per-bracket live state from Polymarket
// every 2 seconds. Combines two sources:
//
//   1. gamma-api event endpoint — structural data (questions, conditionIds,
//      YES tokenIds, in-review / closed flags, last-trade prices).
//   2. clob.polymarket.com /books batch endpoint — TRUE bid/ask from the
//      live orderbook. Gamma's bestBid/bestAsk fields are cached/aggregated
//      and lag the orderbook by 5–30 seconds, which is enough to show
//      stale prices that differ from polymarket.com. Going to CLOB direct
//      makes our prices match Polymarket exactly.
//
// CORS: both endpoints accept browser requests (they're what Polymarket's
// own frontend uses).
//
// Network cost per refresh: 1 GET gamma + 1 POST clob/books = 2 requests
// total (the books call is batched across all 11 brackets). At 2s refresh
// that's 60 req/min while you're on a Trade Station — light.

import { useCallback, useEffect, useState } from 'react'
import { buildEventSlug, parseBracketLabel } from '../../lib/polymarketSlugs'

export interface LiveBracketTick {
  bracket_label: string
  condition_id: string
  yes_token_id: string | null
  yes_price: number | null         // last-trade YES price (from gamma)
  no_price: number | null          // last-trade NO price
  best_bid: number | null          // FROM CLOB (live orderbook)
  best_ask: number | null          // FROM CLOB (live orderbook)
  spread_pct: number | null
  in_review: boolean
  market_closed: boolean
}

export interface LiveEventData {
  byBracket: Record<string, LiveBracketTick>
  eventInReview: boolean
  eventClosed: boolean
  lastFetched: Date | null
  error: string | null
}

const POLL_MS = 2_000


function parseStatuses(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw as string[]
  if (typeof raw === 'string') {
    try {
      const v = JSON.parse(raw)
      return Array.isArray(v) ? (v as string[]) : []
    } catch { return [] }
  }
  return []
}


function parseOutcomePrices(raw: unknown): number[] {
  if (Array.isArray(raw)) return (raw as unknown[]).map((x) => Number(x))
  if (typeof raw === 'string') {
    try {
      const v = JSON.parse(raw)
      return Array.isArray(v) ? (v as unknown[]).map((x) => Number(x)) : []
    } catch { return [] }
  }
  return []
}


function parseTokenIds(raw: unknown): string[] {
  if (Array.isArray(raw)) return (raw as unknown[]).map((x) => String(x))
  if (typeof raw === 'string') {
    try {
      const v = JSON.parse(raw)
      return Array.isArray(v) ? (v as unknown[]).map((x) => String(x)) : []
    } catch { return [] }
  }
  return []
}


// CLOB orderbook quirk: the asks array is ordered DESCENDING (worst
// price first, best last); bids are ordered ASCENDING (worst first,
// best last). So "best ask" = asks[last], "best bid" = bids[last].
function bestFromBook(book: { asks?: { price: string }[]; bids?: { price: string }[] }):
  { bestBid: number | null; bestAsk: number | null } {
  const asks = book.asks ?? []
  const bids = book.bids ?? []
  const bestAsk = asks.length > 0 ? Number(asks[asks.length - 1].price) : null
  const bestBid = bids.length > 0 ? Number(bids[bids.length - 1].price) : null
  return {
    bestBid: bestBid != null && Number.isFinite(bestBid) ? bestBid : null,
    bestAsk: bestAsk != null && Number.isFinite(bestAsk) ? bestAsk : null,
  }
}


export function useLivePolymarketEvent(
  city: string | null,
  forecastDate: string | null,
): LiveEventData {
  const [byBracket, setByBracket] = useState<Record<string, LiveBracketTick>>({})
  const [eventInReview, setEventInReview] = useState(false)
  const [eventClosed, setEventClosed] = useState(false)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchEvent = useCallback(async () => {
    if (!city || !forecastDate) return
    const slug = buildEventSlug(city, forecastDate)
    if (!slug) return
    try {
      // 1) Gamma — structural + last-trade prices
      const r = await fetch(`https://gamma-api.polymarket.com/events/slug/${slug}`)
      if (!r.ok) { setError(`gamma HTTP ${r.status}`); return }
      const ev = await r.json()

      interface Stub {
        label: string
        condition_id: string
        yes_token: string | null
        yes_last: number | null
        no_last: number | null
        gammaBid: number | null
        gammaAsk: number | null
        in_review: boolean
        market_closed: boolean
      }
      const stubs: Stub[] = []
      let anyInReview = false
      const closedFlag = Boolean(ev?.closed)
      for (const m of (ev?.markets ?? []) as Record<string, unknown>[]) {
        const q = (m.question as string) ?? ''
        const label = parseBracketLabel(q)
        if (!label) continue
        const op = parseOutcomePrices(m.outcomePrices)
        const yes = op.length >= 1 && Number.isFinite(op[0]) ? op[0] : null
        const no  = op.length >= 2 && Number.isFinite(op[1]) ? op[1]
                  : (yes != null ? 1 - yes : null)
        const gBid = m.bestBid != null ? Number(m.bestBid) : null
        const gAsk = m.bestAsk != null ? Number(m.bestAsk) : null
        const statuses = parseStatuses(m.umaResolutionStatuses)
        const inReview = statuses.some((s) => s === 'proposed' || s === 'disputed')
        if (inReview) anyInReview = true
        const tids = parseTokenIds(m.clobTokenIds)
        stubs.push({
          label,
          condition_id: (m.conditionId as string) ?? '',
          yes_token: tids[0] ?? null,
          yes_last: yes,
          no_last: no,
          gammaBid: gBid != null && Number.isFinite(gBid) ? gBid : null,
          gammaAsk: gAsk != null && Number.isFinite(gAsk) ? gAsk : null,
          in_review: inReview,
          market_closed: Boolean(m.closed),
        })
      }

      // 2) CLOB /books — true live bid/ask for all 11 brackets in one POST.
      // Fall back to gamma's cached values if this fails (e.g. CORS,
      // network blip).
      const tokens = stubs.map((s) => s.yes_token).filter((t): t is string => !!t)
      const livePrices = new Map<string, { bestBid: number | null; bestAsk: number | null }>()
      try {
        const resp = await fetch('https://clob.polymarket.com/books', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(tokens.map((id) => ({ token_id: id }))),
        })
        if (resp.ok) {
          const books = await resp.json() as { asset_id: string; asks?: { price: string }[]; bids?: { price: string }[] }[]
          for (const b of books) {
            livePrices.set(b.asset_id, bestFromBook(b))
          }
        }
      } catch { /* fall back to gamma values */ }

      // 3) Merge
      const next: Record<string, LiveBracketTick> = {}
      for (const s of stubs) {
        const live = s.yes_token ? livePrices.get(s.yes_token) : undefined
        const bid = live?.bestBid ?? s.gammaBid
        const ask = live?.bestAsk ?? s.gammaAsk
        const spread = (bid != null && ask != null && bid > 0 && ask > 0)
          ? (ask - bid) / ((ask + bid) / 2) : null
        next[s.label] = {
          bracket_label: s.label,
          condition_id: s.condition_id,
          yes_token_id: s.yes_token,
          yes_price: s.yes_last,
          no_price: s.no_last,
          best_bid: bid,
          best_ask: ask,
          spread_pct: spread,
          in_review: s.in_review,
          market_closed: s.market_closed,
        }
      }
      setByBracket(next)
      setEventInReview(anyInReview)
      setEventClosed(closedFlag)
      setError(null)
      setLastFetched(new Date())
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
    }
  }, [city, forecastDate])

  useEffect(() => {
    fetchEvent()
    const id = setInterval(fetchEvent, POLL_MS)
    return () => clearInterval(id)
  }, [fetchEvent])

  return { byBracket, eventInReview, eventClosed, lastFetched, error }
}
