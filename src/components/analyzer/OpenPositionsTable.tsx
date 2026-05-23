import type { OpenPosition } from './types'

function fmtUsd(n: number): string {
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(2)}K`
  return `${sign}$${abs.toFixed(2)}`
}

export default function OpenPositionsTable({ positions }: { positions: OpenPosition[] }) {
  if (!positions.length) {
    return (
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 text-sm text-gray-500">
        No open positions.
      </div>
    )
  }
  const truly = positions.filter((p) => !p.unredeemed_post_resolution)
  const unredeemed = positions.filter((p) => !!p.unredeemed_post_resolution)
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg">
      <div className="px-4 py-2 border-b border-gray-700">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Open Positions <span className="text-xs text-gray-500 normal-case">({positions.length} shown — top by cost basis)</span>
        </h3>
        {unredeemed.length > 0 && (
          <div className="text-xs text-gray-500 mt-0.5">
            <span className="text-yellow-400">⏳ {unredeemed.length} unredeemed</span>
            {' '}— market resolved on-chain but trader hasn't claimed/written off. Not tradable.
          </div>
        )}
      </div>
      <div className="overflow-x-auto max-h-[28rem] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-gray-800">
            <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-700">
              <th className="text-left px-3 py-2">Market</th>
              <th className="text-right px-3 py-2">Side</th>
              <th className="text-right px-3 py-2">Size</th>
              <th className="text-right px-3 py-2">Entry</th>
              <th className="text-right px-3 py-2">Cost</th>
              <th className="text-right px-3 py-2">Mkt Date</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {[...truly, ...unredeemed].map((p) => {
              const stale = !!p.unredeemed_post_resolution
              const rowClass = stale
                ? 'border-b border-gray-800 bg-gray-900/40 opacity-60'
                : 'border-b border-gray-800 hover:bg-gray-700/30'
              return (
                <tr key={p.conditionId + p.outcome} className={rowClass}>
                  <td className="px-3 py-1.5 text-gray-300 max-w-md truncate" title={p.title}>
                    {stale && <span className="text-yellow-400 mr-1" title="Unredeemed post-resolution">⏳</span>}
                    {p.title}
                  </td>
                  <td className={`px-3 py-1.5 text-right text-xs ${p.outcome === 'Yes' ? 'text-green-400' : 'text-red-400'}`}>
                    {p.outcome.toUpperCase()}
                  </td>
                  <td className="px-3 py-1.5 text-right text-white">{p.size.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                  <td className="px-3 py-1.5 text-right text-gray-400">${p.avg_entry_price.toFixed(3)}</td>
                  <td className="px-3 py-1.5 text-right text-gray-300">{fmtUsd(p.cost_basis_usd)}</td>
                  <td className={`px-3 py-1.5 text-right text-xs ${stale ? 'text-yellow-500' : 'text-gray-500'}`}>
                    {p.market_date || new Date(p.entered_at * 1000).toISOString().slice(0, 10)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
