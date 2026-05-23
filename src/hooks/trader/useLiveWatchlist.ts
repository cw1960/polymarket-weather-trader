// useLiveWatchlist — Watchlist hook that pulls directly from Polymarket's
// gamma-api, with ZERO Supabase dependency.
//
// Why this exists (replaces useWatchlist):
//   The old hook read from bracket_price_history, which was populated by
//   a 4×/min VPS collector. That collector saturated free-tier disk IO and
//   eventually took the DB offline (see triage thread, 2026-05-22). The
//   live data we actually need — current prices, bids/asks, resolution
//   status — is available from gamma directly, costs us ~2 HTTP requests
//   per 30s refresh, and has zero load on our own backend.
//
// What we lose vs the Supabase version:
//   • Observed temperature ("Now") and running daily-max ("Day max") on
//     each tile — those came from a different bot table (temp_readings).
//     The Trader can see live temp in the Trade Station's WU chart.
//   • Per-tile sparkline of the favorite bracket — would need a CLOB call
//     per tile (~15 extra requests). Easy to add later; skipped in v1.
//
// What we gain:
//   • Sub-30s freshness on prices (was 5–22s real-world via collector).
//   • No write amplification on Supabase. Watchlist works even when the
//     collector / DB are down.

import { useCallback, useEffect, useState } from 'react'
import { POLYMARKET_CITY_SLUG, parseBracketLabel } from '../../lib/polymarketSlugs'
import { CITY_TIMEZONES, currentLocalHour, parseForecastDateFromSlug } from '../../lib/cityTimezones'
import type { WatchlistData, WatchlistTile, WatchlistBracket } from './useWatchlist'

// Invert the CITY_SLUG map (display → slug) for parsing event slugs back to display name.
const SLUG_TO_CITY: Record<string, string> = (() => {
  const out: Record<string, string> = {}
  for (const [display, slug] of Object.entries(POLYMARKET_CITY_SLUG)) out[slug] = display
  return out
})()

// Tradeability filter — match useWatchlist semantics.
const LOCAL_HOUR_MIN = 10
const LOCAL_HOUR_MAX = 17

const REFRESH_MS = 30_000

// gamma-api returns max 100 events per request. We fetch the first 3 pages
// in parallel, which more than covers the typical ~120 active weather events.
const GAMMA_PAGES = 3
const GAMMA_PAGE_SIZE = 100


function localDateAt(tz: string, now: Date): string {
  // Render the current date in the target tz as YYYY-MM-DD.
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now)
  const y = parts.find((p) => p.type === 'year')?.value
  const m = parts.find((p) => p.type === 'month')?.value
  const d = parts.find((p) => p.type === 'day')?.value
  return `${y}-${m}-${d}`
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


export function useLiveWatchlist(): WatchlistData & { refresh: () => void } {
  const [tiles, setTiles] = useState<WatchlistTile[]>([])
  const [loading, setLoading] = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const now = new Date()
      // Fetch N pages in parallel.
      const pageUrls = Array.from({ length: GAMMA_PAGES }, (_, i) =>
        `https://gamma-api.polymarket.com/events?tag_slug=weather&closed=false&limit=${GAMMA_PAGE_SIZE}&offset=${i * GAMMA_PAGE_SIZE}`,
      )
      const resps = await Promise.all(pageUrls.map((u) => fetch(u)))
      for (const r of resps) {
        if (!r.ok) { setError(`gamma HTTP ${r.status}`); setLoading(false); return }
      }
      const pages = await Promise.all(resps.map((r) => r.json()))
      // Combine + dedupe by event id
      const seen = new Set<string>()
      const allEvents: Record<string, unknown>[] = []
      for (const page of pages) {
        for (const ev of (page as Record<string, unknown>[]) ?? []) {
          const id = String(ev.id ?? ev.slug ?? '')
          if (!id || seen.has(id)) continue
          seen.add(id)
          allEvents.push(ev)
        }
      }

      const out: WatchlistTile[] = []
      for (const ev of allEvents) {
        const slug = (ev.slug as string) ?? ''
        if (!slug.startsWith('highest-temperature-in-')) continue
        const forecastDate = parseForecastDateFromSlug(slug)
        if (!forecastDate) continue

        // Resolve the slug fragment back to a display city. If we don't
        // know this city (e.g. a Polymarket city we haven't mapped), skip.
        const slugFragMatch = slug.match(/^highest-temperature-in-(.+?)-on-/)
        const slugFrag = slugFragMatch?.[1] ?? ''
        const city = SLUG_TO_CITY[slugFrag]
        if (!city) continue

        const tz = CITY_TIMEZONES[city]
        if (!tz) continue

        // Tradeability filter #1: must be the city's current local date.
        const cityToday = localDateAt(tz, now)
        if (forecastDate !== cityToday) continue

        // Tradeability filter #2: city's current local hour ∈ [10, 17).
        const lhNow = currentLocalHour(city, now)
        if (lhNow == null || lhNow < LOCAL_HOUR_MIN || lhNow >= LOCAL_HOUR_MAX) continue

        // Build per-bracket data from the event's markets.
        const brackets: WatchlistBracket[] = []
        let unit: 'F' | 'C' = 'F'
        let anyClosed = false
        for (const m of (ev.markets as Record<string, unknown>[]) ?? []) {
          const q = (m.question as string) ?? ''
          const parsed = parseBracketLabel(q)
          if (!parsed) continue
          // parsed is e.g. "86-87°F" — extract unit from last char
          const u = parsed.endsWith('°F') ? 'F' : 'C'
          unit = u
          const op = parseOutcomePrices(m.outcomePrices)
          const yes = op.length >= 1 && Number.isFinite(op[0]) ? op[0] : null
          const no  = op.length >= 2 && Number.isFinite(op[1]) ? op[1]
                    : (yes != null ? 1 - yes : null)
          const bid = m.bestBid != null ? Number(m.bestBid) : null
          const ask = m.bestAsk != null ? Number(m.bestAsk) : null
          const spread = (bid != null && ask != null && bid > 0 && ask > 0)
            ? (ask - bid) / ((ask + bid) / 2) : null
          brackets.push({
            bracket_label: parsed,
            yes_price: yes,
            no_price: no,
            spread_pct: spread,
            bracket_low_native: null,        // not provided by gamma directly; chart doesn't need it for the Watchlist tile
            bracket_high_native: null,
          })
          if (m.closed) anyClosed = true
        }
        if (brackets.length === 0) continue
        if (anyClosed) continue              // event has at least one bracket already resolved — skip

        // Sort brackets by parsing the leading temperature out of the label
        // so the favorite computation reads from the highest YES price across
        // the typical lo→hi order.
        brackets.sort((a, b) => {
          const av = Number(a.bracket_label.match(/-?\d+/)?.[0] ?? 0)
          const bv = Number(b.bracket_label.match(/-?\d+/)?.[0] ?? 0)
          return av - bv
        })

        // Market favorite = bracket with the highest YES price
        let favLabel: string | null = null
        let favYes = 0
        for (const b of brackets) {
          if (b.yes_price != null && b.yes_price > favYes) {
            favYes = b.yes_price; favLabel = b.bracket_label
          }
        }

        // TTR — derive from event endDate if present
        let ttrMin: number | null = null
        const endDateRaw = ev.endDate as string | undefined
        if (endDateRaw) {
          const endMs = new Date(endDateRaw).getTime()
          if (Number.isFinite(endMs)) {
            ttrMin = Math.floor((endMs - now.getTime()) / 60_000)
          }
        }

        out.push({
          city,
          forecast_date: forecastDate,
          unit,
          brackets,
          observed_temp_c: null,           // dropped — see hook header
          observed_running_max_c: null,    // dropped — see hook header
          local_hour: lhNow,               // (compat field; same as current_local_hour now)
          current_local_hour: lhNow,
          time_to_resolution_minutes: ttrMin,
          market_favorite_label: favLabel,
          market_favorite_yes_price: favLabel ? favYes : null,
          favorite_sparkline: [],          // skipped in v1 — see hook header
          last_recorded_at: now.toISOString(),
        })
      }

      // Sort by TTR ascending (most urgent first)
      out.sort((a, b) => (a.time_to_resolution_minutes ?? 9999) - (b.time_to_resolution_minutes ?? 9999))

      setTiles(out)
      setError(null)
      setLastRefreshed(new Date())
      setLoading(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchAll])

  return { tiles, loading, lastRefreshed, error, refresh: fetchAll }
}
