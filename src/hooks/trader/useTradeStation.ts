// useTradeStation — price data for a single (city, forecast_date) market.
//
// HISTORY of this hook's data source:
//   v1: Read from bracket_price_history (Supabase). 4×/min VPS collector
//       wrote rows; hook polled Supabase every 5s. This saturated free-tier
//       disk IO and took the DB offline on 2026-05-22.
//   v2: Read from Polymarket gamma + CLOB directly. Zero Supabase load.
//       • Gamma snapshot (every refresh) gives us each bracket's current
//         price, bid/ask, condition_id, and YES tokenId.
//       • CLOB prices-history for the FOCUSED bracket gives us minute-level
//         12h history without hitting our backend.
//       Same returned interface as v1 so callers don't need to change.
//
// Refresh: 30s. Live ticks come from useLivePolymarketEvent (2s polling)
// which overlays current snapshot on top of this hook's historical line.
//
// Per-event traffic: 1 gamma call + 1 CLOB call per refresh = 2 req / 30s.
// 144 cities don't multiply because we're only watching one event at a time.

import { useState, useEffect, useCallback } from 'react'
import { buildEventSlug, parseBracketFull } from '../../lib/polymarketSlugs'
import { CITY_TIMEZONES, currentLocalHour, estimateResolutionUtcMs } from '../../lib/cityTimezones'

export interface TradePoint {
  recorded_at: string
  yes_price: number | null
  no_price: number | null
  best_bid: number | null
  best_ask: number | null
  spread_pct: number | null
  observed_temp_c: number | null            // null in v2 (was from temp_readings)
  observed_running_max_c: number | null     // null in v2
  local_hour: number | null
  time_to_resolution_minutes: number | null
  market_closed: boolean
}

export interface BracketSeries {
  bracket_label: string
  bracket_unit: 'F' | 'C'
  bracket_low_native: number | null
  bracket_high_native: number | null
  points: TradePoint[]                       // ordered ASCENDING by recorded_at
}

export interface TradeStationData {
  brackets: BracketSeries[]                  // ordered low → high
  loading: boolean
  error: string | null
  lastRefreshed: Date | null
  latestObservedTempC: number | null         // always null in v2; kept for UI compat
  latestRunningMaxC: number | null           // always null in v2
  latestLocalHour: number | null             // derived from CITY_TIMEZONES
  latestTtrMinutes: number | null            // derived from gamma endDate
  marketClosed: boolean
}

const HISTORY_HOURS = 12
const REFRESH_MS = 30_000


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


function finiteOrNull(n: number): number | null {
  return Number.isFinite(n) ? n : null
}


export function useTradeStation(
  city: string | null,
  forecastDate: string | null,
  focusBracketLabel: string | null = null,
): TradeStationData & { refresh: () => void } {
  const [brackets, setBrackets] = useState<BracketSeries[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const [latestLocalHour, setLatestLocalHour] = useState<number | null>(null)
  const [latestTtrMinutes, setLatestTtrMinutes] = useState<number | null>(null)
  const [marketClosed, setMarketClosed] = useState<boolean>(false)

  const fetchData = useCallback(async () => {
    if (!city || !forecastDate) {
      setBrackets([])
      setLoading(false)
      return
    }
    const slug = buildEventSlug(city, forecastDate)
    if (!slug) {
      setError(`no slug for ${city} ${forecastDate}`)
      setLoading(false)
      return
    }
    try {
      // 1) Gamma — current snapshot of every bracket
      const evResp = await fetch(`https://gamma-api.polymarket.com/events/slug/${slug}`)
      if (!evResp.ok) { setError(`gamma HTTP ${evResp.status}`); setLoading(false); return }
      const ev = await evResp.json()

      // Derive TTR + closed.
      //
      // IMPORTANT: gamma's event.endDate is NOT the actual resolution time
      // for weather markets — it's set to 12:00 UTC of the forecast calendar
      // date, which is hours-to-a-day before the market actually resolves.
      // The real resolution happens when Wunderground publishes the daily
      // high for the city's local date (≈ end-of-city-local-day + 2h).
      // We estimate it ourselves; same formula the Python collector used.
      const now = new Date()
      const tz = CITY_TIMEZONES[city]
      const resolutionMs = tz && forecastDate
        ? estimateResolutionUtcMs(tz, forecastDate)
        : NaN
      const ttrMin = Number.isFinite(resolutionMs) ? Math.floor((resolutionMs - now.getTime()) / 60_000) : null
      const eventClosed = Boolean(ev?.closed)
      const localHour = tz ? currentLocalHour(city, now) : null

      // Build per-bracket entries
      interface MarketStub {
        label: string
        unit: 'F' | 'C'
        low: number
        high: number
        condition_id: string
        yes_token: string | null
        yes_price: number | null
        no_price: number | null
        best_bid: number | null
        best_ask: number | null
        spread_pct: number | null
        market_closed: boolean
      }
      const stubs: MarketStub[] = []
      for (const m of (ev?.markets as Record<string, unknown>[]) ?? []) {
        const q = (m.question as string) ?? ''
        const f = parseBracketFull(q)
        if (!f) continue
        const op = parseOutcomePrices(m.outcomePrices)
        const yes = op.length >= 1 && Number.isFinite(op[0]) ? op[0] : null
        const no  = op.length >= 2 && Number.isFinite(op[1]) ? op[1]
                  : (yes != null ? 1 - yes : null)
        const bid = m.bestBid != null ? Number(m.bestBid) : null
        const ask = m.bestAsk != null ? Number(m.bestAsk) : null
        const spread = (bid != null && ask != null && bid > 0 && ask > 0)
          ? (ask - bid) / ((ask + bid) / 2) : null
        let tokenIds: unknown = m.clobTokenIds
        if (typeof tokenIds === 'string') {
          try { tokenIds = JSON.parse(tokenIds) } catch { tokenIds = [] }
        }
        const yesTok = Array.isArray(tokenIds) && tokenIds.length > 0 ? String(tokenIds[0]) : null
        stubs.push({
          label: f.label,
          unit: f.unit,
          low: f.lowNative,
          high: f.highNative,
          condition_id: (m.conditionId as string) ?? '',
          yes_token: yesTok,
          yes_price: yes,
          no_price: no,
          best_bid: bid != null && Number.isFinite(bid) ? bid : null,
          best_ask: ask != null && Number.isFinite(ask) ? ask : null,
          spread_pct: spread,
          market_closed: Boolean(m.closed),
        })
      }
      // Sort low → high
      stubs.sort((a, b) => a.low - b.low)

      // 2) Optional CLOB prices-history for the focused bracket (12h, 1-min fidelity)
      let focusedHistory: { ms: number; p: number }[] = []
      if (focusBracketLabel) {
        const focused = stubs.find((s) => s.label === focusBracketLabel)
        if (focused?.yes_token) {
          const histUrl = `https://clob.polymarket.com/prices-history?market=${focused.yes_token}&interval=1d&fidelity=1`
          try {
            const r = await fetch(histUrl)
            if (r.ok) {
              const j = await r.json()
              const hist = (j?.history as { t: number; p: number }[] | undefined) ?? []
              const cutoff = Date.now() - HISTORY_HOURS * 3600_000
              focusedHistory = hist
                .filter((h) => Number.isFinite(h.t) && Number.isFinite(h.p) && h.t * 1000 >= cutoff)
                .map((h) => ({ ms: h.t * 1000, p: h.p }))
                .sort((a, b) => a.ms - b.ms)
            }
          } catch { /* swallow; chart just shows live ticks then */ }
        }
      }

      // 3) Build BracketSeries[] in legacy shape
      const out: BracketSeries[] = stubs.map((s) => {
        const isFocused = s.label === focusBracketLabel
        // Build points array.
        //  • Focused bracket: minute-resolution CLOB history + a synthetic
        //    "latest snapshot" tick at now() if it differs from the last
        //    history point. Other tick-level overlay still comes from
        //    useLivePolymarketEvent in the chart.
        //  • Other brackets: single current snapshot (used only for the
        //    All-Brackets table; chart doesn't read non-focused points).
        const points: TradePoint[] = []
        if (isFocused && focusedHistory.length > 0) {
          for (const h of focusedHistory) {
            points.push({
              recorded_at: new Date(h.ms).toISOString(),
              yes_price: h.p,
              no_price: 1 - h.p,
              best_bid: null,
              best_ask: null,
              spread_pct: null,
              observed_temp_c: null,
              observed_running_max_c: null,
              local_hour: localHour,
              time_to_resolution_minutes: ttrMin,
              market_closed: s.market_closed,
            })
          }
        }
        // Always append a "now" snapshot from the gamma response so the
        // chart visually ends at current time even before useLivePolymarketEvent
        // posts its first 2s tick.
        points.push({
          recorded_at: now.toISOString(),
          yes_price: finiteOrNull(s.yes_price ?? NaN),
          no_price: finiteOrNull(s.no_price ?? NaN),
          best_bid: s.best_bid,
          best_ask: s.best_ask,
          spread_pct: s.spread_pct,
          observed_temp_c: null,
          observed_running_max_c: null,
          local_hour: localHour,
          time_to_resolution_minutes: ttrMin,
          market_closed: s.market_closed,
        })
        return {
          bracket_label: s.label,
          bracket_unit: s.unit,
          bracket_low_native: Number.isFinite(s.low) ? s.low : null,
          bracket_high_native: Number.isFinite(s.high) ? s.high : null,
          points,
        }
      })

      setBrackets(out)
      setLatestLocalHour(localHour)
      setLatestTtrMinutes(ttrMin)
      setMarketClosed(eventClosed)
      setError(null)
      setLastRefreshed(new Date())
      setLoading(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setLoading(false)
    }
  }, [city, forecastDate, focusBracketLabel])

  useEffect(() => {
    setLoading(true)
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchData])

  return {
    brackets,
    loading,
    error,
    lastRefreshed,
    latestObservedTempC: null,
    latestRunningMaxC: null,
    latestLocalHour,
    latestTtrMinutes,
    marketClosed,
    refresh: fetchData,
  }
}
