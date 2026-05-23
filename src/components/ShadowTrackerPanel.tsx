import { ShadowSummary } from '../hooks/useShadowTracker'

interface Props {
  shadow: ShadowSummary | null
}

export default function ShadowTrackerPanel({ shadow }: Props) {
  if (!shadow) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 text-gray-500 text-sm">
        Loading shadow tracker…
      </div>
    )
  }

  const startDate = shadow.startedAt
    ? new Date(shadow.startedAt + 'T12:00:00Z').toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', timeZone: 'UTC'
      })
    : '—'

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      <div className="border-b border-gray-700 px-4 py-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold text-white">Shadow Tracker — Higher Price Tiers</div>
            <div className="text-xs text-gray-500 mt-0.5">
              Hypothetical P&L if cap were raised. Calibrated cities only · stake = $45 · since {startDate}
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-gray-500 uppercase tracking-wider">Current Cap P&L (real)</div>
            <div className={`text-lg font-bold tabular-nums ${shadow.currentCapPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {shadow.currentCapPnl >= 0 ? '+' : ''}${shadow.currentCapPnl.toFixed(2)}
            </div>
          </div>
        </div>
      </div>

      <div className="p-4">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-700">
              <th className="text-left  py-1.5 pr-3 font-semibold">Price Tier</th>
              <th className="text-right py-1.5 pr-3 font-semibold">Resolved</th>
              <th className="text-right py-1.5 pr-3 font-semibold">Open</th>
              <th className="text-right py-1.5 pr-3 font-semibold">W / L</th>
              <th className="text-right py-1.5 pr-3 font-semibold">Win Rate</th>
              <th className="text-right py-1.5 pr-3 font-semibold">Breakeven WR</th>
              <th className="text-right py-1.5 pr-3 font-semibold">Hypothetical P&L</th>
              <th className="text-center py-1.5 font-semibold">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {shadow.tiers.map((t) => {
              const verdict = t.resolved < 3
                ? { text: 'too few', color: 'text-gray-500' }
                : t.hypotheticalPnl > 0
                  ? { text: '✓ profitable', color: 'text-green-400' }
                  : t.hypotheticalPnl < 0
                    ? { text: '✗ losing', color: 'text-red-400' }
                    : { text: 'flat', color: 'text-gray-400' }
              return (
                <tr key={t.label} className="border-b border-gray-700/50">
                  <td className="py-2 pr-3 text-white font-semibold">{t.label}</td>
                  <td className="py-2 pr-3 text-right text-gray-300 tabular-nums">{t.resolved}</td>
                  <td className="py-2 pr-3 text-right text-gray-500 tabular-nums">{t.open}</td>
                  <td className="py-2 pr-3 text-right text-gray-300 tabular-nums">
                    {t.wins} / {t.losses}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">
                    {t.resolved > 0 ? (
                      <span className={t.winRate > t.realisticBreakeven ? 'text-green-400' : 'text-yellow-400'}>
                        {t.winRate.toFixed(1)}%
                      </span>
                    ) : <span className="text-gray-600">—</span>}
                  </td>
                  <td className="py-2 pr-3 text-right text-gray-500 tabular-nums">
                    ≥{t.realisticBreakeven.toFixed(0)}%
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums font-bold">
                    {t.resolved > 0 ? (
                      <span className={t.hypotheticalPnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                        {t.hypotheticalPnl >= 0 ? '+' : ''}${t.hypotheticalPnl.toFixed(2)}
                      </span>
                    ) : <span className="text-gray-600">—</span>}
                  </td>
                  <td className={`py-2 text-center text-xs font-semibold ${verdict.color}`}>
                    {verdict.text}
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-gray-600">
              <td className="py-2 pr-3 text-white font-bold">All shadow tiers</td>
              <td className="py-2 pr-3 text-right text-gray-300 tabular-nums">{shadow.totalResolved}</td>
              <td colSpan={4}></td>
              <td className={`py-2 pr-3 text-right tabular-nums font-bold ${shadow.totalShadowPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {shadow.totalShadowPnl >= 0 ? '+' : ''}${shadow.totalShadowPnl.toFixed(2)}
              </td>
              <td></td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="border-t border-gray-700 px-4 py-2 text-xs text-gray-600">
        These are observation trades that the system <i>would</i> have placed at $45 if the cap were raised.
        Once a tier shows ≥10 resolved trades and a positive hypothetical P&L, that tier is a candidate for real-money expansion.
      </div>
    </div>
  )
}
