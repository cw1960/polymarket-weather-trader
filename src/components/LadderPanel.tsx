import { Ladder, TradeSignal } from '../types'

interface Props {
  ladders: Ladder[]
  signals: TradeSignal[]
}

function formatDate(d: string): string {
  const dt = new Date(d + 'T12:00:00Z')
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

// Approximate UTC hour at which each city's market resolves (≈ local midnight).
// Cities resolving after UTC midnight are represented as 24+h (e.g. NY at 04:00 UTC = 28).
// Unknown cities default to 20 (mid-range, neutral sort position).
const CITY_RESOLUTION_HOUR: Record<string, number> = {
  // Australasia / Pacific
  Wellington:      12,
  Sydney:          14,
  Melbourne:       14,
  // East Asia
  Tokyo:           15,
  Seoul:           15,
  Osaka:           15,
  Taipei:          16,
  Shanghai:        16,
  Beijing:         16,
  'Hong Kong':     16,
  Chengdu:         16,
  Chongqing:       16,
  Wuhan:           16,
  Singapore:       16,
  'Kuala Lumpur':  16,
  Jakarta:         17,
  Bangkok:         17,
  // South Asia
  Mumbai:          18,
  Delhi:           18,
  Karachi:         19,
  // Middle East / East Africa
  Dubai:           20,
  Nairobi:         21,
  Moscow:          21,
  Istanbul:        21,
  Ankara:          21,
  'Tel Aviv':      22,
  Helsinki:        21,
  // Europe / Africa
  Cairo:           22,
  'Cape Town':     22,
  Johannesburg:    22,
  Warsaw:          22,
  Amsterdam:       22,
  Paris:           22,
  Milan:           22,
  Madrid:          22,
  Berlin:          22,
  Stockholm:       22,
  Lagos:           23,
  London:          23,
  Casablanca:      23,
  Accra:           24,
  // Americas (resolve after UTC midnight → +24)
  'São Paulo':     27,
  'Buenos Aires':  27,
  Santiago:        28,
  'New York':      28,
  Miami:           28,
  Toronto:         28,
  Bogotá:          29,
  Lima:            29,
  Houston:         29,
  Chicago:         29,
  'Mexico City':   30,
  Monterrey:       30,
  Denver:          31,
  Phoenix:         31,
  'Los Angeles':   31,
  Vancouver:       31,
  Seattle:         31,
  Honolulu:        34,
}

function cityResolutionHour(city: string): number {
  return CITY_RESOLUTION_HOUR[city] ?? 20
}

// Sort ladders so the user can watch results roll in around the world:
//   1. TODAY's ladders first, Americas → Europe → Asia (resolution hour descending)
//   2. PAST dates next, newest first, same city order within each date
//   3. FUTURE dates last (tomorrow's open markets not yet in play)
function sortLadders(ladders: Ladder[]): Ladder[] {
  const today = new Date().toLocaleDateString('en-CA') // YYYY-MM-DD in local time

  function datePriority(d: string): number {
    if (d === today) return 0   // today   → top
    if (d < today)  return 1   // past    → middle
    return 2                    // future  → bottom
  }

  return [...ladders].sort((a, b) => {
    // 1. Today → past → future
    const pd = datePriority(a.forecast_date) - datePriority(b.forecast_date)
    if (pd !== 0) return pd

    // 2. Within past group: newest date first; within future group: oldest date first
    const priority = datePriority(a.forecast_date)
    const dateDiff = priority === 2
      ? a.forecast_date.localeCompare(b.forecast_date)   // future: ascending
      : b.forecast_date.localeCompare(a.forecast_date)   // past:   descending

    if (dateDiff !== 0) return dateDiff

    // 3. Within same date: Americas → Europe → Asia (resolution hour descending)
    return cityResolutionHour(b.city) - cityResolutionHour(a.city)
  })
}

export default function LadderPanel({ ladders, signals }: Props) {
  if (ladders.length === 0 && signals.filter(s => s.signal_phase === 'phase2').length === 0) return null

  // Summary stats from OPEN ladders only (Phase 1)
  const openLadderRows = ladders.filter((l) => l.status === 'open')
  const openLadders    = openLadderRows.length
  const ladderCost     = openLadderRows.reduce((sum, l) => sum + l.total_cost_usd, 0)
  const totalCore      = openLadderRows.reduce((sum, l) => sum + l.num_core,  0)
  const totalWings     = openLadderRows.reduce((sum, l) => sum + l.num_wings, 0)

  // Phase 2 confirmation trades + NO sweep (separate from Phase 1 ladders)
  const phase2Signals  = signals.filter((s) =>
    (s.signal_phase === 'phase2' || s.signal_phase === 'phase2_sweep') && s.pnl_usd == null
  )
  const phase2Capital  = phase2Signals.reduce((sum, s) => sum + (s.recommended_position ?? 0), 0)

  // Combined total capital in play today
  const totalCost = ladderCost + phase2Capital

  // Rung stats — Phase 1 ladder rungs only
  const ladderRungs = signals.filter((s) => s.recommended_position != null && s.signal_phase !== 'phase2')

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Ladder Grid
        </h2>
        <span className="text-xs text-gray-600">
          {openLadders} ladders · {phase2Signals.length} P2 · ${totalCost.toFixed(2)} deployed
        </span>
      </div>

      {/* Summary pills */}
      <div className="grid grid-cols-4 gap-3">
        <Pill label="Ladders" value={String(openLadders)} sub="open" />
        <Pill label="Total Rungs" value={String(totalCore + totalWings)} sub={`${totalCore} core · ${totalWings} wings`} />
        <Pill
          label="Capital"
          value={`$${totalCost.toFixed(2)}`}
          sub={phase2Capital > 0 ? `$${ladderCost.toFixed(2)} P1 · $${phase2Capital.toFixed(2)} P2` : 'deployed'}
        />
        <Pill label="Avg Rung" value={ladderRungs.length ? `$${(ladderCost / ladderRungs.length).toFixed(2)}` : '—'} sub="per rung" />
      </div>

      {/* Phase 2 confirmation trades */}
      {phase2Signals.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">
            Phase 2 — Confirmations &amp; NO sweep
          </div>
          <div className="overflow-x-auto overflow-y-auto max-h-36">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-gray-800">
                <tr className="text-gray-500 border-b border-gray-700">
                  <th className="text-left py-1 pr-3">City</th>
                  <th className="text-left py-1 pr-3">Bracket</th>
                  <th className="text-right py-1 pr-3">Conf</th>
                  <th className="text-right py-1 pr-3">Price</th>
                  <th className="text-right py-1 pr-3">Size</th>
                  <th className="text-left py-1">Status</th>
                </tr>
              </thead>
              <tbody>
                {phase2Signals.map((s) => {
                  const isSweep = s.signal_phase === 'phase2_sweep'
                  return (
                    <tr key={s.id} className={`border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors ${isSweep ? 'opacity-80' : ''}`}>
                      <td className="py-1.5 pr-3 font-semibold text-white">
                        {s.city}
                        {isSweep && <span className="ml-1 text-xs text-orange-400 font-normal">sweep</span>}
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-xs">
                        <span className={isSweep ? 'text-orange-300' : 'text-yellow-300'}>{s.outcome}</span>
                      </td>
                      <td className="py-1.5 pr-3 text-right">
                        {isSweep
                          ? <span className="text-orange-400 text-xs font-semibold">NO</span>
                          : <span className={s.confidence != null && s.confidence >= 0.90 ? 'text-green-400' : 'text-yellow-400'}>
                              {s.confidence != null ? `${(s.confidence * 100).toFixed(0)}%` : '—'}
                            </span>
                        }
                      </td>
                      <td className="py-1.5 pr-3 text-right text-gray-300">
                        {(s.market_price * 100).toFixed(1)}¢
                      </td>
                      <td className="py-1.5 pr-3 text-right text-yellow-400">
                        ${(s.recommended_position ?? 0).toFixed(2)}
                      </td>
                      <td className="py-1.5">
                        <span className={`px-1.5 py-0.5 rounded text-xs font-semibold ${
                          s.order_status === 'filled'  ? 'bg-green-900 text-green-300' :
                          s.order_status === 'pending' ? 'bg-blue-900 text-blue-300'  :
                          s.order_status === 'paper'   ? 'bg-gray-700 text-gray-400'  :
                          s.order_status === 'failed'  ? 'bg-red-900 text-red-300'    :
                          'bg-gray-800 text-gray-500'
                        }`}>
                          {s.order_status ?? 'queued'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Active ladders table */}
      <div>
        <div className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">
          Active &amp; recent ladders
        </div>
        <div className="overflow-x-auto overflow-y-auto max-h-52">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-800">
              <tr className="text-gray-500 border-b border-gray-700">
                <th className="text-left py-1 pr-3">City</th>
                <th className="text-left py-1 pr-3">Date</th>
                <th className="text-right py-1 pr-3">Core</th>
                <th className="text-right py-1 pr-3">Wings</th>
                <th className="text-right py-1 pr-3">Cost</th>
                <th className="text-right py-1 pr-3">P&L</th>
                <th className="text-left py-1 pr-3">Forecast</th>
                <th className="text-left py-1">Status</th>
              </tr>
            </thead>
            <tbody>
              {sortLadders(ladders).map((l) => (
                <LadderRow key={l.id} ladder={l} />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Pill({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="bg-gray-700 rounded p-2.5">
      <div className="text-xs text-gray-400 mb-0.5">{label}</div>
      <div className="text-lg font-bold text-white leading-tight">{value}</div>
      <div className="text-xs text-gray-500">{sub}</div>
    </div>
  )
}

function LadderRow({ ladder }: { ladder: Ladder }) {
  // Forecast values are stored in Celsius across ALL cities (the trading
  // logic converts F-market bucket boundaries to C for comparison).  For
  // display, we convert back to the city's native market unit so the
  // number you see matches Polymarket's bracket labels.
  const isF = ladder.unit === 'F'
  const unitLabel = isF ? '°F' : '°C'
  const meanDisplay = ladder.mean_high != null
    ? (isF ? ladder.mean_high * 9 / 5 + 32 : ladder.mean_high)
    : null
  const stdDisplay = ladder.std_high != null
    ? (isF ? ladder.std_high * 9 / 5 : ladder.std_high)
    : null
  const forecast = meanDisplay != null && stdDisplay != null
    ? `${meanDisplay.toFixed(1)}${unitLabel} ±${stdDisplay.toFixed(1)}`
    : '—'
  const pmUrl = ladder.event_slug
    ? `https://polymarket.com/event/${ladder.event_slug}`
    : null
  const pnl = ladder.total_pnl_usd
  const isClosed = ladder.status === 'closed' || ladder.status === 'resolved'

  return (
    <tr className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors">
      <td className="py-1.5 pr-3 font-semibold text-white">
        {pmUrl
          ? <a href={pmUrl} target="_blank" rel="noopener noreferrer" className="hover:text-blue-400 transition-colors">{ladder.city} ↗</a>
          : ladder.city
        }
      </td>
      <td className="py-1.5 pr-3 text-gray-300">{formatDate(ladder.forecast_date)}</td>
      <td className="py-1.5 pr-3 text-right text-green-400">{ladder.num_core}</td>
      <td className="py-1.5 pr-3 text-right text-blue-400">{ladder.num_wings}</td>
      <td className="py-1.5 pr-3 text-right text-yellow-400">${ladder.total_cost_usd.toFixed(2)}</td>
      <td className={`py-1.5 pr-3 text-right font-bold ${
        !isClosed ? 'text-gray-600' :
        pnl == null ? 'text-gray-500' :
        pnl >= 0 ? 'text-green-400' : 'text-red-400'
      }`}>
        {!isClosed ? '—' : pnl == null ? '?' : pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`}
      </td>
      <td className="py-1.5 pr-3 text-gray-400 font-mono text-xs">{forecast}</td>
      <td className="py-1.5">
        <span className={`px-1.5 py-0.5 rounded text-xs font-semibold ${
          ladder.status === 'open'
            ? 'bg-green-900 text-green-300'
            : 'bg-gray-700 text-gray-400'
        }`}>
          {isClosed
            ? `${ladder.winning_rungs ?? 0}W / ${ladder.losing_rungs ?? 0}L`
            : ladder.status}
        </span>
      </td>
    </tr>
  )
}
