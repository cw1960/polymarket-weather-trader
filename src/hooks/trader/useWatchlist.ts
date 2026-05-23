// Trader Watchlist hook — pulls a tile per (city, forecast_date) with
// markets resolving within the next 6 hours, plus the latest bracket prices,
// sparkline data, and weather context.
//
// Powered by the bracket_price_history table populated by the
// trader_price_collector cron.

import { useState, useEffect, useCallback } from 'react'
import supabase from '../../lib/supabase'

export interface WatchlistBracket {
  bracket_label: string
  yes_price: number | null
  no_price: number | null
  spread_pct: number | null
  bracket_low_native: number | null
  bracket_high_native: number | null
}

export interface WatchlistTile {
  city: string
  forecast_date: string
  unit: 'F' | 'C'
  brackets: WatchlistBracket[]
  observed_temp_c: number | null
  observed_running_max_c: number | null
  local_hour: number | null
  time_to_resolution_minutes: number | null
  // Live local hour, derived from TTR (NOT from temp_readings, which lags).
  current_local_hour: number | null
  // The bracket with the highest YES price right now (= market's pick for winner)
  market_favorite_label: string | null
  market_favorite_yes_price: number | null
  // 30-minute mini-series of the favorite bracket's price (sparkline)
  favorite_sparkline: { t: string; p: number }[]
  last_recorded_at: string | null
}

export interface WatchlistData {
  tiles: WatchlistTile[]
  loading: boolean
  lastRefreshed: Date | null
  error: string | null
}

const SPARKLINE_MIN = 30                // minutes of price history for the sparkline
// Tradeable window in current LOCAL hour at the city. After 5pm the day's
// high is effectively locked and the market converges to 100¢ on the
// winning bracket — nothing to trade. Before 10am the market hasn't
// reacted to the day yet and is mostly forecast-driven (less day-trader
// edge). 10:00–16:59 local is the sweet spot.
const LOCAL_HOUR_MIN = 10
const LOCAL_HOUR_MAX = 17

// The collector sets time_to_resolution_minutes = (end_of_local_day - now) + 2h buffer.
//   end_of_local_day = midnight local
//   hours_until_end_of_day = (TTR_min - 120) / 60
//   current_local_hour ≈ 24 - hours_until_end_of_day = 26 - TTR_min/60
// If the value comes out > 23.999 the local day has already rolled over
// (market in finalization buffer) → not tradeable. Negative or > 26 also
// non-tradeable. Returns null if TTR is missing.
function currentLocalHour(ttrMinutes: number | null): number | null {
  if (ttrMinutes == null) return null
  const lh = 26 - ttrMinutes / 60
  if (lh < 0 || lh >= 24) return null
  return lh
}


function nativeTemp(c: number | null, unit: 'F' | 'C'): string {
  if (c == null) return '—'
  if (unit === 'F') return `${Math.round(c * 9 / 5 + 32)}°F`
  return `${Math.round(c)}°C`
}

export function fmtTemp(c: number | null, unit: 'F' | 'C') {
  return nativeTemp(c, unit)
}


export function useWatchlist(): WatchlistData & { refresh: () => void } {
  const [tiles, setTiles] = useState<WatchlistTile[]>([])
  const [loading, setLoading] = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetch = useCallback(async () => {
    try {
      // Pull every bracket_price_history row from the last 35 minutes
      // (enough for a 30-min sparkline + recent snapshot).
      //
      // IMPORTANT: PostgREST caps responses at 1000 rows by default. With ~11
      // brackets per market and ~90 active markets, a single cron tick writes
      // ~1500 rows — already over the cap. We work around this by:
      //   (1) filtering SERVER-SIDE on TTR window matching the local-hour
      //       trading window [LOCAL_HOUR_MIN, LOCAL_HOUR_MAX) and on
      //       market_closed = false, and
      //   (2) ordering DESCENDING so the freshest snapshot for each bracket
      //       lands in the first 1000 rows even if the cap kicks in.
      //
      // TTR range derivation: current_local_hour = 26 - TTR/60. So
      //   local_hour < LOCAL_HOUR_MAX  ⇔  TTR > (26 - LOCAL_HOUR_MAX) * 60
      //   local_hour >= LOCAL_HOUR_MIN ⇔  TTR <= (26 - LOCAL_HOUR_MIN) * 60
      const ttrMinExclusive = (26 - LOCAL_HOUR_MAX) * 60      // e.g. 540 = 9h
      const ttrMaxInclusive = (26 - LOCAL_HOUR_MIN) * 60      // e.g. 960 = 16h
      const cutoff = new Date(Date.now() - 35 * 60 * 1000).toISOString()
      const { data, error: e } = await supabase
        .from('bracket_price_history')
        .select('recorded_at, city, forecast_date, bracket_label, bracket_unit, bracket_low_native, bracket_high_native, yes_price, no_price, spread_pct, observed_temp_c, observed_running_max_c, local_hour, time_to_resolution_minutes, market_closed')
        .gte('recorded_at', cutoff)
        .gt('time_to_resolution_minutes', ttrMinExclusive)
        .lte('time_to_resolution_minutes', ttrMaxInclusive)
        .eq('market_closed', false)
        .order('recorded_at', { ascending: false })
        .limit(5000)
      if (e) { setError(e.message); setLoading(false); return }

      // Group by (city, forecast_date)
      type Row = NonNullable<typeof data>[number]
      const groups = new Map<string, Row[]>()
      for (const r of data ?? []) {
        const key = `${r.city}|${r.forecast_date}`
        if (!groups.has(key)) groups.set(key, [])
        groups.get(key)!.push(r as Row)
      }

      const out: WatchlistTile[] = []
      for (const [key, rows] of groups) {
        const [city, forecast_date] = key.split('|')
        // Rows are in DESCENDING time order → most recent is rows[0].
        const last = rows[0]
        if (!last) continue
        // (closed / out-of-window already filtered server-side, but keep defensive checks)
        if (last.market_closed) continue
        if (last.time_to_resolution_minutes == null) continue
        const lhNow = currentLocalHour(last.time_to_resolution_minutes)
        if (lhNow == null) continue
        if (lhNow < LOCAL_HOUR_MIN || lhNow >= LOCAL_HOUR_MAX) continue

        // Latest snapshot per bracket — since rows are DESC, the FIRST occurrence
        // of each bracket_label is the freshest one.
        const latestByBracket = new Map<string, Row>()
        for (const r of rows) {
          if (!latestByBracket.has(r.bracket_label)) latestByBracket.set(r.bracket_label, r)
        }
        const brackets: WatchlistBracket[] = [...latestByBracket.values()].map((r) => ({
          bracket_label: r.bracket_label,
          yes_price: r.yes_price as number | null,
          no_price:  r.no_price  as number | null,
          spread_pct: r.spread_pct as number | null,
          bracket_low_native: r.bracket_low_native as number | null,
          bracket_high_native: r.bracket_high_native as number | null,
        }))
        // Sort brackets by bracket_low_native (low → high)
        brackets.sort((a, b) => (a.bracket_low_native ?? 0) - (b.bracket_low_native ?? 0))

        // Market favorite = bracket with highest YES price
        let favLabel: string | null = null
        let favYes = 0
        for (const b of brackets) {
          if (b.yes_price != null && b.yes_price > favYes) { favYes = b.yes_price; favLabel = b.bracket_label }
        }

        // Sparkline: favorite bracket's YES price over the last SPARKLINE_MIN
        const sparkCutoff = Date.now() - SPARKLINE_MIN * 60 * 1000
        const favSpark = favLabel
          ? rows.filter(r => r.bracket_label === favLabel && new Date(r.recorded_at).getTime() >= sparkCutoff)
                .map(r => ({ t: r.recorded_at, p: (r.yes_price as number | null) ?? 0 }))
                .sort((a, b) => new Date(a.t).getTime() - new Date(b.t).getTime())    // chart needs ascending
          : []

        out.push({
          city,
          forecast_date,
          unit: (last.bracket_unit as 'F' | 'C') || 'C',
          brackets,
          observed_temp_c: last.observed_temp_c as number | null,
          observed_running_max_c: last.observed_running_max_c as number | null,
          local_hour: last.local_hour as number | null,
          current_local_hour: lhNow,
          time_to_resolution_minutes: last.time_to_resolution_minutes,
          market_favorite_label: favLabel,
          market_favorite_yes_price: favLabel ? favYes : null,
          favorite_sparkline: favSpark,
          last_recorded_at: last.recorded_at,
        })
      }
      // Sort tiles by time-to-resolution ascending (most urgent first)
      out.sort((a, b) => (a.time_to_resolution_minutes ?? 9999) - (b.time_to_resolution_minutes ?? 9999))
      setTiles(out)
      setLastRefreshed(new Date())
      setError(null)
      setLoading(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, 15_000)    // refresh every 15s (Supabase IO budget)
    return () => clearInterval(id)
  }, [fetch])

  return { tiles, loading, lastRefreshed, error, refresh: fetch }
}
