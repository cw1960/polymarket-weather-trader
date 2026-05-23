import { Trade } from '../types'

interface Props {
  trade: Trade
}

export default function PositionCard({ trade }: Props) {
  const pnl = trade.pnl ?? 0
  const isProfit = pnl >= 0

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 px-4 py-3">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="text-white font-bold">{trade.city}</span>
          <span className="text-gray-400 text-sm">{trade.outcome}</span>
          <span
            className={`text-xs px-1.5 py-0.5 rounded font-semibold ${
              trade.side === 'YES'
                ? 'bg-green-900 text-green-300'
                : 'bg-red-900 text-red-300'
            }`}
          >
            {trade.side}
          </span>
        </div>
        <span
          className={`text-sm font-bold ${
            trade.status === 'open' ? 'text-blue-400' : isProfit ? 'text-green-400' : 'text-red-400'
          }`}
        >
          {trade.status === 'open' ? 'OPEN' : pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`}
        </span>
      </div>
      <div className="flex items-center gap-4 text-xs text-gray-500">
        <span>Entry: ${trade.entry_price.toFixed(3)}</span>
        <span>Size: ${trade.position_size.toFixed(2)}</span>
        <span>{trade.is_paper ? 'PAPER' : 'LIVE'}</span>
      </div>
    </div>
  )
}
