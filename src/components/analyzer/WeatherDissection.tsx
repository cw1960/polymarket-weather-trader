import type { WeatherDissection } from './types'

function fmtUsd(n: number): string {
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(2)}K`
  return `${sign}$${abs.toFixed(0)}`
}

export default function WeatherDissectionPanel({ data }: { data?: WeatherDissection }) {
  if (!data || data.error) {
    return (
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 text-sm text-gray-500">
        Weather dissection unavailable.{data?.error ? ` (${data.error})` : ''}
      </div>
    )
  }
  if (!data.weather_trades) {
    return (
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 text-sm text-gray-500">
        This trader has no weather-market activity.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* GFS phase histogram */}
      {data.gfs_phase_histogram && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-2">
            Entry timing — hours since last GFS run (00z/06z/12z/18z)
          </h3>
          <p className="text-xs text-gray-500 mb-3">
            If buys concentrate at 0–1h after each run, suggests latency-arb like us. Spread out → not GFS-timed.
          </p>
          <GfsBar histogram={data.gfs_phase_histogram} />
        </div>
      )}

      {/* Price bucket P&L — the key weather edge analysis */}
      {data.price_bucket_pnl && data.price_bucket_pnl.length > 0 && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg">
          <div className="px-4 py-2 border-b border-gray-700">
            <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
              Price-bucket P&L
            </h3>
            <div className="text-xs text-gray-500">
              <span className="text-gray-400">Resolved</span> = closed + on-chain-resolved
              positions.{' '}
              <span className="text-amber-400">Open MTM</span> = mark-to-market on still-open
              positions at current best bid.{' '}
              <span className="text-blue-400">True Est</span> = Resolved P&L + Open MTM
              (best honest estimate).{' '}
              <span className="text-gray-500">[worst, best]</span> brackets the range if every
              open position lost vs. won.
            </div>
          </div>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-[10px] text-gray-500 uppercase border-b border-gray-700">
                <th className="text-left px-2 py-2">Bucket</th>
                <th className="text-right px-2 py-2" title="Resolved positions (closed + on-chain resolved)">Resolved</th>
                <th className="text-right px-2 py-2" title="Win rate on resolved positions">Win %</th>
                <th className="text-right px-2 py-2" title="Realized P&L from resolved positions">Resolved P&L</th>
                <th className="text-right px-2 py-2" title="Still-open positions (markets not yet resolved)">Open n</th>
                <th className="text-right px-2 py-2 text-amber-400" title="Mark-to-market: P&L if liquidated now at current best bid">Open MTM</th>
                <th className="text-right px-2 py-2 text-gray-500" title="P&L if every open position lost (resolved at $0)">Worst case</th>
                <th className="text-right px-2 py-2 text-gray-500" title="P&L if every open position won (resolved at $1)">Best case</th>
                <th className="text-right px-2 py-2 text-blue-400" title="Resolved P&L + Open MTM. The honest current P&L estimate.">True Est</th>
              </tr>
            </thead>
            <tbody>
              {data.price_bucket_pnl.map((b) => {
                const pnlColor = (n: number) =>
                  n > 0 ? 'text-green-400' : n < 0 ? 'text-red-400' : 'text-gray-400'
                const trueEst = b.true_pnl_estimate ?? b.pnl_usd
                const openMtm = b.open_mtm_pnl ?? 0
                const openBest = b.open_best_pnl ?? 0
                const openWorst = b.open_worst_pnl ?? 0
                return (
                  <tr key={b.bucket} className="border-b border-gray-800 hover:bg-gray-700/30">
                    <td className="px-2 py-1.5 text-gray-300">{b.bucket}</td>
                    <td className="px-2 py-1.5 text-right text-white">{b.n_resolved}</td>
                    <td className="px-2 py-1.5 text-right text-gray-300">{b.win_rate_pct.toFixed(1)}%</td>
                    <td className={`px-2 py-1.5 text-right ${pnlColor(b.pnl_usd)}`}>
                      {b.pnl_usd >= 0 ? '+' : ''}{fmtUsd(b.pnl_usd)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-gray-500">{b.n_open}</td>
                    <td className={`px-2 py-1.5 text-right ${pnlColor(openMtm)}`}>
                      {b.n_open > 0
                        ? `${openMtm >= 0 ? '+' : ''}${fmtUsd(openMtm)}`
                        : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right text-xs ${pnlColor(openWorst)}`}>
                      {b.n_open > 0
                        ? `${openWorst >= 0 ? '+' : ''}${fmtUsd(openWorst)}`
                        : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right text-xs ${pnlColor(openBest)}`}>
                      {b.n_open > 0
                        ? `${openBest >= 0 ? '+' : ''}${fmtUsd(openBest)}`
                        : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-bold ${pnlColor(trueEst)}`}>
                      {trueEst >= 0 ? '+' : ''}{fmtUsd(trueEst)}
                    </td>
                  </tr>
                )
              })}
              {/* Bucket totals row — the headline number we just enabled */}
              {(() => {
                const tot = data.price_bucket_pnl.reduce(
                  (acc, b) => {
                    acc.resolved   += b.pnl_usd
                    acc.openMtm    += b.open_mtm_pnl ?? 0
                    acc.openBest   += b.open_best_pnl ?? 0
                    acc.openWorst  += b.open_worst_pnl ?? 0
                    acc.trueEst    += b.true_pnl_estimate ?? b.pnl_usd
                    return acc
                  },
                  { resolved: 0, openMtm: 0, openBest: 0, openWorst: 0, trueEst: 0 },
                )
                const c = (n: number) => n > 0 ? 'text-green-400' : n < 0 ? 'text-red-400' : 'text-gray-400'
                return (
                  <tr className="border-t-2 border-gray-600 bg-gray-900/60 font-bold">
                    <td className="px-2 py-2 text-gray-200" colSpan={3}>TOTAL</td>
                    <td className={`px-2 py-2 text-right ${c(tot.resolved)}`}>
                      {tot.resolved >= 0 ? '+' : ''}{fmtUsd(tot.resolved)}
                    </td>
                    <td className="px-2 py-2"></td>
                    <td className={`px-2 py-2 text-right ${c(tot.openMtm)}`}>
                      {tot.openMtm >= 0 ? '+' : ''}{fmtUsd(tot.openMtm)}
                    </td>
                    <td className={`px-2 py-2 text-right text-xs ${c(tot.openWorst)}`}>
                      {tot.openWorst >= 0 ? '+' : ''}{fmtUsd(tot.openWorst)}
                    </td>
                    <td className={`px-2 py-2 text-right text-xs ${c(tot.openBest)}`}>
                      {tot.openBest >= 0 ? '+' : ''}{fmtUsd(tot.openBest)}
                    </td>
                    <td className={`px-2 py-2 text-right ${c(tot.trueEst)}`}>
                      {tot.trueEst >= 0 ? '+' : ''}{fmtUsd(tot.trueEst)}
                    </td>
                  </tr>
                )
              })()}
            </tbody>
          </table>
        </div>
      )}

      {/* City specialization */}
      {data.cities && data.cities.length > 0 && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-2">City specialization</h3>
          <div className="flex gap-2 flex-wrap text-xs font-mono">
            {data.cities.slice(0, 20).map((c) => (
              <span key={c.city} className="px-2 py-1 bg-gray-700 rounded text-gray-300">
                {c.city}: <span className="text-white font-semibold">{c.trades.toLocaleString()}</span>
                <span className="text-gray-500"> · {fmtUsd(c.buy_volume)}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Hold time */}
      {data.hold_hours_distribution && data.hold_hours_distribution.n > 0 && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-2">Hold time (closed weather positions)</h3>
          <div className="flex gap-4 text-sm font-mono">
            <span className="text-gray-400">n=<span className="text-white">{data.hold_hours_distribution.n}</span></span>
            <span className="text-gray-400">p10=<span className="text-white">{data.hold_hours_distribution.p10.toFixed(1)}h</span></span>
            <span className="text-gray-400">p50=<span className="text-white">{data.hold_hours_distribution.p50.toFixed(1)}h</span></span>
            <span className="text-gray-400">p90=<span className="text-white">{data.hold_hours_distribution.p90.toFixed(1)}h</span></span>
            <span className="text-gray-400">max=<span className="text-white">{data.hold_hours_distribution.max.toFixed(1)}h</span></span>
          </div>
        </div>
      )}
    </div>
  )
}

function GfsBar({ histogram }: { histogram: Record<string, number> }) {
  const entries = Object.entries(histogram).map(([k, v]) => [parseInt(k, 10), v] as [number, number])
  const max = Math.max(...entries.map(([, v]) => v), 1)
  return (
    <div className="space-y-1">
      {entries.sort((a, b) => a[0] - b[0]).map(([h, count]) => (
        <div key={h} className="flex items-center gap-2 text-xs font-mono">
          <span className="text-gray-400 w-16">+{h}h</span>
          <div className="flex-1 bg-gray-900 rounded overflow-hidden h-5">
            <div
              className="h-full bg-blue-700"
              style={{ width: `${(count / max) * 100}%` }}
            />
          </div>
          <span className="text-gray-300 w-16 text-right">{count.toLocaleString()}</span>
        </div>
      ))}
    </div>
  )
}
