// RsiChart — small bottom-strip chart for RSI(14). 0–100 axis with
// reference lines at 30 (oversold) and 70 (overbought). Time axis is
// shared with the price chart above so they read top-down at a glance.

import { useEffect, useRef, useState } from 'react'

interface Props {
  tsMs: number[]          // shared x-coords (unix ms), ascending
  rsi: (number | null)[]  // RSI values, same length as tsMs
  tMin: number            // SAME x-domain as the price chart above so the
  tMax: number            //  lines line up visually.
  height?: number
  padL?: number
  padR?: number
}


export default function RsiChart({
  tsMs, rsi, tMin, tMax, height = 80, padL = 52, padR = 24,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(800)

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setWidth((prev) => (Math.abs(w - prev) > 1 ? Math.floor(w) : prev))
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const padT = 6, padB = 18
  const innerW = Math.max(40, width - padL - padR)
  const innerH = Math.max(20, height - padT - padB)

  const xAt = (ms: number) => padL + ((ms - tMin) / (tMax - tMin || 1)) * innerW
  const yAt = (v: number) => padT + (1 - v / 100) * innerH

  // Path
  let path = ''
  let started = false
  for (let i = 0; i < tsMs.length; i++) {
    const v = rsi[i]
    if (v == null) { started = false; continue }
    const x = xAt(tsMs[i])
    const y = yAt(v)
    path += `${started ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)} `
    started = true
  }

  // Find latest RSI value for the right-margin label
  let lastVal: number | null = null
  for (let i = rsi.length - 1; i >= 0; i--) {
    if (rsi[i] != null) { lastVal = rsi[i]; break }
  }
  const lastColor = lastVal == null ? '#6b7280'
    : lastVal >= 70 ? '#f87171'
    : lastVal <= 30 ? '#34d399'
    : '#a78bfa'

  return (
    <div ref={containerRef} className="w-full" style={{ height }}>
      <svg width={width} height={height}>
        {/* 70 / 50 / 30 reference lines */}
        <line x1={padL} x2={width - padR} y1={yAt(70)} y2={yAt(70)} stroke="#7f1d1d" strokeDasharray="2 3" />
        <line x1={padL} x2={width - padR} y1={yAt(50)} y2={yAt(50)} stroke="#374151" strokeDasharray="2 3" opacity={0.6} />
        <line x1={padL} x2={width - padR} y1={yAt(30)} y2={yAt(30)} stroke="#14532d" strokeDasharray="2 3" />

        {/* Y axis labels */}
        <text x={padL - 6} y={yAt(70) + 4} textAnchor="end" fontSize="10" fill="#9ca3af">70</text>
        <text x={padL - 6} y={yAt(50) + 4} textAnchor="end" fontSize="10" fill="#6b7280">50</text>
        <text x={padL - 6} y={yAt(30) + 4} textAnchor="end" fontSize="10" fill="#9ca3af">30</text>
        <text x={padL - 18} y={padT + 9} textAnchor="end" fontSize="10" fill="#9ca3af" fontWeight={600}>RSI</text>

        {/* RSI line */}
        {path && <path d={path} stroke="#a78bfa" strokeWidth={1.5} fill="none" />}

        {/* Right margin current value */}
        {lastVal != null && (
          <g>
            <text x={width - padR + 4} y={yAt(lastVal) + 4} textAnchor="start" fontSize="11" fontFamily="monospace" fill={lastColor}>
              {lastVal.toFixed(1)}
            </text>
          </g>
        )}
      </svg>
    </div>
  )
}
