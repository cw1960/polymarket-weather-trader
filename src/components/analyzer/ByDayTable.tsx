import type { ByDayRow } from './types'

function fmtUsd(n: number): string {
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(2)}K`
  return `${sign}$${abs.toFixed(2)}`
}

export default function ByDayTable({ rows }: { rows: ByDayRow[] }) {
  if (!rows.length) return null
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg">
      <div className="px-4 py-2 border-b border-gray-700">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">By Day</h3>
        <div className="text-xs text-gray-500">Counted on closing date; open positions in own row</div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm font-mono">
          <thead>
            <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-700">
              <th className="text-left px-3 py-2">Date</th>
              <th className="text-right px-3 py-2">Buys</th>
              <th className="text-right px-3 py-2">Closed</th>
              <th className="text-right px-3 py-2">Open</th>
              <th className="text-right px-3 py-2">W/L</th>
              <th className="text-right px-3 py-2">Spent</th>
              <th className="text-right px-3 py-2">PnL</th>
              <th className="text-right px-3 py-2">ROI</th>
              <th className="text-right px-3 py-2">Avg Hold</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const pnlColor = r.pnl > 0 ? 'text-green-400' : r.pnl < 0 ? 'text-red-400' : 'text-gray-400'
              const roiColor = r.roi_pct > 0 ? 'text-green-400' : r.roi_pct < 0 ? 'text-red-400' : 'text-gray-400'
              return (
                <tr key={r.date} className="border-b border-gray-800 hover:bg-gray-700/30">
                  <td className="px-3 py-1.5 text-gray-300">{r.date}</td>
                  <td className="px-3 py-1.5 text-right text-white">{r.buys}</td>
                  <td className="px-3 py-1.5 text-right text-white">{r.closed}</td>
                  <td className="px-3 py-1.5 text-right text-gray-400">{r.open}</td>
                  <td className="px-3 py-1.5 text-right text-gray-300">{r.wins}W/{r.losses}L</td>
                  <td className="px-3 py-1.5 text-right text-gray-300">{fmtUsd(r.spent)}</td>
                  <td className={`px-3 py-1.5 text-right ${pnlColor}`}>
                    {r.pnl >= 0 ? '+' : ''}{fmtUsd(r.pnl)}
                  </td>
                  <td className={`px-3 py-1.5 text-right ${roiColor}`}>
                    {r.roi_pct >= 0 ? '+' : ''}{r.roi_pct.toFixed(1)}%
                  </td>
                  <td className="px-3 py-1.5 text-right text-gray-400">{r.avg_hold_hours.toFixed(1)}h</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
