import { useNotifications, NotificationBatch, NotificationTrade } from '../hooks/useNotifications'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTimestamp(iso: string): { date: string; time: string; relative: string } {
  const dt    = new Date(iso)
  const now   = new Date()
  const diffMs = now.getTime() - dt.getTime()
  const diffH  = diffMs / 3_600_000
  const diffD  = diffMs / 86_400_000

  const date = dt.toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', timeZone: 'UTC',
  })
  const time = dt.toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC',
  }) + ' UTC'

  let relative: string
  if (diffH < 1)       relative = `${Math.floor(diffMs / 60000)}m ago`
  else if (diffH < 24) relative = `${Math.floor(diffH)}h ago`
  else if (diffD < 7)  relative = `${Math.floor(diffD)}d ago`
  else                 relative = date

  return { date, time, relative }
}

// ── Batch card ────────────────────────────────────────────────────────────────

function BatchCard({ batch }: { batch: NotificationBatch }) {
  const { date, time, relative } = formatTimestamp(batch.resolvedAt)
  const isPositive = batch.normPnl >= 0
  const allWin     = batch.losses === 0
  const allLoss    = batch.wins   === 0

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700/60">
        <div className="flex items-center gap-3">
          {/* Win/loss indicator strip */}
          <div className={`w-1 self-stretch rounded-full ${
            allWin  ? 'bg-green-500' :
            allLoss ? 'bg-red-500'   : 'bg-yellow-500'
          }`} />
          <div>
            <div className="text-xs text-gray-400 font-mono">{time}</div>
            <div className="text-xs text-gray-600">{date} · {relative}</div>
          </div>
          <div className="flex items-center gap-1.5 ml-1">
            <span className="text-xs px-2 py-0.5 rounded-full bg-green-900/50 text-green-400 font-semibold">
              {batch.wins}W
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-red-900/50 text-red-400 font-semibold">
              {batch.losses}L
            </span>
          </div>
        </div>
        <div className={`text-sm font-bold tabular-nums ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
          {isPositive ? '+' : ''}${batch.normPnl.toFixed(2)}
        </div>
      </div>

      {/* Trade rows */}
      <div className="divide-y divide-gray-700/40">
        {batch.trades.map((t, i) => (
          <TradeRow key={i} trade={t} />
        ))}
      </div>
    </div>
  )
}

function TradeRow({ trade }: { trade: NotificationTrade }) {
  const icon    = trade.won ? '✅' : '❌'
  const pnlColor = trade.won ? 'text-green-400' : 'text-red-400'

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-700/20 transition-colors">
      <span className="text-sm flex-shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <span className="text-sm font-semibold text-white">{trade.city}</span>
        <span className="text-xs text-gray-500 ml-2 font-mono">[{trade.outcome}]</span>
      </div>
      <div className="flex items-center gap-3 text-xs text-gray-500 flex-shrink-0">
        <span className="font-mono">{(trade.marketPrice * 100).toFixed(1)}¢</span>
        <span className="font-mono text-gray-600">conf {(trade.confidence * 100).toFixed(0)}%</span>
      </div>
      <div className={`text-sm font-bold tabular-nums font-mono flex-shrink-0 ${pnlColor}`}>
        {trade.normPnl >= 0 ? '+' : ''}${trade.normPnl.toFixed(2)}
      </div>
    </div>
  )
}

// ── Main tab ──────────────────────────────────────────────────────────────────

export default function NotificationsTab() {
  const { batches, loading, lastRefreshed, refresh } = useNotifications()

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-500 text-sm">
        Loading notifications…
      </div>
    )
  }

  // Summary stats across all batches
  const totalWins   = batches.reduce((s, b) => s + b.wins,   0)
  const totalLosses = batches.reduce((s, b) => s + b.losses, 0)
  const totalPnl    = Math.round(batches.reduce((s, b) => s + b.normPnl, 0) * 100) / 100
  const winRate     = totalWins + totalLosses > 0
    ? (totalWins / (totalWins + totalLosses)) * 100 : 0

  return (
    <div className="space-y-4">
      {/* Header bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span>
            {batches.length} resolution{batches.length !== 1 ? 's' : ''} · {totalWins + totalLosses} Phase 2 trades
          </span>
          <span>
            Win rate:{' '}
            <span className={winRate >= 50 ? 'text-green-400' : 'text-red-400'}>
              {winRate.toFixed(0)}%
            </span>
          </span>
          <span>
            Net P&L (normalized):{' '}
            <span className={`font-bold ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
            </span>
          </span>
          {lastRefreshed && (
            <span className="text-gray-600">
              Updated {lastRefreshed.toLocaleTimeString()}
            </span>
          )}
        </div>
        <button
          onClick={refresh}
          className="text-xs px-2.5 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
        >
          ↻ Refresh
        </button>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-gray-600">
        <span>Calibrated cities only · $45/trade · price cap 30¢ · $350/day budget.</span>
      </div>

      {/* Batch list */}
      {batches.length === 0 ? (
        <div className="text-center text-gray-500 text-sm py-16">
          No Phase 2 resolutions yet. Check back after the next market settles.
        </div>
      ) : (
        <div className="space-y-3">
          {batches.map(batch => (
            <BatchCard key={batch.batchKey} batch={batch} />
          ))}
        </div>
      )}
    </div>
  )
}
