import { useEffect } from 'react'
import { Trade } from '../types'

interface Props {
  trade: Trade | null
  onClose: () => void
}

function Field({ label, value, mono = false, className = '' }: {
  label: string
  value: React.ReactNode
  mono?: boolean
  className?: string
}) {
  return (
    <div>
      <div className="text-xs text-gray-500 uppercase tracking-wider mb-0.5">{label}</div>
      <div className={`text-sm text-white ${mono ? 'font-mono' : ''} ${className}`}>{value ?? '—'}</div>
    </div>
  )
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-semibold ${color}`}>{label}</span>
  )
}

export default function TradeDrawer({ trade, onClose }: Props) {
  // Close on Escape key
  useEffect(() => {
    if (!trade) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [trade, onClose])

  if (!trade) return null

  const isResolved  = trade.status === 'resolved'
  const isPhase2    = trade.signal_phase === 'phase2'
  const pnl         = trade.pnl ?? null
  const pnlPositive = pnl !== null && pnl >= 0

  const phaseLabel  = isPhase2 ? 'Phase 2' : 'Phase 1'
  const rungLabel   = trade.rung_type
    ? trade.rung_type.charAt(0).toUpperCase() + trade.rung_type.slice(1)
    : null

  const polyUrl = trade.event_slug
    ? `https://polymarket.com/event/${trade.event_slug}`
    : null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />

      {/* Drawer */}
      <div className="fixed right-0 top-0 h-full w-96 bg-gray-900 border-l border-gray-700 z-50 flex flex-col shadow-2xl">

        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-gray-700">
          <div>
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-lg font-bold text-white">{trade.city}</span>
              <span className="text-gray-400 font-mono text-sm">{trade.outcome}</span>
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge
                label={trade.side}
                color={trade.side === 'YES' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}
              />
              <Badge
                label={phaseLabel}
                color={isPhase2 ? 'bg-purple-900 text-purple-300' : 'bg-blue-900 text-blue-300'}
              />
              {rungLabel && rungLabel !== 'Phase2' && (
                <Badge
                  label={rungLabel}
                  color={
                    rungLabel === 'Core' ? 'bg-green-900/60 text-green-400' :
                    rungLabel === 'Wing' ? 'bg-blue-900/60 text-blue-400'  :
                    'bg-red-900/60 text-red-400'
                  }
                />
              )}
              <Badge
                label={trade.is_paper ? 'PAPER' : 'LIVE'}
                color="bg-gray-700 text-gray-400"
              />
              {isResolved && (
                <Badge
                  label={pnlPositive ? '✓ WON' : '✗ LOST'}
                  color={pnlPositive ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}
                />
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white text-xl leading-none ml-2 mt-0.5"
          >
            ✕
          </button>
        </div>

        {/* P&L banner — only when resolved */}
        {isResolved && pnl !== null && (
          <div className={`px-5 py-3 border-b border-gray-700 ${pnlPositive ? 'bg-green-900/30' : 'bg-red-900/30'}`}>
            <div className="text-xs text-gray-400 mb-0.5">Net P&L</div>
            <div className={`text-2xl font-bold ${pnlPositive ? 'text-green-400' : 'text-red-400'}`}>
              {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
            </div>
            {trade.winning_bracket && (
              <div className="text-xs text-gray-400 mt-1">
                Resolved: <span className="text-white font-mono">{trade.winning_bracket}</span>
              </div>
            )}
          </div>
        )}

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">

          {/* Execution */}
          <section>
            <div className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-3">Execution</div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Entry Price" value={`${(trade.entry_price * 100).toFixed(1)}¢`} />
              <Field label="Position Size" value={`$${trade.position_size.toFixed(2)}`} />
              <Field label="Implied Payout" value={
                trade.entry_price > 0
                  ? `$${(trade.position_size * (1 / trade.entry_price - 1)).toFixed(2)}`
                  : '—'
              } />
              <Field label="Shares" value={trade.shares.toFixed(2)} />
            </div>
          </section>

          {/* Order */}
          <section>
            <div className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-3">Order</div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Status" value={
                <span className={`text-xs px-1.5 py-0.5 rounded font-semibold ${
                  trade.order_status === 'filled'  ? 'bg-green-900 text-green-300' :
                  trade.order_status === 'pending' ? 'bg-blue-900 text-blue-300'  :
                  trade.order_status === 'paper'   ? 'bg-gray-700 text-gray-400'  :
                  trade.order_status === 'failed'  ? 'bg-red-900 text-red-300'    :
                  'bg-gray-800 text-gray-500'
                }`}>
                  {trade.order_status ?? 'unknown'}
                </span>
              } />
              <Field label="Forecast Date" value={trade.forecast_date ?? '—'} />
            </div>
          </section>

          {/* Signal intelligence */}
          <section>
            <div className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-3">Signal</div>
            <div className="grid grid-cols-2 gap-4">
              <Field
                label="Confidence"
                value={trade.confidence != null ? `${(trade.confidence * 100).toFixed(1)}%` : '—'}
                className={
                  trade.confidence == null ? '' :
                  trade.confidence >= 0.90 ? 'text-green-400' :
                  trade.confidence >= 0.70 ? 'text-yellow-400' : 'text-gray-400'
                }
              />
              <Field
                label="Edge"
                value={trade.edge_val != null ? `${(trade.edge_val * 100).toFixed(1)}%` : '—'}
                className={trade.edge_val != null && trade.edge_val > 0 ? 'text-green-400' : 'text-red-400'}
              />
              <Field
                label="Model Prob"
                value={trade.model_prob != null ? `${(trade.model_prob * 100).toFixed(1)}%` : '—'}
              />
              <Field label="Phase" value={phaseLabel} />
            </div>
          </section>

          {/* Market question */}
          {trade.market_question && (
            <section>
              <div className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-2">Market Question</div>
              <div className="text-xs text-gray-300 leading-relaxed bg-gray-800 rounded p-3">
                {trade.market_question}
              </div>
            </section>
          )}

          {/* Timing */}
          <section>
            <div className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-3">Timing</div>
            <div className="grid grid-cols-1 gap-2">
              <Field label="Signal Time" value={
                trade.created_at
                  ? new Date(trade.created_at).toLocaleString('en-US', { timeZone: 'UTC', timeZoneName: 'short' })
                  : '—'
              } />
              {trade.resolved_at && (
                <Field label="Resolved At" value={
                  new Date(trade.resolved_at).toLocaleString('en-US', { timeZone: 'UTC', timeZoneName: 'short' })
                } />
              )}
            </div>
          </section>

          {/* Condition ID */}
          {trade.condition_id && (
            <section>
              <div className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-2">Condition ID</div>
              <div className="text-xs text-gray-500 font-mono break-all bg-gray-800 rounded p-2">
                {trade.condition_id}
              </div>
            </section>
          )}
        </div>

        {/* Footer — Polymarket link */}
        {polyUrl && (
          <div className="p-5 border-t border-gray-700">
            <a
              href={polyUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block w-full text-center text-xs px-4 py-2.5 rounded bg-blue-900 hover:bg-blue-800 text-blue-300 font-semibold transition-colors"
            >
              View on Polymarket ↗
            </a>
          </div>
        )}
      </div>
    </>
  )
}
