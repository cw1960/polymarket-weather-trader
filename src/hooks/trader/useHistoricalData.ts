// Hooks reading the backfilled historical_* tables that the
// scripts/backfill_history.py cron populates nightly.
//
//   • useHistoricalEvents     — list of resolved (city, date) events,
//                               with winning bracket + day-max temp
//   • useHistoricalEvent      — full bracket-price series + WU hourly
//                               obs for one (city, date)
//
// These two together are enough to backtest a strategy: at each tick we
// know all bracket prices, the current observed temp, and the eventual
// resolution.

import { useCallback, useEffect, useState } from 'react'
import supabase from '../../lib/supabase'

export interface HistoricalEvent {
  city: string
  forecast_date: string                  // 'YYYY-MM-DD'
  winning_bracket_label: string | null
  day_max_temp_c: number | null
  day_max_temp_f: number | null
  day_max_local_hour: number | null
}

export interface HistoricalBracketPoint {
  ms: number                             // unix ms
  yes_price: number
}

export interface HistoricalBracket {
  bracket_label: string
  bracket_unit: 'F' | 'C'
  bracket_low_native: number | null
  bracket_high_native: number | null
  condition_id: string
  points: HistoricalBracketPoint[]       // ASC by time
}

export interface HistoricalObservation {
  ms: number
  temp_f: number
  temp_c: number
}

export interface HistoricalEventDetail {
  event: HistoricalEvent | null
  brackets: HistoricalBracket[]
  observations: HistoricalObservation[]
}


/** List all resolved events, optionally restricted by city. */
export function useHistoricalEvents(cityFilter: string | null = null) {
  const [events, setEvents] = useState<HistoricalEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  const fetchEvents = useCallback(async () => {
    try {
      let q = supabase
        .from('historical_event_resolutions')
        .select('city, forecast_date, winning_bracket_label, day_max_temp_c, day_max_temp_f, day_max_local_hour')
        .order('forecast_date', { ascending: false })
        .order('city', { ascending: true })
        .limit(5000)
      if (cityFilter) q = q.eq('city', cityFilter)
      const { data, error: e } = await q
      if (e) { setError(e.message); setLoading(false); return }
      setEvents((data ?? []) as HistoricalEvent[])
      setError(null)
      setLastRefreshed(new Date())
      setLoading(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setLoading(false)
    }
  }, [cityFilter])

  useEffect(() => {
    setLoading(true)
    fetchEvents()
  }, [fetchEvents])

  return { events, loading, error, lastRefreshed, refresh: fetchEvents }
}


/** Full price + obs for one resolved event. */
export function useHistoricalEvent(
  city: string | null,
  forecastDate: string | null,
): { data: HistoricalEventDetail; loading: boolean; error: string | null } {
  const [data, setData] = useState<HistoricalEventDetail>({ event: null, brackets: [], observations: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchAll = useCallback(async () => {
    if (!city || !forecastDate) {
      setData({ event: null, brackets: [], observations: [] })
      setLoading(false)
      return
    }
    try {
      // Resolution row
      const { data: resRow, error: e1 } = await supabase
        .from('historical_event_resolutions')
        .select('city, forecast_date, winning_bracket_label, day_max_temp_c, day_max_temp_f, day_max_local_hour')
        .eq('city', city).eq('forecast_date', forecastDate)
        .maybeSingle()
      if (e1) { setError(e1.message); setLoading(false); return }

      // Bracket prices (could be many thousands — page if needed; usually
      // ≤ 3500 rows for the full event so we can fit in one request after
      // we narrow by (city, date)).
      const { data: priceRows, error: e2 } = await supabase
        .from('historical_bracket_prices')
        .select('bracket_label, bracket_unit, bracket_low_native, bracket_high_native, condition_id, recorded_at, yes_price')
        .eq('city', city).eq('forecast_date', forecastDate)
        .order('recorded_at', { ascending: true })
        .limit(20000)
      if (e2) { setError(e2.message); setLoading(false); return }

      // Hourly obs — the day of, plus a buffer (some markets resolve on
      // a date but the WU observation is a few hours into the next day).
      // We just pull observations within the day's UTC bounds; backtest
      // can be more precise if needed.
      const dayStart = new Date(forecastDate + 'T00:00:00Z')
      const dayEnd = new Date(dayStart.getTime() + 36 * 3600 * 1000)
      const { data: obsRows, error: e3 } = await supabase
        .from('historical_temp_observations')
        .select('observed_at, temp_f, temp_c')
        .eq('city', city)
        .gte('observed_at', dayStart.toISOString())
        .lte('observed_at', dayEnd.toISOString())
        .order('observed_at', { ascending: true })
        .limit(200)
      if (e3) { setError(e3.message); setLoading(false); return }

      // Group prices by bracket
      type Row = NonNullable<typeof priceRows>[number]
      const byBracket = new Map<string, Row[]>()
      for (const r of priceRows ?? []) {
        const lbl = r.bracket_label as string
        if (!byBracket.has(lbl)) byBracket.set(lbl, [])
        byBracket.get(lbl)!.push(r as Row)
      }
      const brackets: HistoricalBracket[] = []
      for (const [lbl, rows] of byBracket) {
        const first = rows[0]
        brackets.push({
          bracket_label: lbl,
          bracket_unit: ((first.bracket_unit as string) || 'C') as 'F' | 'C',
          bracket_low_native: first.bracket_low_native as number | null,
          bracket_high_native: first.bracket_high_native as number | null,
          condition_id: first.condition_id as string,
          points: rows.map((r) => ({
            ms: new Date(r.recorded_at as string).getTime(),
            yes_price: r.yes_price as number,
          })),
        })
      }
      brackets.sort((a, b) => (a.bracket_low_native ?? 0) - (b.bracket_low_native ?? 0))

      setData({
        event: resRow as HistoricalEvent | null,
        brackets,
        observations: (obsRows ?? []).map((o) => ({
          ms: new Date(o.observed_at as string).getTime(),
          temp_f: o.temp_f as number,
          temp_c: o.temp_c as number,
        })),
      })
      setError(null)
      setLoading(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setLoading(false)
    }
  }, [city, forecastDate])

  useEffect(() => {
    setLoading(true)
    fetchAll()
  }, [fetchAll])

  return { data, loading, error }
}


/** Lightweight summary stats — count of events + earliest/latest date. */
export function useHistoricalDataStatus() {
  const [stats, setStats] = useState<{
    eventCount: number; earliest: string | null; latest: string | null;
    priceRowCount: number | null; obsRowCount: number | null;
    loading: boolean; error: string | null;
  }>({
    eventCount: 0, earliest: null, latest: null,
    priceRowCount: null, obsRowCount: null, loading: true, error: null,
  })

  const fetchStatus = useCallback(async () => {
    try {
      // CHEAP version of the status panel: we used to do
      //   .select('*', { count: 'exact', head: true })
      // on two growing tables every 30s, which on a 1M-row table causes a
      // full table scan and burns Supabase disk-IO budget very quickly.
      // We now just count the resolutions table (small — one row per event)
      // and DERIVE approximate price-row count from `eventCount × 1500`.
      // The user only needs a rough number to see "is data growing?".
      const { data: events, error: e1 } = await supabase
        .from('historical_event_resolutions')
        .select('forecast_date')
        .order('forecast_date', { ascending: false })
        .limit(5000)
      if (e1) { setStats((s) => ({ ...s, error: e1.message, loading: false })); return }
      const eventCount = events?.length ?? 0
      const latest = events?.[0]?.forecast_date as string | undefined
      const earliest = events?.[events.length - 1]?.forecast_date as string | undefined
      setStats({
        eventCount,
        earliest: earliest ?? null,
        latest: latest ?? null,
        priceRowCount: eventCount * 1500,     // rough estimate (avg ~1500 price points / event)
        obsRowCount:   eventCount * 30,       // rough estimate (~30 hourly obs / event)
        loading: false,
        error: null,
      })
    } catch (err) {
      setStats((s) => ({ ...s, error: err instanceof Error ? err.message : String(err), loading: false }))
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    // Refresh on a long interval. The user can open/close the Backtest
    // tab to force a fresh check; we don't need second-by-second updates.
    const id = setInterval(fetchStatus, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [fetchStatus])

  return stats
}
