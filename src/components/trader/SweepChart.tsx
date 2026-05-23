// SweepChart — bar chart showing ROI per swept parameter value.
// Used by the Backtest page to visualize "ROI(hour_utc) across all events"
// in one glance. Each bar is one parameter value; positive ROI = green,
// negative = red.

interface Point {
  paramValue: number | string
  roi: number              // -1 .. 1
  winRate: number          // 0..1
  n: number
  totalPnl: number
}

interface Props {
  points: Point[]
  paramName: string
  width?: number
  height?: number
}

export default function SweepChart({ points, paramName, width = 700, height = 220 }: Props) {
  if (points.length === 0) return null

  const padL = 50, padR = 80, padT = 18, padB = 32
  const innerW = width - padL - padR
  const innerH = height - padT - padB

  // Y range — symmetric around 0, padded to nearest 5%
  const maxAbs = Math.max(0.05, ...points.map((p) => Math.abs(p.roi)))
  const yMax = Math.ceil(maxAbs * 20) / 20      // round up to 5%
  const yAt = (v: number) => padT + (1 - (v + yMax) / (2 * yMax)) * innerH

  const barW = innerW / points.length * 0.7
  const barGap = innerW / points.length
  const baseX = padL + barGap * 0.15

  return (
    <svg width={width} height={height} className="select-none">
      {/* Y axis: zero line, ±yMax, ±yMax/2 */}
      {[-yMax, -yMax / 2, 0, yMax / 2, yMax].map((y, i) => (
        <g key={i}>
          <line x1={padL} x2={width - padR} y1={yAt(y)} y2={yAt(y)}
            stroke={y === 0 ? '#475569' : '#1f2937'}
            strokeDasharray={y === 0 ? '' : '2 3'} />
          <text x={padL - 6} y={yAt(y) + 4} textAnchor="end" fontSize="11" fill="#9ca3af">
            {(y * 100).toFixed(1)}%
          </text>
        </g>
      ))}

      {/* Bars */}
      {points.map((p, i) => {
        const x = baseX + i * barGap
        const y0 = yAt(0)
        const y1 = yAt(p.roi)
        const top = Math.min(y0, y1)
        const h = Math.abs(y1 - y0)
        const color = p.roi >= 0 ? '#10b981' : '#ef4444'
        return (
          <g key={i}>
            <rect x={x} y={top} width={barW} height={h} fill={color} opacity={0.85} />
            <text x={x + barW / 2} y={height - padB + 14} textAnchor="middle" fontSize="11" fill="#9ca3af">
              {String(p.paramValue)}
            </text>
            <text x={x + barW / 2} y={top - 4} textAnchor="middle" fontSize="10" fontFamily="monospace"
              fill={p.roi >= 0 ? '#34d399' : '#fca5a5'}>
              {(p.roi * 100).toFixed(1)}%
            </text>
            <text x={x + barW / 2} y={height - padB + 26} textAnchor="middle" fontSize="9" fill="#6b7280">
              n={p.n}
            </text>
          </g>
        )
      })}

      {/* Axis title */}
      <text x={padL + innerW / 2} y={height - 2} textAnchor="middle" fontSize="11" fill="#9ca3af">
        {paramName}
      </text>
    </svg>
  )
}
