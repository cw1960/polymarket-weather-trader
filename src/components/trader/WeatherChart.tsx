// WeatherChart — renders Wunderground's hourly temp series for one
// (city, date), with the market's bracket boundaries drawn as horizontal
// reference lines. Designed to sit next to the price chart in the Trade
// Station so you can eyeball "temp is 2° under the 68-69 bracket with
// 3h of warming left".
//
// Same lightweight inline-SVG approach as PriceChart.

import { useEffect, useRef, useState } from 'react'
import type { WuObservation } from '../../hooks/trader/useWundergroundDay'
import { startOfCityDayUtcMs, formatCityTime } from '../../lib/cityTimezones'

// All forecast inputs come in Fahrenheit; the chart converts to its
// display unit. (See useWundergroundForecast for why we always fetch in F.)
export interface ForecastPoint {
  ms: number
  tempF: number
}

interface Props {
  observations: WuObservation[]         // ascending by time (actual hourly obs)
  forecast?: ForecastPoint[]            // ascending by time (hourly forecast)
  // Market bracket boundaries in the chart's display unit (°F or °C).
  // We render each as a horizontal dashed line with a label.
  bracketLines: { label: string; lowNative: number | null; highNative: number | null; isFavorite: boolean }[]
  unit: 'F' | 'C'
  height?: number
  // Optional: a vertical marker at "now" (so you can see what's already
  // happened vs what's still to come within the day).
  nowMs?: number
  // IANA tz for axis labels + day-bounds (e.g. "America/New_York" for Atlanta).
  // Defaults to "UTC" to match prior behavior if caller doesn't pass one.
  cityTz?: string
}


function fToDisplay(f: number | null, unit: 'F' | 'C'): number | null {
  if (f == null) return null
  return unit === 'F' ? f : ((f - 32) * 5) / 9
}


export default function WeatherChart({
  observations, forecast = [], bracketLines, unit, height = 260, nowMs, cityTz = 'UTC',
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(800)
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setWidth((prev) => (Math.abs(w - prev) > 1 ? Math.floor(w) : prev))
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const padL = 52, padR = 60, padT = 18, padB = 28
  const innerW = Math.max(40, width - padL - padR)
  const innerH = Math.max(40, height - padT - padB)

  // Display temps in user's chosen unit
  const tsMs = observations.map((o) => o.valid_time_gmt * 1000)
  const temps = observations.map((o) => fToDisplay(o.temp_f, unit))

  // X domain: full CITY-LOCAL day (00:00 → 24:00 in cityTz). We use UTC ms
  // internally so the data already aligns; we just anchor tMin at the
  // city's local midnight and label ticks in city time. If we have no obs
  // yet, fall back to "now-relative" so the chart still renders.
  const anchorMs = nowMs ?? Date.now()
  const tMinMs = startOfCityDayUtcMs(cityTz, new Date(anchorMs))
  const tMaxMs = tMinMs + 24 * 3600 * 1000

  // Only show forecast points that fall within the day window we're plotting
  // AND are at or after "now" (the past portion of the forecast curve is
  // less interesting since we have the actual observed line for that span).
  const cutMs = nowMs ?? Date.now()
  // Convert forecast F → display unit so it sits on the same axis as obs.
  type FP = { ms: number; tempDisplay: number }
  const forecastDisplay: FP[] = forecast
    .map((f) => ({ ms: f.ms, tempDisplay: fToDisplay(f.tempF, unit) ?? NaN }))
    .filter((f) => Number.isFinite(f.tempDisplay))
  // Build a continuous forecast line that starts ONE hour before "now" so
  // it visibly meets up with the last observation, then runs forward.
  const forecastInWindow = forecastDisplay.filter((f) => f.ms >= cutMs - 60 * 60 * 1000)
  // Y domain: extend to cover observations, forecast, and bracket lines.
  const yVals: number[] = []
  for (const t of temps) if (t != null) yVals.push(t)
  for (const f of forecastInWindow) yVals.push(f.tempDisplay)
  for (const b of bracketLines) {
    if (b.lowNative != null && Number.isFinite(b.lowNative))  yVals.push(b.lowNative)
    if (b.highNative != null && Number.isFinite(b.highNative)) yVals.push(b.highNative)
  }
  let yMin = yVals.length ? Math.min(...yVals) : (unit === 'F' ? 50 : 10)
  let yMax = yVals.length ? Math.max(...yVals) : (unit === 'F' ? 90 : 32)
  if (yMax - yMin < (unit === 'F' ? 8 : 4)) {
    const mid = (yMax + yMin) / 2
    const half = unit === 'F' ? 4 : 2
    yMin = mid - half; yMax = mid + half
  }
  // 1-unit padding above + below
  yMin -= (unit === 'F' ? 1.5 : 1)
  yMax += (unit === 'F' ? 1.5 : 1)

  const xAt = (ms: number) => padL + ((ms - tMinMs) / (tMaxMs - tMinMs)) * innerW
  const yAt = (v: number) => padT + (1 - (v - yMin) / (yMax - yMin || 1)) * innerH

  // Build path for the actual-observed temp line
  let pathParts: string[] = []
  let cur: string[] = []
  for (let i = 0; i < observations.length; i++) {
    const v = temps[i]
    if (v == null) {
      if (cur.length) { pathParts.push(cur.join(' ')); cur = [] }
      continue
    }
    cur.push(`${cur.length === 0 ? 'M' : 'L'}${xAt(tsMs[i]).toFixed(1)},${yAt(v).toFixed(1)}`)
  }
  if (cur.length) pathParts.push(cur.join(' '))

  // Build the forecast path. To visually connect the forecast curve to the
  // observed curve (forecast points sit at HH:00 but observations sit at
  // xx:53, leaving a ~7-minute gap on screen), we prepend the most recent
  // observation as the path's starting point. We then only keep forecast
  // points strictly after the last observation time.
  const lastObsMs = tsMs[tsMs.length - 1]
  const lastObsTemp = temps[temps.length - 1]
  const futureForecast = lastObsMs != null
    ? forecastInWindow.filter((f) => f.ms > lastObsMs)
    : forecastInWindow
  const fcPathPoints: { ms: number; t: number }[] = []
  if (lastObsMs != null && lastObsTemp != null) fcPathPoints.push({ ms: lastObsMs, t: lastObsTemp })
  for (const f of futureForecast) fcPathPoints.push({ ms: f.ms, t: f.tempDisplay })
  const forecastPath = fcPathPoints
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${xAt(p.ms).toFixed(1)},${yAt(p.t).toFixed(1)}`)
    .join(' ')

  // Y ticks: 5 evenly spaced
  const yTicks: number[] = []
  const tickStep = (yMax - yMin) / 4
  for (let i = 0; i <= 4; i++) yTicks.push(yMin + i * tickStep)

  // X ticks: every 4h across the city-local day. Labels show city-local
  // HH:00; positions are computed in UTC ms anchored at city midnight.
  const xTicks: { ms: number; label: string }[] = []
  for (let h = 0; h <= 24; h += 4) {
    const ms = tMinMs + h * 3600 * 1000
    xTicks.push({ ms, label: formatCityTime(ms, cityTz) })
  }

  // Hover snap
  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    if (x < padL || x > width - padR || tsMs.length === 0) { setHoverIdx(null); return }
    const t = tMinMs + ((x - padL) / innerW) * (tMaxMs - tMinMs)
    let lo = 0, hi = tsMs.length - 1
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1
      if (tsMs[mid] < t) lo = mid; else hi = mid
    }
    const best = Math.abs(tsMs[lo] - t) < Math.abs(tsMs[hi] - t) ? lo : hi
    setHoverIdx(best)
  }

  const hoverObs = hoverIdx != null ? observations[hoverIdx] : null
  const hoverTemp = hoverIdx != null ? temps[hoverIdx] : null
  const lastTemp = temps[temps.length - 1]
  const nowX = nowMs != null && nowMs >= tMinMs && nowMs <= tMaxMs ? xAt(nowMs) : null

  return (
    <div ref={containerRef} className="w-full" style={{ height }}>
      <svg
        width={width}
        height={height}
        onPointerMove={onMove}
        onPointerLeave={() => setHoverIdx(null)}
        className="select-none"
      >
        {/* Y gridlines + ticks */}
        {yTicks.map((y, i) => (
          <g key={i}>
            <line x1={padL} x2={width - padR} y1={yAt(y)} y2={yAt(y)} stroke="#1f2937" />
            <text x={padL - 6} y={yAt(y) + 4} textAnchor="end" fontSize="12" fill="#9ca3af">
              {y.toFixed(unit === 'F' ? 0 : 1)}°{unit}
            </text>
          </g>
        ))}
        {/* X ticks */}
        {xTicks.map((t, i) => (
          <text key={i} x={xAt(t.ms)} y={height - padB + 16} textAnchor="middle" fontSize="12" fill="#9ca3af">
            {t.label}
          </text>
        ))}

        {/* Bracket boundary lines.
            We dedupe Y values across brackets (b1.high typically equals
            b2.low) and label each unique boundary on the RIGHT margin only.
            Each label sits in a 12px row; we lift colliding labels by half
            a row so the favorite never gets buried under a neighbour. */}
        {(() => {
          // Build a deduped list of { value, isFavorite } in ascending order
          const byVal = new Map<number, { v: number; isFavorite: boolean }>()
          for (const b of bracketLines) {
            for (const v of [b.lowNative, b.highNative]) {
              if (v == null || !Number.isFinite(v)) continue
              const cur = byVal.get(v)
              if (!cur) byVal.set(v, { v, isFavorite: b.isFavorite })
              else if (b.isFavorite) cur.isFavorite = true
            }
          }
          const uniq = [...byVal.values()].sort((a, b) => a.v - b.v)
          return uniq.map((u, i) => (
            <g key={i}>
              <line
                x1={padL} x2={width - padR}
                y1={yAt(u.v)} y2={yAt(u.v)}
                stroke={u.isFavorite ? '#22d3ee' : '#475569'}
                strokeDasharray="3 3"
                opacity={u.isFavorite ? 0.95 : 0.4}
                strokeWidth={u.isFavorite ? 1.5 : 1}
              />
              <text
                x={width - padR + 4} y={yAt(u.v) + 4}
                textAnchor="start" fontSize="11" fontFamily="monospace"
                fill={u.isFavorite ? '#22d3ee' : '#94a3b8'}
                fontWeight={u.isFavorite ? 600 : 400}
              >
                {u.v.toFixed(unit === 'F' ? 0 : 1)}°
              </text>
            </g>
          ))
        })()}

        {/* "Now" marker */}
        {nowX != null && (
          <g>
            <line
              x1={nowX} x2={nowX} y1={padT} y2={height - padB}
              stroke="#f59e0b" strokeDasharray="2 2" opacity={0.6}
            />
            <text x={nowX} y={padT - 4} textAnchor="middle" fontSize="11" fontWeight={600} fill="#f59e0b">now</text>
          </g>
        )}

        {/* Forecast line (dashed, behind the obs line so obs sits on top) */}
        {forecastPath && (
          <path
            d={forecastPath}
            stroke="#fbbf24"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            fill="none"
            opacity={0.85}
          />
        )}

        {/* Observed-temperature line (solid, on top) */}
        {pathParts.map((d, i) => (
          <path key={i} d={d} stroke="#f97316" strokeWidth={1.8} fill="none" />
        ))}

        {/* Last observation dot */}
        {lastTemp != null && tsMs.length > 0 && (
          <circle cx={xAt(tsMs[tsMs.length - 1])} cy={yAt(lastTemp)} r={3} fill="#f97316" />
        )}

        {/* Hover crosshair */}
        {hoverIdx != null && hoverObs && hoverTemp != null && (
          <g>
            <line
              x1={xAt(tsMs[hoverIdx])} x2={xAt(tsMs[hoverIdx])}
              y1={padT} y2={height - padB}
              stroke="#374151" strokeDasharray="2 2"
            />
            <circle cx={xAt(tsMs[hoverIdx])} cy={yAt(hoverTemp)} r={3.5} fill="#fff" stroke="#ea580c" />
            <g transform={`translate(${Math.min(width - padR - 110, xAt(tsMs[hoverIdx]) + 10)}, ${padT + 6})`}>
              <rect width={106} height={42} fill="#0b1220" stroke="#334155" rx={4} />
              <text x={8} y={17} fontSize="14" fontFamily="monospace" fontWeight={600} fill="#fb923c">
                {hoverTemp.toFixed(unit === 'F' ? 0 : 1)}°{unit}
              </text>
              <text x={8} y={34} fontSize="12" fontFamily="monospace" fill="#cbd5e1">
                {formatCityTime(hoverObs.valid_time_gmt * 1000, cityTz)}
              </text>
            </g>
          </g>
        )}
      </svg>
    </div>
  )
}
