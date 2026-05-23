import { FillStats } from '../hooks/useExecutionTelemetry'

interface Props {
  stats: FillStats | null
}

function fmtSlip(cents: number): string {
  const sign = cents >= 0 ? '+' : ''
  return `${sign}${cents.toFixed(2)}¢`
}

function colorForSlip(cents: number): string {
  if (cents <= 0) return 'text-green-400'    // we paid <= mid (good)
  if (cents <= 0.5) return 'text-yellow-400' // small premium
  return 'text-red-400'                       // expensive fill
}

function fmtLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}m`
}

export default function ExecutionTelemetryPanel({ stats }: Props) {
  if (!stats) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 text-gray-500 text-sm">
        Loading execution telemetry…
      </div>
    )
  }

  if (stats.totalFills === 0) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <div className="text-sm font-semibold text-white">Execution Telemetry</div>
        <div className="text-xs text-gray-500 mt-1">
          No live fills yet. Metrics will appear when the first trade fills.
        </div>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      <div className="border-b border-gray-700 px-4 py-3 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-white">Execution Telemetry</div>
          <div className="text-xs text-gray-500 mt-0.5">
            Fill quality on real-money trades · {stats.totalFills} filled order{stats.totalFills === 1 ? '' : 's'}
          </div>
        </div>
      </div>

      <div className="p-4 space-y-4">
        <div className="grid grid-cols-4 gap-3 text-xs">
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Avg Slippage</div>
            <div className={`text-lg font-bold tabular-nums ${colorForSlip(stats.avgSlippageCents)}`}>
              {fmtSlip(stats.avgSlippageCents)}
            </div>
            <div className="text-gray-600">fill − mid_at_signal</div>
          </div>
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Intended → Fill</div>
            <div className={`text-lg font-bold tabular-nums ${colorForSlip(stats.avgIntendedVsFill)}`}>
              {fmtSlip(stats.avgIntendedVsFill)}
            </div>
            <div className="text-gray-600">fill − intended_price</div>
          </div>
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Avg Latency</div>
            <div className="text-lg font-bold tabular-nums text-blue-400">
              {fmtLatency(stats.avgLatencyMs)}
            </div>
            <div className="text-gray-600">signal → filled</div>
          </div>
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Worst</div>
            <div className="text-lg font-bold tabular-nums text-orange-400">
              {fmtSlip(stats.worstSlippage)}
            </div>
            <div className="text-gray-600">max single-trade slip</div>
          </div>
        </div>

        <div>
          <div className="text-xs text-gray-500 mb-2">Recent fills (most recent 10)</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-700">
                  <th className="text-left py-1 pr-2 font-semibold">Date</th>
                  <th className="text-left py-1 pr-2 font-semibold">City</th>
                  <th className="text-left py-1 pr-2 font-semibold">Bracket</th>
                  <th className="text-right py-1 pr-2 font-semibold">Bid</th>
                  <th className="text-right py-1 pr-2 font-semibold">Mid</th>
                  <th className="text-right py-1 pr-2 font-semibold">Ask</th>
                  <th className="text-right py-1 pr-2 font-semibold">Intended</th>
                  <th className="text-right py-1 pr-2 font-semibold">Fill</th>
                  <th className="text-right py-1 pr-2 font-semibold">Slip</th>
                  <th className="text-right py-1 font-semibold">Latency</th>
                </tr>
              </thead>
              <tbody>
                {stats.fills.slice(0, 10).map((f) => {
                  const slip = (f.fill_price != null && f.mid_at_signal != null)
                    ? (Number(f.fill_price) - Number(f.mid_at_signal)) * 100
                    : null
                  return (
                    <tr key={f.id} className="border-b border-gray-700/30 text-gray-300">
                      <td className="py-1 pr-2">{f.forecast_date}</td>
                      <td className="py-1 pr-2">{f.city}</td>
                      <td className="py-1 pr-2 text-gray-400">{f.outcome}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{f.bid_at_signal != null ? `${(Number(f.bid_at_signal)*100).toFixed(1)}¢` : '—'}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{f.mid_at_signal != null ? `${(Number(f.mid_at_signal)*100).toFixed(1)}¢` : '—'}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{f.ask_at_signal != null ? `${(Number(f.ask_at_signal)*100).toFixed(1)}¢` : '—'}</td>
                      <td className="py-1 pr-2 text-right tabular-nums text-gray-400">{f.intended_price != null ? `${(Number(f.intended_price)*100).toFixed(1)}¢` : '—'}</td>
                      <td className="py-1 pr-2 text-right tabular-nums font-semibold">{f.fill_price != null ? `${(Number(f.fill_price)*100).toFixed(1)}¢` : '—'}</td>
                      <td className={`py-1 pr-2 text-right tabular-nums ${slip != null ? colorForSlip(slip) : 'text-gray-600'}`}>
                        {slip != null ? fmtSlip(slip) : '—'}
                      </td>
                      <td className="py-1 text-right text-gray-500 tabular-nums">
                        {f.fill_latency_ms != null ? fmtLatency(Number(f.fill_latency_ms)) : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="border-t border-gray-700 px-4 py-2 text-xs text-gray-600">
        Slippage = fill price minus mid quote at signal time. Negative is good (we paid below mid).
      </div>
    </div>
  )
}
