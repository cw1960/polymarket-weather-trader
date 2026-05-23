// Trade Station — the "single market focus" page.
//
// Layout (Phase 3 step 1: shell only):
//   ┌─────────────────────────────────────────────────────────────────┐
//   │ Header: city + date + countdown + close                          │
//   ├──────────────────────────────────┬──────────────────────────────┤
//   │ Price chart (per bracket)        │ Weather context              │
//   │  (placeholder — Phase 3 step 2)  │  - Now / Day max / Local h   │
//   │                                  │  - Resolution countdown       │
//   │                                  │                              │
//   ├──────────────────────────────────┤ Indicator panel              │
//   │ Cross-bracket table              │  (placeholder — Phase 3 #3)  │
//   │  (latest YES per bracket)        │                              │
//   ├──────────────────────────────────┴──────────────────────────────┤
//   │ Order entry (placeholder — Phase 3 step 5 / Phase 4)             │
//   └─────────────────────────────────────────────────────────────────┘
//
// This is intentionally just the layout + cross-bracket table + weather
// strip + countdown. The chart, the indicators, and the order entry
// each ship in the next steps.

import { useEffect, useMemo, useState } from 'react'
import { useTradeStation, type BracketSeries } from '../../hooks/trader/useTradeStation'
import { useWundergroundDay } from '../../hooks/trader/useWundergroundDay'
import { useWundergroundForecast } from '../../hooks/trader/useWundergroundForecast'
import { useLivePolymarketEvent } from '../../hooks/trader/useLivePolymarketEvent'
import { usePolymarketHistory, type PMInterval } from '../../hooks/trader/usePolymarketHistory'
import PolymarketHistoryChart from './PolymarketHistoryChart'
import WeatherChart from './WeatherChart'
import DirectionalSpreadPanel from './DirectionalSpreadPanel'
import { CITY_TIMEZONES } from '../../lib/cityTimezones'

interface Props {
  city: string
  forecastDate: string
  onBack: () => void
}


function nativeTempLabel(c: number | null, unit: 'F' | 'C'): string {
  if (c == null) return '—'
  if (unit === 'F') return `${Math.round(c * 9 / 5 + 32)}°F`
  return `${Math.round(c)}°C`
}


function formatTtr(min: number | null): string {
  if (min == null) return '—'
  const sign = min < 0 ? '-' : ''
  const abs = Math.abs(min)
  const h = Math.floor(abs / 60)
  const m = abs % 60
  if (h === 0) return `${sign}${m}m`
  return `${sign}${h}h${m.toString().padStart(2, '0')}m`
}


/** Pill showing the city's current local time. Ticks every 30s so it stays
 * accurate without burning a render per second. */
function CityNowBadge({ city, cityTz }: { city: string; cityTz: string }) {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(id)
  }, [])
  const timeStr = new Intl.DateTimeFormat('en-US', {
    timeZone: cityTz, hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(now)
  // Try to grab the zone abbreviation (EDT/EST/JST/etc.) via shortGeneric.
  // Some browsers won't give us a useful abbrev, in which case we fall back.
  let zoneAbbrev = ''
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: cityTz, timeZoneName: 'short',
    }).formatToParts(now)
    const tzPart = parts.find((p) => p.type === 'timeZoneName')?.value
    if (tzPart && !tzPart.includes('GMT')) zoneAbbrev = tzPart
  } catch { /* swallow */ }
  return (
    <div className="ml-4 px-3 py-1.5 rounded border border-cyan-900/60 bg-cyan-950/30">
      <div className="text-[10px] uppercase tracking-wider text-cyan-500 leading-tight">{city} now</div>
      <div className="text-lg font-mono text-cyan-200 leading-tight">
        {timeStr}{zoneAbbrev && <span className="ml-1 text-[10px] text-cyan-500 font-sans">{zoneAbbrev}</span>}
      </div>
    </div>
  )
}


function bracketLatest(b: BracketSeries) {
  return b.points[b.points.length - 1] ?? null
}


export default function TradeStation({ city, forecastDate, onBack }: Props) {
  const [selectedBracket, setSelectedBracket] = useState<string | null>(null)

  // First we fetch with no focus (just latest snapshots for all brackets) to
  // discover the market favorite. Once we know the favorite (or the user
  // clicks one), we pass it through and the hook fetches deep history for
  // that one bracket. This keeps each query well under PostgREST's 1000-row
  // cap.
  const {
    brackets, loading, error, lastRefreshed,
    latestLocalHour,
    latestTtrMinutes, marketClosed,
  } = useTradeStation(city, forecastDate, selectedBracket)

  // Default focus = market favorite (highest current YES price). When the
  // user hasn't picked a bracket yet, we auto-select the favorite so the
  // chart fills with its history.
  // IANA tz for whichever city we're viewing. Used by both charts to label
  // axes in city time (instead of the browser's local time).
  const cityTz = CITY_TIMEZONES[city] ?? 'UTC'

  const favoriteLabel = useMemo(() => {
    let bestLabel: string | null = null
    let bestYes = 0
    for (const b of brackets) {
      const last = bracketLatest(b)
      const yes = last?.yes_price ?? 0
      if (yes != null && yes > bestYes) { bestYes = yes; bestLabel = b.bracket_label }
    }
    return bestLabel
  }, [brackets])

  useEffect(() => {
    if (!selectedBracket && favoriteLabel) setSelectedBracket(favoriteLabel)
  }, [favoriteLabel, selectedBracket])

  const activeBracketLabel = selectedBracket ?? favoriteLabel
  const unit: 'F' | 'C' = (brackets[0]?.bracket_unit) ?? 'F'
  const urgent = (latestTtrMinutes ?? 9999) < 90

  // Live Wunderground hourly observations for this (city, date). Same
  // data that powers the History chart on wunderground.com.
  const wu = useWundergroundDay(city, forecastDate)
  // Wunderground hourly forecast — dashed overlay on the weather chart.
  // We use Wunderground's OWN forecast (not Open-Meteo) because Polymarket
  // resolves from Wunderground's daily history page, and the bot's
  // Open-Meteo forecasts had a documented bias problem
  // (see scripts/forecast_bias.py). This is the market's implicit benchmark.
  // Values come back in F regardless; the chart converts to display unit.
  const wuFc = useWundergroundForecast(city)

  // Live Polymarket gamma poll (2s). Bypasses the VPS collector for the
  // CURRENT snapshot — cuts price-display lag from ~22s to ~2s. The
  // historical chart still comes from Supabase.
  const live = useLivePolymarketEvent(city, forecastDate)

  // Polymarket multi-bracket price history — same data that powers the
  // chart on polymarket.com/event/.../highest-temp-... User can switch
  // 1h / 6h / 1d / 1w / 1m / max.
  const [pmInterval, setPmInterval] = useState<PMInterval>('1d')
  const pmHist = usePolymarketHistory(city, forecastDate, pmInterval)

  // Build bracket reference lines for the weather chart. We highlight the
  // currently-focused bracket so the user instantly sees how far the temp
  // has to travel to land in (or out of) it.
  const bracketLines = useMemo(() => {
    return brackets.map((b) => ({
      label: b.bracket_label,
      lowNative: b.bracket_low_native,
      highNative: b.bracket_high_native,
      isFavorite: b.bracket_label === activeBracketLabel,
    }))
  }, [brackets, activeBracketLabel])

  return (
    <div className="text-white">
      {/* Header */}
      <div className="px-6 py-3 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="text-xs text-gray-400 hover:text-cyan-300 px-2 py-1 rounded border border-gray-800 hover:border-cyan-700"
          >← Watchlist</button>
          <div>
            <div className="text-base font-bold text-gray-100">{city}</div>
            <div className="text-[11px] text-gray-500">{forecastDate}</div>
          </div>
          {/* Big timezone indicator — clarifies the chart axes for users
              whose browser tz differs from the market's city. */}
          {cityTz !== 'UTC' && (
            <CityNowBadge city={city} cityTz={cityTz} />
          )}
        </div>
        <div className="flex items-center gap-4 text-xs">
          {live.eventInReview && (
            <div className="px-2 py-1 rounded bg-amber-900/60 text-amber-200 text-xs font-semibold tracking-wide animate-pulse">
              🔒 IN REVIEW
            </div>
          )}
          <div className={`font-mono ${urgent ? 'text-orange-300' : 'text-gray-300'}`}>
            ⏱ resolves in <span className="font-bold">{formatTtr(latestTtrMinutes)}</span>
          </div>
          {(marketClosed || live.eventClosed) && <div className="px-2 py-0.5 rounded bg-red-950 text-red-300">CLOSED</div>}
          <div className="text-gray-500">
            {live.lastFetched ? `live ${live.lastFetched.toLocaleTimeString()}` : ''}
          </div>
        </div>
      </div>

      {loading && <div className="p-6 text-gray-400 text-sm">Loading market history…</div>}
      {error && <div className="p-6 text-red-400 text-sm">Error: {error}</div>}
      {!loading && !error && brackets.length === 0 && (
        <div className="p-6 text-sm text-gray-400">
          No price history for this market yet. The collector writes one row per minute —
          if this market just appeared on Polymarket, give it a few cycles.
        </div>
      )}

      {!loading && !error && brackets.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4 p-4">
          {/* Left column: chart + cross-bracket table */}
          <div className="space-y-4">
            {/* Polymarket history (multi-bracket) */}
            <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
              <div className="flex items-center justify-between mb-2">
                <div>
                  <div className="text-lg font-semibold text-gray-100">📈 Polymarket history</div>
                  <div className="text-xs text-gray-400">Same data as polymarket.com's chart. Click a line to focus that bracket.</div>
                </div>
              </div>
              <PolymarketHistoryChart
                brackets={pmHist.brackets}
                focusBracketLabel={activeBracketLabel}
                onSelectBracket={(lbl) => setSelectedBracket(lbl)}
                interval={pmInterval}
                onChangeInterval={setPmInterval}
                cityTz={cityTz}
                lastFetched={pmHist.lastFetched}
                loading={pmHist.loading}
                height={300}
              />
            </div>

            {/* Directional spread calculator */}
            <DirectionalSpreadPanel
              liveByBracket={live.byBracket}
              bracketsOrder={brackets.map((b) => ({
                bracket_label: b.bracket_label,
                bracket_low_native: b.bracket_low_native,
                bracket_high_native: b.bracket_high_native,
              }))}
            />

            {/* Weather chart — Wunderground hourly obs with bracket lines */}
            <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <div className="text-lg font-semibold text-gray-100">
                    🌡️ Temperature
                    {wu.icao && <span className="ml-2 text-sm text-gray-400 font-normal">{wu.icao}</span>}
                  </div>
                  <div className="text-xs text-gray-400 flex items-center gap-4 mt-1">
                    <span className="inline-flex items-center gap-1.5"><span className="inline-block w-4 h-[2.5px] bg-orange-500"/> observed (Wunderground)</span>
                    <span className="inline-flex items-center gap-1.5"><span className="inline-block w-4 h-[2.5px]" style={{ borderTop: '2.5px dashed #fbbf24' }}/> forecast (Wunderground)</span>
                    <span className="inline-flex items-center gap-1.5"><span className="inline-block w-4 h-[2.5px] bg-cyan-400"/> focused bracket</span>
                  </div>
                </div>
                <div className="text-xs text-gray-500 text-right">
                  {wu.lastFetched ? `updated ${wu.lastFetched.toLocaleTimeString()}` : ''}
                  <div>refresh 60s</div>
                </div>
              </div>
              {wu.loading && wu.observations.length === 0 ? (
                <div className="h-56 flex items-center justify-center text-[11px] text-gray-600 border border-dashed border-gray-800 rounded">
                  Fetching Wunderground observations…
                </div>
              ) : wu.error ? (
                <div className="h-56 flex items-center justify-center text-[11px] text-red-400 border border-dashed border-red-900 rounded p-4 text-center">
                  Wunderground error: {wu.error}
                  <br/>
                  <span className="text-gray-500">(Direct browser fetch may have hit CORS. Tell me if this persists — I'll route through a proxy.)</span>
                </div>
              ) : !wu.stationAvailable ? (
                <div className="h-56 flex items-center justify-center text-[11px] text-gray-500 border border-dashed border-gray-800 rounded p-4 text-center">
                  No Wunderground station mapping for {city}.
                  <br/>(Add it to src/lib/wundergroundStations.ts.)
                </div>
              ) : (
                <WeatherChart
                  observations={wu.observations}
                  forecast={wuFc.forecast}
                  bracketLines={bracketLines}
                  unit={unit}
                  height={260}
                  cityTz={cityTz}
                  nowMs={Date.now()}
                />
              )}
            </div>

            {/* Cross-bracket table */}
            <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
              <div className="text-base font-medium text-gray-200 mb-3">All brackets</div>
              <table className="w-full text-sm">
                <thead className="text-gray-400 border-b border-gray-800">
                  <tr>
                    <th className="text-left  py-2 font-normal">Bracket</th>
                    <th className="text-right py-2 font-normal">YES</th>
                    <th className="text-right py-2 font-normal">NO</th>
                    <th className="text-right py-2 font-normal">Bid</th>
                    <th className="text-right py-2 font-normal">Ask</th>
                    <th className="text-right py-2 font-normal">Spread</th>
                  </tr>
                </thead>
                <tbody>
                  {brackets.map((b) => {
                    // Prefer live values; fall back to Supabase historical.
                    const liveTick = live.byBracket[b.bracket_label]
                    const last = bracketLatest(b)
                    const yes = liveTick?.yes_price ?? last?.yes_price
                    const no  = liveTick?.no_price  ?? last?.no_price
                    const bid = liveTick?.best_bid  ?? last?.best_bid
                    const ask = liveTick?.best_ask  ?? last?.best_ask
                    const sp  = liveTick?.spread_pct ?? last?.spread_pct
                    const isActive = b.bracket_label === activeBracketLabel
                    const inReview = Boolean(liveTick?.in_review)
                    return (
                      <tr
                        key={b.bracket_label}
                        onClick={() => setSelectedBracket(b.bracket_label)}
                        className={`cursor-pointer ${isActive ? 'bg-cyan-950/40 text-cyan-200 font-medium' : 'hover:bg-gray-900/50 text-gray-300'}`}
                      >
                        <td className="py-2">
                          {b.bracket_label}
                          {inReview && <span className="ml-2 text-xs text-amber-300">🔒</span>}
                        </td>
                        <td className="text-right font-mono py-2">{yes != null ? `${(yes * 100).toFixed(1)}¢` : '—'}</td>
                        <td className="text-right font-mono py-2">{no  != null ? `${(no  * 100).toFixed(1)}¢` : '—'}</td>
                        <td className="text-right font-mono py-2">{bid != null ? `${(bid * 100).toFixed(1)}¢` : '—'}</td>
                        <td className="text-right font-mono py-2">{ask != null ? `${(ask * 100).toFixed(1)}¢` : '—'}</td>
                        <td className="text-right font-mono py-2">{sp  != null ? `${(sp  * 100).toFixed(1)}%` : '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <div className="text-xs text-gray-500 mt-3">Click a row to focus the chart on that bracket.</div>
            </div>

            {/* Order entry placeholder */}
            <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
              <div className="text-base font-medium text-gray-200 mb-1">Order entry</div>
              <div className="text-sm text-gray-500">Wiring through CLOB shim in Phase 4.</div>
            </div>
          </div>

          {/* Right column: weather + indicators */}
          <div className="space-y-4">
            <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
              <div className="text-base font-semibold text-gray-200 mb-3">Weather context</div>
              <div className="text-sm space-y-2.5">
                {(() => {
                  // Derive Now + Day max from the Wunderground observations
                  // we already fetch for the weather chart. No Supabase needed.
                  const obs = wu.observations
                  const latestF = obs.length > 0 ? obs[obs.length - 1].temp_f : null
                  let maxF: number | null = null
                  for (const o of obs) {
                    if (o.temp_f != null && (maxF == null || o.temp_f > maxF)) maxF = o.temp_f
                  }
                  const latestC = latestF != null ? ((latestF - 32) * 5) / 9 : null
                  const maxC    = maxF    != null ? ((maxF    - 32) * 5) / 9 : null
                  return (
                    <>
                      <div className="flex justify-between items-baseline">
                        <span className="text-gray-400">Now</span>
                        <span className="font-mono text-gray-100 text-base">{nativeTempLabel(latestC, unit)}</span>
                      </div>
                      <div className="flex justify-between items-baseline">
                        <span className="text-gray-400">Day max</span>
                        <span className="font-mono text-cyan-300 text-base">{nativeTempLabel(maxC, unit)}</span>
                      </div>
                    </>
                  )
                })()}
                <div className="flex justify-between items-baseline">
                  <span className="text-gray-400">Local hour</span>
                  <span className="font-mono text-gray-200 text-base">{latestLocalHour != null ? `${latestLocalHour}h` : '—'}</span>
                </div>
                <div className="flex justify-between items-baseline">
                  <span className="text-gray-400">Resolves in</span>
                  <span className={`font-mono text-base ${urgent ? 'text-orange-300' : 'text-gray-200'}`}>{formatTtr(latestTtrMinutes)}</span>
                </div>
              </div>
            </div>

          </div>
        </div>
      )}
    </div>
  )
}
