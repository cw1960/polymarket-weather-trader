import { CityCalibration, PrecisionMetrics } from '../hooks/usePrecisionMetrics'

interface Props {
  metrics: PrecisionMetrics | null
  calibration: CityCalibration[]
}

function MetricCard({
  label, value, sub, valueColor,
}: {
  label: string
  value: string
  sub?: string
  valueColor?: string
}) {
  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-3">
      <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-xl font-bold ${valueColor ?? 'text-white'}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function CalibrationPanel({ metrics, calibration }: Props) {
  if (!metrics) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 text-gray-500 text-sm">
        Loading precision metrics…
      </div>
    )
  }

  const calibrated = calibration.filter((c) => c.calibrated)
  const uncalibrated = calibration.filter((c) => !c.calibrated)

  // Color the avg miss based on target
  const missColor =
    metrics.avgMissDistance < 0.5 ? 'text-green-400' :
    metrics.avgMissDistance < 0.7 ? 'text-yellow-400' :
    'text-red-400'

  const recentMissColor =
    metrics.recentAvgMiss === 0 ? 'text-gray-400' :
    metrics.recentAvgMiss < 0.5 ? 'text-green-400' :
    metrics.recentAvgMiss < 0.7 ? 'text-yellow-400' :
    'text-red-400'

  return (
    <div className="space-y-4">
      {/* Precision metric cards */}
      <div className="grid grid-cols-4 gap-3">
        <MetricCard
          label="Avg Miss Distance"
          value={`${metrics.avgMissDistance.toFixed(2)}°C`}
          sub={`baseline · ${metrics.resolvedRealCount} real trades`}
          valueColor={missColor}
        />
        <MetricCard
          label="Recent Miss (7d)"
          value={metrics.recentCount > 0 ? `${metrics.recentAvgMiss.toFixed(2)}°C` : '—'}
          sub={metrics.recentCount > 0 ? `${metrics.recentCount} trades` : 'no recent data'}
          valueColor={recentMissColor}
        />
        <MetricCard
          label="Bracket Accuracy"
          value={`${metrics.exactCount}/${metrics.resolvedRealCount}`}
          sub={`${metrics.exactCount} correct · ${metrics.oneOffCount} off-by-one · ${metrics.twoOffCount} off-by-two+`}
          valueColor={metrics.exactCount > metrics.oneOffCount ? 'text-green-400' : 'text-yellow-400'}
        />
        <MetricCard
          label="Calibrated Cities"
          value={`${metrics.calibratedCities}/${metrics.calibratedCities + metrics.uncalibratedCities}`}
          sub={`${metrics.uncalibratedCities} in observation mode`}
          valueColor="text-blue-400"
        />
      </div>

      {/* Per-city calibration table */}
      <div className="bg-gray-800 rounded-lg border border-gray-700">
        <div className="border-b border-gray-700 px-4 py-3 flex items-center justify-between">
          <div className="text-sm font-semibold text-white">City Calibration State</div>
          <div className="text-xs text-gray-500">
            Variance-adjusted K · Conditional buffer (σ ≥ 0.3°C)
          </div>
        </div>

        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-800">
              <tr className="text-gray-500 border-b border-gray-700">
                <th className="text-left  py-1.5 px-3 font-semibold">City</th>
                <th className="text-right py-1.5 px-3 font-semibold">Raw δ</th>
                <th className="text-right py-1.5 px-3 font-semibold">n</th>
                <th className="text-right py-1.5 px-3 font-semibold">σ_city</th>
                <th className="text-right py-1.5 px-3 font-semibold">K_adj</th>
                <th className="text-right py-1.5 px-3 font-semibold">Eff δ</th>
                <th className="text-center py-1.5 px-3 font-semibold">Buffer</th>
                <th className="text-right py-1.5 px-3 font-semibold">Avg Miss</th>
                <th className="text-left  py-1.5 px-3 font-semibold">Recent</th>
              </tr>
            </thead>
            <tbody>
              {/* Calibrated cities first */}
              {calibrated.map((c) => {
                const effDelta = c.k_adj != null
                  ? (c.delta_samples / (c.delta_samples + c.k_adj)) * c.delta_c
                  : 0
                return (
                  <tr key={c.city} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                    <td className="py-1.5 px-3 font-semibold text-white">{c.city}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums text-gray-300">
                      {c.delta_c >= 0 ? '+' : ''}{c.delta_c.toFixed(2)}
                    </td>
                    <td className="py-1.5 px-3 text-right tabular-nums text-gray-400">{c.delta_samples}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums text-gray-300">
                      {c.sigma_c != null ? c.sigma_c.toFixed(2) : '—'}
                    </td>
                    <td className="py-1.5 px-3 text-right tabular-nums text-gray-300">
                      {c.k_adj != null ? c.k_adj.toFixed(1) : '—'}
                    </td>
                    <td className="py-1.5 px-3 text-right tabular-nums text-yellow-300">
                      {effDelta >= 0 ? '+' : ''}{effDelta.toFixed(2)}
                    </td>
                    <td className="py-1.5 px-3 text-center">
                      <span className={`text-xs px-1.5 py-0.5 rounded font-semibold ${
                        c.buffer_active ? 'bg-orange-900/60 text-orange-400' : 'bg-green-900/60 text-green-400'
                      }`}>
                        {c.buffer_active ? 'ON' : 'OFF'}
                      </span>
                    </td>
                    <td className="py-1.5 px-3 text-right tabular-nums">
                      {c.avg_miss != null ? (
                        <span className={
                          c.avg_miss < 0.5 ? 'text-green-400' :
                          c.avg_miss < 1.0 ? 'text-yellow-400' :
                          'text-red-400'
                        }>{c.avg_miss.toFixed(2)}°</span>
                      ) : <span className="text-gray-600">—</span>}
                    </td>
                    <td className="py-1.5 px-3 text-left text-gray-500 font-mono">
                      {c.recent_misses.length > 0
                        ? c.recent_misses.map((m) => m.toFixed(0)).join(' · ')
                        : '—'}
                    </td>
                  </tr>
                )
              })}
              {/* Uncalibrated divider */}
              {uncalibrated.length > 0 && (
                <tr className="bg-gray-900/50">
                  <td colSpan={9} className="py-1 px-3 text-xs text-gray-500 italic">
                    Observation only (n &lt; 3) — using default delta +1.0°C, buffer ON
                  </td>
                </tr>
              )}
              {uncalibrated.map((c) => (
                <tr key={c.city} className="border-b border-gray-700/30 text-gray-500">
                  <td className="py-1.5 px-3 italic">{c.city}</td>
                  <td className="py-1.5 px-3 text-right tabular-nums">{c.delta_c.toFixed(2)}</td>
                  <td className="py-1.5 px-3 text-right tabular-nums">{c.delta_samples}</td>
                  <td colSpan={6} className="py-1.5 px-3 text-xs italic">awaiting samples</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
