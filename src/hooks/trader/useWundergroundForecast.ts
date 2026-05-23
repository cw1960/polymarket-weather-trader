// Wunderground hourly forecast for one (city, day).
//
// Why this exists (and why not Open-Meteo):
//   Polymarket weather markets resolve from Wunderground's daily-history
//   page. The bot's Open-Meteo forecasts had a documented bias problem —
//   see scripts/forecast_bias.py and migrate_forecast_bias_corrections.sql.
//   Pulling Wunderground's OWN forecast means we're looking at the same
//   model output Wunderground will eventually grade the day against,
//   which is what the market participants implicitly trade off.
//
// Endpoint: api.weather.com/v3/wx/forecast/hourly/2day — same one the
// wunderground.com hourly-forecast page calls client-side. Unauthenticated
// public key, CORS-open, same one we already use for observations.

import { useCallback, useEffect, useState } from 'react'
import { CITY_COORDS } from '../../lib/cityCoords'
import { WU_APIKEY } from '../../lib/wundergroundStations'

export interface WuForecastPoint {
  ms: number             // unix ms (UTC)
  tempF: number          // ALWAYS Fahrenheit — chart converts to display unit.
                         // (WU's /v3/wx/forecast/hourly/2day ignores units=m on
                         // some responses, so we always fetch in F to dodge
                         // unit-mismatch bugs.)
}

export interface WuForecastData {
  forecast: WuForecastPoint[]
  loading: boolean
  error: string | null
  lastFetched: Date | null
  coordsAvailable: boolean
}

const REFRESH_MS = 5 * 60 * 1000


export function useWundergroundForecast(
  city: string | null,
): WuForecastData {
  const [forecast, setForecast] = useState<WuForecastPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)
  const coords = city ? CITY_COORDS[city] : undefined

  const fetchForecast = useCallback(async () => {
    if (!city || !coords) {
      setForecast([])
      setLoading(false)
      return
    }
    // Wunderground's hourly forecast endpoint. Always request units=e
    // (Fahrenheit) — empirically the response uses Imperial values even
    // when units=m is requested for some city/region combinations. The
    // chart converts F→display-unit, same as for observations.
    const url =
      `https://api.weather.com/v3/wx/forecast/hourly/2day` +
      `?geocode=${coords.lat},${coords.lon}` +
      `&format=json&units=e&language=en-US` +
      `&apiKey=${WU_APIKEY}`
    try {
      const r = await fetch(url)
      if (!r.ok) {
        setError(`Wunderground forecast HTTP ${r.status}`)
        setLoading(false)
        return
      }
      const json = await r.json()
      const times: number[] = json?.validTimeUtc ?? []   // unix seconds
      const temps: (number | null)[] = json?.temperature ?? []
      const out: WuForecastPoint[] = []
      for (let i = 0; i < times.length; i++) {
        const t = times[i], v = temps[i]
        if (!Number.isFinite(t) || v == null || !Number.isFinite(v)) continue
        out.push({ ms: t * 1000, tempF: v })
      }
      out.sort((a, b) => a.ms - b.ms)
      setForecast(out)
      setError(null)
      setLastFetched(new Date())
      setLoading(false)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setLoading(false)
    }
  }, [city, coords])

  useEffect(() => {
    setLoading(true)
    fetchForecast()
    const id = setInterval(fetchForecast, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchForecast])

  return {
    forecast,
    loading,
    error,
    lastFetched,
    coordsAvailable: Boolean(coords),
  }
}
