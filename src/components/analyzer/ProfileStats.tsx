import type { Identity, ProfileStats } from './types'

function fmtUsd(n: number): string {
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(2)}K`
  return `${sign}$${abs.toFixed(2)}`
}

function fmtPct(n: number, digits = 1): string {
  return `${(n * 100).toFixed(digits)}%`
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between border-b border-gray-800 py-1.5 text-sm">
      <span className="text-gray-400">{label}</span>
      <span className={`font-mono ${color || 'text-white'}`}>{value}</span>
    </div>
  )
}

export default function ProfileStatsPanel({
  identity, stats,
}: { identity: Identity; stats: ProfileStats }) {
  const pnlColor = stats.net_cashflow_usd >= 0 ? 'text-green-400' : 'text-red-400'
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <h3 className="text-lg font-semibold text-white">
            {identity.username || identity.pseudonym || identity.address.slice(0, 10) + '…'}
          </h3>
          <div className="text-xs text-gray-500 font-mono break-all">{identity.address}</div>
          {identity.bio && <p className="text-xs text-gray-500 mt-1 italic max-w-prose">{identity.bio}</p>}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
        <div>
          <Row label="Total trades" value={stats.total_trades.toLocaleString()} />
          <Row label="Buys / Sells" value={`${stats.buy_count.toLocaleString()} / ${stats.sell_count.toLocaleString()}`} />
          <Row label="Unique markets" value={stats.unique_markets.toLocaleString()} />
          <Row label="Closed positions" value={stats.closed_positions.toLocaleString()} />
          <Row
            label="Open positions"
            value={
              stats.unredeemed_positions && stats.unredeemed_positions > 0
                ? `${(stats.truly_open_positions ?? 0).toLocaleString()} + ${stats.unredeemed_positions.toLocaleString()} unredeemed`
                : stats.open_positions.toLocaleString()
            }
          />
          <Row label="Round-trip rate" value={fmtPct(stats.roundtrip_rate)} />
          <Row label="Weather share" value={fmtPct(stats.weather_share)} />
        </div>
        <div>
          <Row label="Total volume" value={fmtUsd(stats.total_volume_usd)} />
          <Row label="Buy volume" value={fmtUsd(stats.buy_volume_usd)} />
          <Row label="Sell volume" value={fmtUsd(stats.sell_volume_usd)} />
          <Row label="Net cash flow" value={fmtUsd(stats.net_cashflow_usd)} color={pnlColor} />
          <Row label="Avg buy size" value={fmtUsd(stats.avg_buy_size_usd)} />
          <Row label="Median buy price" value={`$${stats.median_buy_price.toFixed(3)}`} />
          <Row label="Avg hold time" value={`${stats.avg_hold_hours.toFixed(1)}h`} />
          <Row label="Median hold time" value={`${stats.median_hold_hours.toFixed(1)}h`} />
        </div>
      </div>
      <div className="mt-3 pt-3 border-t border-gray-700">
        <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Buy price distribution</div>
        <div className="flex gap-2 flex-wrap text-xs font-mono">
          {Object.entries(stats.buy_price_buckets).sort().map(([k, v]) => (
            <span key={k} className="px-2 py-1 bg-gray-700 rounded text-gray-300">
              {k}: <span className="text-white font-semibold">{v.toLocaleString()}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
