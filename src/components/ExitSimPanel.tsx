import { ExitSimSummary } from '../hooks/useExitSim'

interface Props {
  summary: ExitSimSummary | null
}

function fmt(v: number | null): string {
  if (v == null) return '—'
  const sign = v >= 0 ? '+' : ''
  return `${sign}$${v.toFixed(2)}`
}

function colorFor(pnl: number | null): string {
  if (pnl == null) return 'text-gray-500'
  if (pnl > 0)  return 'text-green-400'
  if (pnl < 0)  return 'text-red-400'
  return 'text-gray-400'
}

export default function ExitSimPanel({ summary }: Props) {
  if (!summary) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 text-gray-500 text-sm">
        Loading exit simulation tracker…
      </div>
    )
  }

  const winningStrategy = (() => {
    const strats = [
      { name: 'Hold',                   pnl: summary.totalHold },
      { name: 'Sell only',              pnl: summary.totalSellOnly },
      { name: 'Switch fresh',           pnl: summary.totalSwitchFresh },
      { name: 'Sell + switch (proceeds)', pnl: summary.totalSellSwitchProceeds },
      { name: 'Sell + switch (fresh)',  pnl: summary.totalSellSwitchFresh },
    ]
    return strats.reduce((best, s) => s.pnl > best.pnl ? s : best, strats[0])
  })()

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      <div className="border-b border-gray-700 px-4 py-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold text-white">Exit Simulation Tracker (Shadow Mode)</div>
            <div className="text-xs text-gray-500 mt-0.5">
              No real trades · simulating post-lock exit strategies on detected bust/decay events
            </div>
          </div>
          <div className="text-right text-xs">
            <div className="text-gray-500">Best strategy so far</div>
            <div className="font-bold text-blue-400">{winningStrategy.name}</div>
          </div>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* Counters */}
        <div className="grid grid-cols-4 gap-3 text-xs">
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Total events</div>
            <div className="text-lg font-bold text-white">{summary.totalEvents}</div>
            <div className="text-gray-600">{summary.bustEvents} busts · {summary.decayEvents} decays</div>
          </div>
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Resolved</div>
            <div className="text-lg font-bold text-white">{summary.resolved}</div>
            <div className="text-gray-600">{summary.pending} pending</div>
          </div>
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Current (hold)</div>
            <div className={`text-lg font-bold tabular-nums ${colorFor(summary.totalHold)}`}>
              {fmt(summary.totalHold)}
            </div>
            <div className="text-gray-600">actual P&L of these trades</div>
          </div>
          <div className="bg-gray-900 rounded p-2">
            <div className="text-gray-500 uppercase tracking-wider">Best alternative</div>
            <div className={`text-lg font-bold tabular-nums ${colorFor(winningStrategy.pnl)}`}>
              {fmt(winningStrategy.pnl)}
            </div>
            <div className="text-gray-600">
              {fmt(winningStrategy.pnl - summary.totalHold)} vs hold
            </div>
          </div>
        </div>

        {/* Strategy comparison */}
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-700">
                <th className="text-left py-1.5 pr-3 font-semibold">Strategy</th>
                <th className="text-right py-1.5 pr-3 font-semibold">Net P&L (resolved sims)</th>
                <th className="text-right py-1.5 font-semibold">vs Hold</th>
              </tr>
            </thead>
            <tbody>
              {[
                { label: 'Hold (current behavior)',     pnl: summary.totalHold },
                { label: 'Sell only',                    pnl: summary.totalSellOnly },
                { label: 'Switch fresh (let die + buy new)', pnl: summary.totalSwitchFresh },
                { label: 'Sell + switch (proceeds)',     pnl: summary.totalSellSwitchProceeds },
                { label: 'Sell + switch (fresh $45)',    pnl: summary.totalSellSwitchFresh },
              ].map((s) => (
                <tr key={s.label} className="border-b border-gray-700/50">
                  <td className="py-1.5 pr-3 text-white">{s.label}</td>
                  <td className={`py-1.5 pr-3 text-right font-semibold tabular-nums ${colorFor(s.pnl)}`}>
                    {fmt(s.pnl)}
                  </td>
                  <td className={`py-1.5 text-right tabular-nums ${colorFor(s.pnl - summary.totalHold)}`}>
                    {s.pnl === summary.totalHold ? '—' : fmt(s.pnl - summary.totalHold)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Recent events */}
        {summary.rows.length > 0 && (
          <div>
            <div className="text-xs text-gray-500 mb-2">Recent events (last 10)</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-500 border-b border-gray-700">
                    <th className="text-left py-1 pr-2 font-semibold">Date</th>
                    <th className="text-left py-1 pr-2 font-semibold">City</th>
                    <th className="text-left py-1 pr-2 font-semibold">Type</th>
                    <th className="text-left py-1 pr-2 font-semibold">Bet</th>
                    <th className="text-left py-1 pr-2 font-semibold">New</th>
                    <th className="text-right py-1 pr-2 font-semibold">Bust sell</th>
                    <th className="text-right py-1 pr-2 font-semibold">New entry</th>
                    <th className="text-right py-1 pr-2 font-semibold">Hold</th>
                    <th className="text-right py-1 font-semibold">Sell+Switch+Fresh</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.rows.slice(0, 10).map((r) => (
                    <tr key={r.id} className="border-b border-gray-700/30 text-gray-300">
                      <td className="py-1 pr-2">{r.forecast_date}</td>
                      <td className="py-1 pr-2">{r.city}</td>
                      <td className="py-1 pr-2">
                        <span className={`text-xs px-1.5 py-0.5 rounded ${
                          r.detection_type === 'bust'
                            ? 'bg-orange-900/60 text-orange-400'
                            : 'bg-gray-700 text-gray-400'
                        }`}>
                          {r.detection_type}
                        </span>
                      </td>
                      <td className="py-1 pr-2 text-gray-400">{r.bet_bracket}</td>
                      <td className="py-1 pr-2 text-gray-400">{r.new_bracket ?? '—'}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">
                        {r.busted_yes_price != null ? `${(r.busted_yes_price * 100).toFixed(1)}¢` : '—'}
                      </td>
                      <td className="py-1 pr-2 text-right tabular-nums">
                        {r.new_yes_price != null ? `${(r.new_yes_price * 100).toFixed(1)}¢` : '—'}
                      </td>
                      <td className={`py-1 pr-2 text-right tabular-nums ${colorFor(r.hold_pnl)}`}>
                        {fmt(r.hold_pnl)}
                      </td>
                      <td className={`py-1 text-right tabular-nums font-semibold ${colorFor(r.sell_switch_fresh_pnl)}`}>
                        {fmt(r.sell_switch_fresh_pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-gray-700 px-4 py-2 text-xs text-gray-600">
        Shadow simulation. No real trades placed. Once a strategy shows consistent improvement
        across 10+ resolved events, it becomes a candidate for live execution.
      </div>
    </div>
  )
}
