// PriceChart — minimal inline-SVG line chart for one bracket's YES price.
//
// Why inline SVG instead of a chart library?
//   • We control every pixel — easy to overlay indicators later (EMAs,
//     Bollinger Bands, RSI) without fighting a 3rd-party API.
//   • No bundle bloat (no recharts/lightweight-charts/visx).
//   • Hover/crosshair logic is ~30 lines of pointer-event math.
//
// Inputs: an ordered (ascending) array of { recorded_at, yes_price } and
// a resolution timestamp (for the vertical end-marker). Renders the
// price line, optional band overlays (passed by Phase 3 step 3), the
// resolution-time marker, and a hover crosshair.

import { useEffect, useRef, useState } from 'react'
import type { TradePoint } from '../../hooks/trader/useTradeStation'
import { formatCityTime } from '../../lib/cityTimezones'

interface Props {
  points: TradePoint[]                          // ascending by time
  resolutionTs: Date | null                     // for the vertical end-marker
  cityTz?: string                                // IANA tz for axis labels (defaults to UTC)
  height?: number
  bands?: BandSeries[]                          // optional overlay lines (e.g. EMA, Bollinger)
  // Optional shaded band between an upper and lower envelope (Bollinger).
  bandFill?: {
    upper: (number | null)[]                    // aligned 1:1 with points
    lower: (number | null)[]
    color: string                               // e.g. "#a78bfa22"
  }
  // Allow the parent to read back the x-domain so a stacked panel (RSI)
  // can render with the same time axis.
  onComputeDomain?: (info: { tMin: number; tMax: number; padL: number; padR: number; tsMs: number[] }) => void
}

export interface BandSeries {
  label: string
  color: string
  values: (number | null)[]                     // aligned 1:1 with points
  dashed?: boolean
}


function fmtTimeShort(d: Date, cityTz: string): string {
  return formatCityTime(d.getTime(), cityTz)
}


export default function PriceChart({ points, resolutionTs, cityTz = 'UTC', height = 240, bands = [], bandFill, onComputeDomain }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const [width, setWidth] = useState(800)

  // Resize observer: keep the SVG width in sync with the container so the
  // chart fills whatever grid cell it lands in.
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setWidth((prev) => (Math.abs(w - prev) > 1 ? Math.floor(w) : prev))
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const padL = 52, padR = 24, padT = 18, padB = 28
  const innerW = Math.max(40, width - padL - padR)
  const innerH = Math.max(40, height - padT - padB)

  const ts = points.map(p => new Date(p.recorded_at).getTime())
  const ps = points.map(p => p.yes_price ?? null)

  // X domain: stretches from the earliest data point to either the
  // resolution time (if it's close) or a small buffer past the last data
  // point. Keeps the chart from being mostly empty when the collector
  // has only had a few minutes to gather data. The resolution marker
  // only appears when it's actually on-chart.
  const tMin = ts[0] ?? Date.now()
  const tLastData = ts[ts.length - 1] ?? tMin
  const dataSpan = Math.max(tLastData - tMin, 60_000)
  const forwardBuffer = Math.max(15 * 60_000, dataSpan * 0.25)   // ≥ 15 min, or 25% of data span
  const tMaxCandidate = tLastData + forwardBuffer
  const tMax = resolutionTs && resolutionTs.getTime() <= tMaxCandidate
    ? resolutionTs.getTime()
    : tMaxCandidate
  // Y: always 0..1 (Polymarket YES prices). Cents on the axis.
  const yMin = 0, yMax = 1

  const xAt = (t: number) => padL + ((t - tMin) / (tMax - tMin || 1)) * innerW
  const yAt = (v: number) => padT + (1 - (v - yMin) / (yMax - yMin)) * innerH

  // Build the main YES path. Break the line on null gaps.
  const segments: string[] = []
  let cur: string[] = []
  for (let i = 0; i < points.length; i++) {
    const v = ps[i]
    if (v == null) {
      if (cur.length) { segments.push(cur.join(' ')); cur = [] }
      continue
    }
    cur.push(`${cur.length === 0 ? 'M' : 'L'}${xAt(ts[i]).toFixed(1)},${yAt(v).toFixed(1)}`)
  }
  if (cur.length) segments.push(cur.join(' '))

  // Build optional band paths
  const bandPaths = bands.map((b) => {
    const seg: string[] = []
    let c: string[] = []
    for (let i = 0; i < points.length; i++) {
      const v = b.values[i]
      if (v == null) {
        if (c.length) { seg.push(c.join(' ')); c = [] }
        continue
      }
      c.push(`${c.length === 0 ? 'M' : 'L'}${xAt(ts[i]).toFixed(1)},${yAt(v).toFixed(1)}`)
    }
    if (c.length) seg.push(c.join(' '))
    return seg.join(' ')
  })

  // Y axis ticks: 0¢, 25¢, 50¢, 75¢, 100¢
  const yTicks = [0, 0.25, 0.5, 0.75, 1]
  // X axis ticks: 4 evenly spaced
  const xTickCount = 5
  const xTicks: number[] = []
  for (let i = 0; i < xTickCount; i++) xTicks.push(tMin + ((tMax - tMin) * i) / (xTickCount - 1))

  // Hover handler: snap hoverIdx to the nearest point by time
  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    if (x < padL || x > width - padR || ts.length === 0) { setHoverIdx(null); return }
    const t = tMin + ((x - padL) / innerW) * (tMax - tMin)
    // Binary-search nearest
    let lo = 0, hi = ts.length - 1
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1
      if (ts[mid] < t) lo = mid; else hi = mid
    }
    const best = Math.abs(ts[lo] - t) < Math.abs(ts[hi] - t) ? lo : hi
    setHoverIdx(best)
  }

  const hoverPt = hoverIdx != null ? points[hoverIdx] : null
  const hoverYes = hoverPt?.yes_price ?? null

  const lastPt = points[points.length - 1]
  const lastYes = lastPt?.yes_price ?? null

  // Resolution marker x
  // Only render the resolution marker when it sits inside the chart's X
  // domain — otherwise it'd be drawn off the right edge.
  const resMs = resolutionTs?.getTime() ?? null
  const resX = resMs != null && resMs >= tMin && resMs <= tMax ? xAt(resMs) : null

  // Bollinger band fill polygon (forward along upper, back along lower).
  let bandFillPath = ''
  if (bandFill) {
    const fwd: string[] = []
    const back: string[] = []
    for (let i = 0; i < points.length; i++) {
      const u = bandFill.upper[i]
      const l = bandFill.lower[i]
      if (u == null || l == null) continue
      fwd.push(`${fwd.length === 0 ? 'M' : 'L'}${xAt(ts[i]).toFixed(1)},${yAt(u).toFixed(1)}`)
      back.unshift(`L${xAt(ts[i]).toFixed(1)},${yAt(l).toFixed(1)}`)
    }
    if (fwd.length > 0) bandFillPath = fwd.join(' ') + ' ' + back.join(' ') + ' Z'
  }

  // Notify parent of the x-domain so a stacked chart (RSI) can align.
  if (onComputeDomain) {
    // Defer to avoid setState-during-render in the parent if it stores domain.
    queueMicrotask(() => onComputeDomain({ tMin, tMax, padL, padR, tsMs: ts }))
  }

  return (
    <div ref={containerRef} className="w-full" style={{ height }}>
      <svg
        width={width}
        height={height}
        onPointerMove={onMove}
        onPointerLeave={() => setHoverIdx(null)}
        className="select-none"
      >
        {/* Gridlines + Y ticks */}
        {yTicks.map((y) => (
          <g key={y}>
            <line
              x1={padL} x2={width - padR}
              y1={yAt(y)} y2={yAt(y)}
              stroke="#1f2937"
              strokeDasharray={y === 0.5 ? '2 3' : ''}
            />
            <text x={padL - 6} y={yAt(y) + 4} textAnchor="end" fontSize="12" fill="#9ca3af">
              {(y * 100).toFixed(0)}¢
            </text>
          </g>
        ))}
        {/* X ticks */}
        {xTicks.map((t, i) => (
          <text
            key={i}
            x={xAt(t)} y={height - padB + 16}
            textAnchor="middle" fontSize="12" fill="#9ca3af"
          >
            {fmtTimeShort(new Date(t), cityTz)}
          </text>
        ))}

        {/* Resolution marker */}
        {resX != null && (
          <g>
            <line
              x1={resX} x2={resX}
              y1={padT} y2={height - padB}
              stroke="#f59e0b"
              strokeDasharray="3 3"
              opacity={0.7}
            />
            <text x={resX} y={padT - 4} textAnchor="middle" fontSize="11" fontWeight={600} fill="#f59e0b">
              resolves
            </text>
          </g>
        )}

        {/* Bollinger band fill — drawn UNDER everything */}
        {bandFillPath && (
          <path d={bandFillPath} fill={bandFill!.color} stroke="none" />
        )}

        {/* Band overlays (drawn before the main line so YES sits on top) */}
        {bandPaths.map((d, i) => (
          <path
            key={bands[i].label}
            d={d}
            stroke={bands[i].color}
            strokeWidth={1}
            strokeDasharray={bands[i].dashed ? '3 2' : ''}
            fill="none"
            opacity={0.85}
          />
        ))}

        {/* Main YES line */}
        {segments.map((d, i) => (
          <path key={i} d={d} stroke="#22d3ee" strokeWidth={1.6} fill="none" />
        ))}

        {/* Last-point dot */}
        {lastPt && lastYes != null && (
          <circle cx={xAt(ts[ts.length - 1])} cy={yAt(lastYes)} r={3} fill="#22d3ee" />
        )}

        {/* Hover crosshair */}
        {hoverIdx != null && hoverPt && hoverYes != null && (
          <g>
            <line
              x1={xAt(ts[hoverIdx])} x2={xAt(ts[hoverIdx])}
              y1={padT} y2={height - padB}
              stroke="#374151" strokeDasharray="2 2"
            />
            <line
              x1={padL} x2={width - padR}
              y1={yAt(hoverYes)} y2={yAt(hoverYes)}
              stroke="#374151" strokeDasharray="2 2"
            />
            <circle cx={xAt(ts[hoverIdx])} cy={yAt(hoverYes)} r={3.5} fill="#fff" stroke="#0891b2" />
            {/* Hover label box */}
            <g transform={`translate(${Math.min(width - padR - 110, xAt(ts[hoverIdx]) + 10)}, ${padT + 6})`}>
              <rect width={106} height={42} fill="#0b1220" stroke="#334155" rx={4} />
              <text x={8} y={17} fontSize="14" fontWeight={600} fill="#22d3ee" fontFamily="monospace">
                {(hoverYes * 100).toFixed(1)}¢
              </text>
              <text x={8} y={34} fontSize="12" fill="#cbd5e1" fontFamily="monospace">
                {fmtTimeShort(new Date(hoverPt.recorded_at), cityTz)}
              </text>
            </g>
          </g>
        )}
      </svg>
    </div>
  )
}
