import type { WatchlistTile } from '../../hooks/trader/useWatchlist'
import { useLiveWatchlist } from '../../hooks/trader/useLiveWatchlist'

interface Props {
  onSelectMarket: (city: string, forecastDate: string) => void
}


function Sparkline({ data, width = 100, height = 28 }: { data: { p: number }[]; width?: number; height?: number }) {
  if (!data.length) {
    return <svg width={width} height={height} className="opacity-30" />
  }
  const ps = data.map((d) => d.p)
  const min = Math.min(...ps)
  const max = Math.max(...ps)
  const range = max - min || 0.01
  const path = data.map((d, i) => {
    const x = (i / Math.max(1, data.length - 1)) * width
    const y = height - ((d.p - min) / range) * height
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const lastP = data[data.length - 1].p
  const firstP = data[0].p
  const up = lastP >= firstP
  const stroke = up ? '#22c55e' : '#ef4444'
  return (
    <svg width={width} height={height}>
      <path d={path} stroke={stroke} strokeWidth={1.5} fill="none" />
    </svg>
  )
}


function formatTtr(min: number | null): string {
  if (min == null) return '?'
  if (min < 60) return `${min}m`
  const h = Math.floor(min / 60)
  const m = min % 60
  return `${h}h${m.toString().padStart(2, '0')}m`
}


function MarketTile({ t, onClick }: { t: WatchlistTile; onClick: () => void }) {
  const ttr = formatTtr(t.time_to_resolution_minutes)
  const urgent = (t.time_to_resolution_minutes ?? 0) < 90
  const favPrice = t.market_favorite_yes_price != null ? `${(t.market_favorite_yes_price * 100).toFixed(0)}¢` : '—'
  // Find a contested bracket (price between 20¢ and 80¢) — that's where action lives
  const contested = t.brackets.find((b) => b.yes_price != null && b.yes_price > 0.20 && b.yes_price < 0.80)

  return (
    <button
      onClick={onClick}
      className={`text-left rounded-lg border ${urgent ? 'border-orange-700/60 bg-orange-950/15' : 'border-gray-800 bg-gray-950/40'} p-3 hover:border-cyan-600 hover:bg-cyan-950/10 transition-colors`}
    >
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="text-sm font-bold text-gray-100">{t.city}</div>
          <div className="text-[10px] text-gray-500">{t.forecast_date}</div>
        </div>
        <div className={`text-xs font-mono ${urgent ? 'text-orange-300' : 'text-gray-400'}`}>
          ⏱ {ttr}
        </div>
      </div>

      <div className="text-xs space-y-1 mb-2">
        <div className="flex justify-between">
          <span className="text-gray-500">Local</span>
          <span className="font-mono text-gray-300">{t.current_local_hour != null ? `${Math.floor(t.current_local_hour)}h` : '—'}</span>
        </div>
        {t.brackets.length > 0 && (
          <div className="flex justify-between">
            <span className="text-gray-500">Brackets</span>
            <span className="font-mono text-gray-400">{t.brackets.length}</span>
          </div>
        )}
      </div>

      <div className="border-t border-gray-800 pt-2">
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="text-gray-500">Mkt favorite</span>
          <span className="font-mono text-gray-200">{t.market_favorite_label ?? '—'}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-2xl font-mono text-cyan-300">{favPrice}</span>
          <Sparkline data={t.favorite_sparkline} />
        </div>
        {contested && (
          <div className="text-[10px] text-gray-500 mt-1">
            contested: {contested.bracket_label} @ {((contested.yes_price ?? 0) * 100).toFixed(0)}¢
          </div>
        )}
      </div>
    </button>
  )
}


export default function Watchlist({ onSelectMarket }: Props) {
  const { tiles, loading, lastRefreshed, error } = useLiveWatchlist()

  if (loading) return <div className="p-6 text-gray-400">Loading watchlist...</div>
  if (error) return <div className="p-6 text-red-400">Error: {error}</div>
  if (tiles.length === 0) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-6 text-center">
          <div className="text-gray-300 font-medium mb-2">No tradeable markets right now</div>
          <div className="text-xs text-gray-500">
            Watchlist shows markets where it's currently 10am–4:59pm local at the city.
            After 5pm local the day's high is effectively locked and the market converges
            to 100¢ — nothing left to trade. Check back later in the cycle.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="px-6 py-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-100">📊 Trader Watchlist</h2>
          <p className="text-xs text-gray-400">
            Tradeable markets only: currently 10am–4:59pm local at the city. Click any tile to open the Trade Station.
          </p>
        </div>
        <div className="text-xs text-gray-500">
          {tiles.length} active {tiles.length === 1 ? 'market' : 'markets'} ·{' '}
          {lastRefreshed ? `updated ${lastRefreshed.toLocaleTimeString()}` : ''} · refresh 30s (live gamma)
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
        {tiles.map((t) => (
          <MarketTile
            key={`${t.city}|${t.forecast_date}`}
            t={t}
            onClick={() => onSelectMarket(t.city, t.forecast_date)}
          />
        ))}
      </div>
    </div>
  )
}
