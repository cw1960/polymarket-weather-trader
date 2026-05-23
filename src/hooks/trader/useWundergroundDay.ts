// Pulls Wunderground's hourly observations for one (city, date) directly
// from the unauthenticated api.weather.com endpoint that wunderground.com
// itself uses client-side. Same data as the History chart on
// https://www.wunderground.com/history/daily/.../{ICAO}/date/YYYY-M-D.
//
// CORS: api.weather.com returns Access-Control-Allow-Origin: * for this
// endpoint (it's how the wunderground site fetches its own data from the
// browser). If that ever changes, swap this for a Netlify function proxy.
//
// Refresh cadence: Wunderground publishes hourly METAR observations at
// xx:53, but the xx:53 row often appears a few minutes late and gets
// retroactively corrected. We poll every 60s so the new hourly dot lands
// on the chart within 60s of being published.

import { useCallback, useEffect, useState } from 'react'
import { WU_STATIONS, WU_APIKEY } from '../../lib/wundergroundStations'

export interface WuObservation {
  valid_time_gmt: number       // unix seconds
  temp_f: number | null        // observed temperature, °F (Wunderground native)
  dewPt_f: number | null
  wspd_mph: number | null
  pressure: number | null      // mbar
}

export interface WuDayData {
  observations: WuObservation[]    // ordered ascending by time
  loading: boolean
  error: string | null
  lastFetched: Date | null
  stationAvailable: boolean
  icao: string | null
}

const REFRESH_MS = 60 * 1000          // 60 seconds


export function useWundergroundDay(city: string | null, dateYmd: string | null): WuDayData {
  const [observations, setObservations] = useState<WuObservation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)
  const station = city ? WU_STATIONS[city] : undefined

  const fetchObs = useCallback(async () => {
    if (!city || !dateYmd || !station) {
      setObservations([])
      setLoading(false)
      return
    }
    const yyyymmdd = dateYmd.split('-').join('')
    const url =
      `https://api.weather.com/v1/location/${station.icao}:9:${station.cc}/observations/historical.json` +
      `?apiKey=${WU_APIKEY}&units=e&startDate=${yyyymmdd}`
    try {
      const r = await fetch(url)
      if (!r.ok) {
        setError(`Wunderground HTTP ${r.status}`)
        setLoading(false)
        return
      }
      const json = await r.json()
      const obs = ((json.observations as unknown[]) ?? []).map((raw) => {
        const o = raw as Record<string, unknown>
        return {
          valid_time_gmt: (o.valid_time_gmt as number) ?? 0,
          temp_f: (o.temp as number | null) ?? null,                  // already °F because we passed units=e
          dewPt_f: (o.dewPt as number | null) ?? null,
          wspd_mph: (o.wspd as number | null) ?? null,
          pressure: (o.pressure as number | null) ?? null,
        } satisfies WuObservation
      })
      // Filter null-temp rows and sort ascending
      const clean = obs
        .filter((o) => o.temp_f != null && o.valid_time_gmt > 0)
        .sort((a, b) => a.valid_time_gmt - b.valid_time_gmt)
      setObservations(clean)
      setError(null)
      setLastFetched(new Date())
      setLoading(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setLoading(false)
    }
  }, [city, dateYmd, station])

  useEffect(() => {
    setLoading(true)
    fetchObs()
    const id = setInterval(fetchObs, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchObs])

  return {
    observations,
    loading,
    error,
    lastFetched,
    stationAvailable: Boolean(station),
    icao: station?.icao ?? null,
  }
}
