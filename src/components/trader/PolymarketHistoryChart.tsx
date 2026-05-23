// PolymarketHistoryChart — multi-bracket price history chart, replicates
// the chart on polymarket.com/event/highest-temperature-...
//
// One line per bracket, 0–100¢ y-axis, time x-axis spanning the full
// returned series. The currently-focused bracket renders thicker / fully
// opaque; the rest fade back.

import { useEffect, useRef, useState } from 'react'
import type { PMBracketHistory, PMInterval } from '../../hooks/trader/usePolymarketHistory'

interface Props {
  brackets: PMBracketHistory[]
  focusBracketLabel: string | null
  onSelectBracket?: (label: string) => void
  interval: PMInterval
  onChangeInterval: (k: PMInterval) => void
  height?: number
  lastFetched: Date | null
  loading: boolean
  cityTz?: string                                  // IANA tz for x-axis labels
}

// 11 distinct colors — enough for typical 11-bracket weather markets.
// Picked to read well on a dark background and stay distinguishable.
const PALETTE = [
  '#60a5fa', '#34d399', '#fbbf24', '#f87171', '#a78bfa',
  '#fb923c', '#22d3ee', '#f472b6', '#84cc16', '#facc15', '#c084fc',
]

function colorFor(i: number, isFavorite: boolean, isHovered: boolean): string {
  return PALETTE[i % PALETTE.length] + (isFavorite || isHovered ? '' : '')
}

function fmtTimeShort(ms: number, cityTz: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: cityTz,
    month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(ms))
}

const INTERVALS: PMInterval[] = ['1h', '6h', '1d', '1w', '1m', 'max']


export default function PolymarketHistoryChart({
  brackets, focusBracketLabel, onSelectBracket,
  interval, onChangeInterval,
  height = 280, lastFetched, loading, cityTz = 'UTC',
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(800)
  const [hoverIdx, setHoverIdx] = useState<{ b: number; i: number } | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setWidth((prev) => (Math.abs(w - prev) > 1 ? Math.floor(w) : prev))
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const padL = 52, padR = 100, padT = 18, padB = 28
  const innerW = Math.max(40, width - padL - padR)
  const innerH = Math.max(40, height - padT - padB)

  // Global X domain = min(first point across all brackets) → max(last point).
  let tMin = Infinity, tMax = -Infinity
  for (const b of brackets) {
    for (const p of b.points) {
      if (p.ms < tMin) tMin = p.ms
      if (p.ms > tMax) tMax = p.ms
    }
  }
  if (!Number.isFinite(tMin) || !Number.isFinite(tMax) || tMin === tMax) {
    tMin = Date.now() - 24 * 3600_000
    tMax = Date.now()
  }
  const xAt = (ms: number) => padL + ((ms - tMin) / (tMax - tMin)) * innerW
  const yAt = (p: number) => padT + (1 - p) * innerH

  // Build paths
  type LinePath = { label: string; color: string; d: string; isFavorite: boolean; lastP: number | null; pointCount: number }
  const lines: LinePath[] = brackets.map((b, i) => {
    const parts: string[] = []
    let started = false
    for (const pt of b.points) {
      if (pt.p == null || !Number.isFinite(pt.p)) continue
      parts.push(`${started ? 'L' : 'M'}${xAt(pt.ms).toFixed(1)},${yAt(pt.p).toFixed(1)}`)
      started = true
    }
    const last = b.points[b.points.length - 1]
    return {
      label: b.bracket_label,
      color: colorFor(i, b.bracket_label === focusBracketLabel, false),
      d: parts.join(' '),
      isFavorite: b.bracket_label === focusBracketLabel,
      lastP: last?.p ?? null,
      pointCount: b.points.length,
    }
  })

  // Y ticks
  const yTicks = [0, 0.25, 0.5, 0.75, 1]
  // X ticks — 5 evenly spaced
  const xTicks: number[] = []
  for (let i = 0; i < 5; i++) xTicks.push(tMin + ((tMax - tMin) * i) / 4)

  // Hover: snap to the nearest point in the FOCUSED bracket (if any),
  // else just show vertical crosshair.
  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    if (x < padL || x > width - padR) { setHoverIdx(null); return }
    const t = tMin + ((x - padL) / innerW) * (tMax - tMin)
    // Find focused bracket
    let bIdx = brackets.findIndex((b) => b.bracket_label === focusBracketLabel)
    if (bIdx < 0) bIdx = 0
    const pts = brackets[bIdx]?.points ?? []
    if (pts.length === 0) { setHoverIdx(null); return }
    let lo = 0, hi = pts.length - 1
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1
      if (pts[mid].ms < t) lo = mid; else hi = mid
    }
    const best = Math.abs(pts[lo].ms - t) < Math.abs(pts[hi].ms - t) ? lo : hi
    setHoverIdx({ b: bIdx, i: best })
  }

  const hoverPt = hoverIdx ? brackets[hoverIdx.b]?.points[hoverIdx.i] : null

  return (
    <div className="w-full">
      {/* Interval picker */}
      <div className="flex items-center gap-2 mb-2 text-xs">
        {INTERVALS.map((k) => (
          <button
            key={k}
            onClick={() => onChangeInterval(k)}
            className={`px-2 py-1 rounded ${k === interval ? 'bg-cyan-900/60 text-cyan-200 font-medium' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900/50'}`}
          >
            {k.toUpperCase()}
          </button>
        ))}
        <div className="ml-auto text-gray-500">
          {loading ? 'loading…' : lastFetched ? `updated ${lastFetched.toLocaleTimeString()} · refresh 30s` : ''}
        </div>
      </div>

      <div ref={containerRef} style={{ height }}>
        <svg
          width={width}
          height={height}
          onPointerMove={onMove}
          onPointerLeave={() => setHoverIdx(null)}
          className="select-none"
        >
          {/* Y gridlines */}
          {yTicks.map((y) => (
            <g key={y}>
              <line x1={padL} x2={width - padR} y1={yAt(y)} y2={yAt(y)} stroke="#1f2937" strokeDasharray={y === 0.5 ? '2 3' : ''} />
              <text x={padL - 6} y={yAt(y) + 4} textAnchor="end" fontSize="12" fill="#9ca3af">
                {(y * 100).toFixed(0)}¢
              </text>
            </g>
          ))}
          {/* X ticks */}
          {xTicks.map((t, i) => (
            <text key={i} x={xAt(t)} y={height - padB + 16} textAnchor="middle" fontSize="11" fill="#9ca3af">
              {fmtTimeShort(t, cityTz)}
            </text>
          ))}

          {/* Bracket lines — non-favorites underneath at low opacity */}
          {lines.filter((l) => !l.isFavorite).map((l) => (
            <path
              key={l.label}
              d={l.d}
              stroke={l.color}
              strokeWidth={1}
              fill="none"
              opacity={0.35}
              style={{ cursor: 'pointer' }}
              onClick={() => onSelectBracket?.(l.label)}
            />
          ))}
          {lines.filter((l) => l.isFavorite).map((l) => (
            <path
              key={l.label}
              d={l.d}
              stroke={l.color}
              strokeWidth={2}
              fill="none"
              opacity={0.95}
            />
          ))}

          {/* Right-edge labels for each bracket */}
          {lines.map((l) => {
            if (l.lastP == null) return null
            const y = yAt(l.lastP)
            return (
              <g key={l.label} style={{ cursor: 'pointer' }} onClick={() => onSelectBracket?.(l.label)}>
                <circle cx={width - padR + 2} cy={y} r={3} fill={l.color} opacity={l.isFavorite ? 1 : 0.55} />
                <text x={width - padR + 8} y={y + 4} textAnchor="start" fontSize="11" fontFamily="monospace"
                  fill={l.color} opacity={l.isFavorite ? 1 : 0.65}
                  fontWeight={l.isFavorite ? 600 : 400}>
                  {l.label} {Math.round(l.lastP * 100)}¢
                </text>
              </g>
            )
          })}

          {/* Hover */}
          {hoverPt && (
            <g>
              <line x1={xAt(hoverPt.ms)} x2={xAt(hoverPt.ms)} y1={padT} y2={height - padB}
                stroke="#374151" strokeDasharray="2 2" />
              <circle cx={xAt(hoverPt.ms)} cy={yAt(hoverPt.p)} r={4} fill="#fff" stroke="#0891b2" />
              <g transform={`translate(${Math.min(width - padR - 130, xAt(hoverPt.ms) + 10)}, ${padT + 6})`}>
                <rect width={124} height={42} fill="#0b1220" stroke="#334155" rx={4} />
                <text x={8} y={17} fontSize="13" fontFamily="monospace" fontWeight={600} fill="#22d3ee">
                  {(hoverPt.p * 100).toFixed(1)}¢
                </text>
                <text x={8} y={34} fontSize="11" fontFamily="monospace" fill="#cbd5e1">
                  {fmtTimeShort(hoverPt.ms, cityTz)}
                </text>
              </g>
            </g>
          )}
        </svg>
      </div>
    </div>
  )
}
