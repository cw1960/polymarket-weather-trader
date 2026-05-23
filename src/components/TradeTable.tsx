import { useState } from 'react'
import { Trade } from '../types'
import { useLiveMtm, mtmKey } from '../hooks/useLiveMtm'

interface Props {
  open:     Trade[]
  history:  Trade[]
  onSelect: (trade: Trade) => void
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', timeZone: 'UTC',
  })
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC',
  })
}

function PhaseTag({ phase }: { phase: string | null }) {
  if (phase === 'phase2')
    return <span className="text-xs px-1 py-0.5 rounded bg-purple-900/60 text-purple-400 font-semibold">P2</span>
  return <span className="text-xs px-1 py-0.5 rounded bg-blue-900/40 text-blue-500 font-semibold">P1</span>
}

function SideBadge({ side }: { side: string }) {
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-semibold ${
      side === 'YES' ? 'bg-green-900/60 text-green-400' : 'bg-red-900/60 text-red-400'
    }`}>
      {side}
    </span>
  )
}

function OrderStatusDot({ status }: { status: string | null }) {
  const cfg: Record<string, string> = {
    filled:  'bg-green-400',
    pending: 'bg-blue-400 animate-pulse',
    paper:   'bg-gray-500',
    failed:  'bg-red-400',
  }
  const color = status ? (cfg[status] ?? 'bg-gray-600') : 'bg-gray-700'
  return <span title={status ?? 'unknown'} className={`inline-block w-2 h-2 rounded-full ${color}`} />
}

interface RowProps {
  trade: Trade
  onClick: () => void
  livePrice?: number   // current YES/NO token price from Polymarket data-api, if known
}

function OpenRow({ trade, onClick, livePrice }: RowProps) {
  // Mark-to-market: shares = size/entry; current value = shares*livePrice;
  // unrealized = current_value - size  →  size × (livePrice/entry − 1).
  // Works for both YES and NO because livePrice is the price of the token
  // we actually hold (data-api curPrice per (cid, outcome)).
  const hasMtm = livePrice != null && trade.entry_price > 0 && trade.position_size > 0
  const unrealized = hasMtm ? trade.position_size * (livePrice / trade.entry_price - 1) : null
  return (
    <tr
      onClick={onClick}
      className="border-b border-gray-700/50 hover:bg-gray-700/40 cursor-pointer transition-colors group"
    >
      <td className="py-2 pr-3 font-semibold text-white group-hover:text-blue-300 transition-colors">
        {trade.city}
      </td>
      <td className="py-2 pr-3 font-mono text-yellow-300 text-xs">{trade.outcome}</td>
      <td className="py-2 pr-3"><SideBadge side={trade.side} /></td>
      <td className="py-2 pr-3"><PhaseTag phase={trade.signal_phase ?? null} /></td>
      <td className="py-2 pr-3 text-right text-gray-300 tabular-nums">
        {(trade.entry_price * 100).toFixed(1)}¢
      </td>
      <td className="py-2 pr-3 text-right text-yellow-400 tabular-nums">
        ${trade.position_size.toFixed(2)}
      </td>
      {/* Live mark-to-market price (current market price for the token we hold). */}
      <td className="py-2 pr-3 text-right text-gray-300 tabular-nums">
        {livePrice != null
          ? `${(livePrice * 100).toFixed(1)}¢`
          : <span className="text-gray-600">—</span>}
      </td>
      {/* Unrealized P&L based on the live price. Refreshes every 5 min. */}
      <td className={`py-2 pr-3 text-right font-semibold tabular-nums ${
        unrealized == null ? '' : unrealized >= 0 ? 'text-green-400' : 'text-red-400'
      }`}>
        {unrealized == null
          ? <span className="text-gray-600">—</span>
          : `${unrealized >= 0 ? '+' : '-'}$${Math.abs(unrealized).toFixed(2)}`}
      </td>
      {/* Profit if the position is held to expiration and wins.
          For a BUY at price p with cost C: payout = C/p, so profit = C × (1/p − 1).
          Works for both YES and NO sides (each is a BUY of its respective token).
          Bright green to draw the eye — this is the "upside if held" number. */}
      <td className="py-2 pr-3 text-right text-green-300 font-semibold tabular-nums">
        {trade.entry_price > 0 && trade.position_size > 0
          ? `+$${(trade.position_size * (1 / trade.entry_price - 1)).toFixed(2)}`
          : <span className="text-gray-600">—</span>
        }
      </td>
      <td className="py-2 pr-3 text-right tabular-nums">
        {trade.confidence != null
          ? <span className={trade.confidence >= 0.90 ? 'text-green-400' : 'text-yellow-400'}>
              {(trade.confidence * 100).toFixed(0)}%
            </span>
          : <span className="text-gray-600">—</span>
        }
      </td>
      <td className="py-2 pr-2 text-center">
        <OrderStatusDot status={trade.order_status ?? null} />
      </td>
      <td className="py-2 text-right text-gray-600 text-xs tabular-nums">
        {formatDate(trade.created_at)} {formatTime(trade.created_at)}
      </td>
    </tr>
  )
}

function HistoryRow({ trade, onClick }: RowProps) {
  const pnl    = trade.pnl ?? 0
  const won    = pnl > 0
  const isZero = pnl === 0 && trade.status === 'resolved'

  return (
    <tr
      onClick={onClick}
      className="border-b border-gray-700/50 hover:bg-gray-700/40 cursor-pointer transition-colors group"
    >
      <td className="py-2 pr-3">
        <span className={`text-sm font-bold ${won ? 'text-green-400' : 'text-red-400'}`}>
          {won ? '✓' : '✗'}
        </span>
      </td>
      <td className="py-2 pr-3 font-semibold text-white group-hover:text-blue-300 transition-colors">
        {trade.city}
      </td>
      <td className="py-2 pr-3 font-mono text-yellow-300 text-xs">{trade.outcome}</td>
      <td className="py-2 pr-3"><SideBadge side={trade.side} /></td>
      <td className="py-2 pr-3"><PhaseTag phase={trade.signal_phase ?? null} /></td>
      <td className="py-2 pr-3 text-right text-gray-300 tabular-nums">
        {(trade.entry_price * 100).toFixed(1)}¢
      </td>
      <td className="py-2 pr-3 text-right text-yellow-400 tabular-nums">
        ${trade.position_size.toFixed(2)}
      </td>
      <td className={`py-2 pr-3 text-right font-bold tabular-nums ${
        isZero ? 'text-gray-500' : won ? 'text-green-400' : 'text-red-400'
      }`}>
        {trade.pnl == null ? '—' : pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`}
      </td>
      <td className="py-2 text-right text-gray-600 text-xs tabular-nums">
        {trade.resolved_at
          ? `${formatDate(trade.resolved_at)}`
          : formatDate(trade.created_at)}
      </td>
    </tr>
  )
}

export default function TradeTable({ open, history, onSelect }: Props) {
  const [tab,          setTab]          = useState<'open' | 'history'>('open')
  const [phase2Only,   setPhase2Only]   = useState(false)

  // Live mark-to-market prices for open positions (5-min refresh).
  const { prices: livePrices, lastRefreshed: mtmRefreshedAt } = useLiveMtm()

  // Apply phase filter
  const filteredOpen    = phase2Only ? open.filter((t)    => t.signal_phase === 'phase2') : open
  const filteredHistory = phase2Only ? history.filter((t) => t.signal_phase === 'phase2') : history

  const totalOpenSize    = filteredOpen.reduce((s, t) => s + t.position_size, 0)
  // Aggregate unrealized P&L across all currently-open positions whose
  // price we have. Excludes positions without a live quote (treated as $0).
  const totalUnrealized  = filteredOpen.reduce((s, t) => {
    const lp = livePrices.get(mtmKey(t.condition_id, t.side))
    if (lp == null || t.entry_price <= 0 || t.position_size <= 0) return s
    return s + t.position_size * (lp / t.entry_price - 1)
  }, 0)
  const historyPnl       = filteredHistory.reduce((s, t) => s + (t.pnl ?? 0), 0)
  const historyWins      = filteredHistory.filter((t) => (t.pnl ?? 0) > 0).length
  const historyWinRate   = filteredHistory.length > 0 ? (historyWins / filteredHistory.length) * 100 : 0

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      {/* Tab bar */}
      <div className="flex items-center border-b border-gray-700 px-4 pt-3">
        <button
          onClick={() => setTab('open')}
          className={`pb-2 mr-6 text-sm font-semibold border-b-2 transition-colors ${
            tab === 'open'
              ? 'border-blue-400 text-white'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          Open
          <span className={`ml-1.5 text-xs px-1.5 py-0.5 rounded-full font-bold ${
            tab === 'open' ? 'bg-blue-900 text-blue-300' : 'bg-gray-700 text-gray-500'
          }`}>
            {filteredOpen.length}
          </span>
        </button>
        <button
          onClick={() => setTab('history')}
          className={`pb-2 text-sm font-semibold border-b-2 transition-colors ${
            tab === 'history'
              ? 'border-blue-400 text-white'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          History
          <span className={`ml-1.5 text-xs px-1.5 py-0.5 rounded-full font-bold ${
            tab === 'history' ? 'bg-blue-900 text-blue-300' : 'bg-gray-700 text-gray-500'
          }`}>
            {filteredHistory.length}
          </span>
        </button>

        {/* Phase 2 filter toggle */}
        <button
          onClick={() => setPhase2Only((v) => !v)}
          className={`ml-4 pb-2 text-xs px-2.5 py-0.5 mb-0.5 rounded font-semibold transition-colors ${
            phase2Only
              ? 'bg-purple-700 text-white'
              : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
          }`}
          title="Show only Phase 2 real-money trades"
        >
          {phase2Only ? 'Phase 2 only ✕' : 'Phase 2 only'}
        </button>

        {/* Right-side summary */}
        <div className="ml-auto text-xs text-gray-500 pb-2">
          {tab === 'open'
            ? filteredOpen.length > 0
              ? <>
                  <span>${totalOpenSize.toFixed(2)} at risk</span>
                  {' · '}
                  <span
                    className={totalUnrealized >= 0 ? 'text-green-400' : 'text-red-400'}
                    title="Sum of unrealized P&L across open positions (mark-to-market at the latest live price)."
                  >
                    {totalUnrealized >= 0 ? '+' : ''}${totalUnrealized.toFixed(2)} unrealized
                  </span>
                  {mtmRefreshedAt && (
                    <span className="text-gray-600" title={`Live prices refreshed ${mtmRefreshedAt.toLocaleTimeString()}`}>
                      {' · '}MTM {mtmRefreshedAt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })}
                    </span>
                  )}
                </>
              : 'No open positions'
            : filteredHistory.length > 0
              ? <>
                  <span className={historyPnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                    {historyPnl >= 0 ? '+' : ''}${historyPnl.toFixed(2)}
                  </span>
                  {' · '}
                  <span className="text-gray-400">{historyWinRate.toFixed(0)}% win rate</span>
                </>
              : 'No history yet'
          }
        </div>
      </div>

      {/* ── Open tab ── */}
      {tab === 'open' && (
        <div className="p-4">
          {filteredOpen.length === 0 ? (
            <div className="text-center text-gray-500 text-sm py-8">
              {phase2Only ? 'No Phase 2 open positions.' : 'No open positions today.'}
            </div>
          ) : (
            <div className="overflow-x-auto overflow-y-auto max-h-64">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-gray-800">
                  <tr className="text-gray-500 border-b border-gray-700">
                    <th className="text-left py-1.5 pr-3 font-semibold">City</th>
                    <th className="text-left py-1.5 pr-3 font-semibold">Bracket</th>
                    <th className="text-left py-1.5 pr-3 font-semibold">Side</th>
                    <th className="text-left py-1.5 pr-3 font-semibold">Phase</th>
                    <th className="text-right py-1.5 pr-3 font-semibold">Price</th>
                    <th className="text-right py-1.5 pr-3 font-semibold">Size</th>
                    <th
                      className="text-right py-1.5 pr-3 font-semibold"
                      title="Latest live market price for the token we hold (YES or NO). Refreshes every 5 minutes."
                    >
                      Now
                    </th>
                    <th
                      className="text-right py-1.5 pr-3 font-semibold"
                      title="Unrealized P&L if we sold at the current market price: size × (now/entry − 1). Refreshes every 5 minutes."
                    >
                      Unreal
                    </th>
                    <th
                      className="text-right py-1.5 pr-3 font-semibold text-green-400"
                      title="Profit in $ if this position is held to expiration and wins (cost × (1/price − 1)). Does not account for fees."
                    >
                      If Win
                    </th>
                    <th className="text-right py-1.5 pr-3 font-semibold">Conf</th>
                    <th className="text-center py-1.5 pr-2 font-semibold">Ord</th>
                    <th className="text-right py-1.5 font-semibold">Signalled</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredOpen.map((t) => (
                    <OpenRow
                      key={t.id}
                      trade={t}
                      onClick={() => onSelect(t)}
                      livePrice={livePrices.get(mtmKey(t.condition_id, t.side))}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── History tab ── */}
      {tab === 'history' && (
        <div className="p-4">
          {filteredHistory.length === 0 ? (
            <div className="text-center text-gray-500 text-sm py-8">
              {phase2Only ? 'No Phase 2 resolved trades yet.' : 'No resolved trades yet. Markets resolve overnight.'}
            </div>
          ) : (
            <div className="overflow-x-auto overflow-y-auto max-h-72">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-gray-800">
                  <tr className="text-gray-500 border-b border-gray-700">
                    <th className="text-left py-1.5 pr-3 font-semibold w-4"></th>
                    <th className="text-left py-1.5 pr-3 font-semibold">City</th>
                    <th className="text-left py-1.5 pr-3 font-semibold">Bracket</th>
                    <th className="text-left py-1.5 pr-3 font-semibold">Side</th>
                    <th className="text-left py-1.5 pr-3 font-semibold">Phase</th>
                    <th className="text-right py-1.5 pr-3 font-semibold">Price</th>
                    <th className="text-right py-1.5 pr-3 font-semibold">Size</th>
                    <th className="text-right py-1.5 pr-3 font-semibold">P&L</th>
                    <th className="text-right py-1.5 font-semibold">Resolved</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredHistory.map((t) => (
                    <HistoryRow key={t.id} trade={t} onClick={() => onSelect(t)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Hint */}
      {(tab === 'open' ? filteredOpen.length : filteredHistory.length) > 0 && (
        <div className="px-4 pb-3 text-xs text-gray-600 text-center">
          Click any row for full details
        </div>
      )}
    </div>
  )
}
