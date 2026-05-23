import { useState, useMemo } from 'react'
import { Trade } from '../types'

interface Props {
  trades: Trade[]
  baselineDate: string | null   // ISO date string e.g. "2026-04-30"; null = show all
}

interface CityRow {
  city: string
  pnl: number
  wins: number
  losses: number
  trades: number
  winRate: number
  deployed: number
  roi: number   // net P&L / total deployed * 100
}

function computeStats(trades: Trade[]): CityRow[] {
  const map: Record<string, { pnl: number; wins: number; losses: number; deployed: number }> = {}
  for (const t of trades) {
    if (!map[t.city]) map[t.city] = { pnl: 0, wins: 0, losses: 0, deployed: 0 }
    const pnl = t.pnl ?? 0
    map[t.city].pnl      += pnl
    map[t.city].deployed += t.position_size
    if (pnl > 0) map[t.city].wins   += 1
    else         map[t.city].losses += 1
  }
  return Object.entries(map)
    .map(([city, s]) => {
      const total = s.wins + s.losses
      return {
        city,
        pnl:      Math.round(s.pnl      * 100) / 100,
        wins:     s.wins,
        losses:   s.losses,
        trades:   total,
        winRate:  total > 0 ? (s.wins / total) * 100 : 0,
        deployed: Math.round(s.deployed * 100) / 100,
        roi:      s.deployed > 0 ? (s.pnl / s.deployed) * 100 : 0,
      }
    })
    .sort((a, b) => b.pnl - a.pnl)
}

export default function CityPerformancePanel({ trades, baselineDate }: Props) {
  const [viewMode,   setViewMode]   = useState<'all' | 'baseline'>('baseline')
  const [collapsed,  setCollapsed]  = useState(true)

  const filteredTrades = useMemo(() => {
    if (viewMode === 'all' || !baselineDate) return trades
    return trades.filter((t) => {
      const d = (t.resolved_at ?? t.created_at).slice(0, 10)
      return d >= baselineDate
    })
  }, [trades, viewMode, baselineDate])

  const cities = useMemo(() => computeStats(filteredTrades), [filteredTrades])

  if (trades.length === 0) return null

  const maxAbsPnl  = Math.max(...cities.map((c) => Math.abs(c.pnl)), 1)
  const totalPnl   = cities.reduce((s, c) => s + c.pnl, 0)
  const totalDep   = cities.reduce((s, c) => s + c.deployed, 0)
  const totalRoi   = totalDep > 0 ? (totalPnl / totalDep) * 100 : 0
  const totalWins  = cities.reduce((s, c) => s + c.wins, 0)
  const totalLoss  = cities.reduce((s, c) => s + c.losses, 0)

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-3">
      {/* Header — always visible */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="flex items-center gap-2 text-left group"
        >
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider group-hover:text-gray-300 transition-colors">
            City Performance
          </h2>
          <span className="text-gray-600 text-xs group-hover:text-gray-400 transition-colors">
            {collapsed ? '▶' : '▼'}
          </span>
        </button>
        <div className="flex items-center gap-2">
          {baselineDate && (
            <div className="flex rounded overflow-hidden border border-gray-700 text-xs">
              <button
                onClick={() => setViewMode('baseline')}
                className={`px-2.5 py-1 transition-colors ${
                  viewMode === 'baseline'
                    ? 'bg-blue-700 text-white'
                    : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                }`}
              >
                Since {baselineDate}
              </button>
              <button
                onClick={() => setViewMode('all')}
                className={`px-2.5 py-1 transition-colors ${
                  viewMode === 'all'
                    ? 'bg-blue-700 text-white'
                    : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                }`}
              >
                All-time
              </button>
            </div>
          )}
          <span className="text-xs text-gray-600">
            {cities.length} cities · {filteredTrades.length} resolved
          </span>
        </div>
      </div>

      {/* Inline summary — always visible so you see headline numbers even when collapsed */}
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-500">
        <span>
          Net P&L:{' '}
          <span className={`font-bold ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
          </span>
        </span>
        <span>
          ROI:{' '}
          <span className={`font-bold ${totalRoi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {totalRoi >= 0 ? '+' : ''}{totalRoi.toFixed(1)}%
          </span>
        </span>
        <span>
          W/L: <span className="text-green-400">{totalWins}</span>
          {' / '}
          <span className="text-red-400">{totalLoss}</span>
        </span>
        <span>Deployed: ${totalDep.toFixed(2)}</span>
        <span>
          Profitable: <span className="text-green-400">{cities.filter((c) => c.pnl > 0).length}</span>
          {' / '}
          <span className="text-gray-400">{cities.length}</span> cities
        </span>
        {collapsed && (
          <button
            onClick={() => setCollapsed(false)}
            className="text-blue-500 hover:text-blue-400 transition-colors"
          >
            Show breakdown ↓
          </button>
        )}
      </div>

      {/* Expandable city table */}
      {!collapsed && (
        <>
          <div className="overflow-x-auto overflow-y-auto max-h-64">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-gray-800">
                <tr className="text-gray-500 border-b border-gray-700">
                  <th className="text-left   py-1 pr-3">City</th>
                  <th className="text-right  py-1 pr-3">Trades</th>
                  <th className="text-right  py-1 pr-3">W / L</th>
                  <th className="text-right  py-1 pr-3">Win%</th>
                  <th className="text-right  py-1 pr-3">Deployed</th>
                  <th className="text-right  py-1 pr-3">Net P&L</th>
                  <th className="text-right  py-1 pr-3">ROI</th>
                  <th className="py-1 pl-2"  style={{ minWidth: 70 }}>Bar</th>
                </tr>
              </thead>
              <tbody>
                {cities.map((c) => {
                  const isPos  = c.pnl >= 0
                  const barPct = Math.min((Math.abs(c.pnl) / maxAbsPnl) * 100, 100)
                  return (
                    <tr
                      key={c.city}
                      className="border-b border-gray-700/40 hover:bg-gray-700/20 transition-colors"
                    >
                      <td className="py-1.5 pr-3 font-semibold text-white">{c.city}</td>
                      <td className="py-1.5 pr-3 text-right text-gray-400">{c.trades}</td>
                      <td className="py-1.5 pr-3 text-right">
                        <span className="text-green-400">{c.wins}</span>
                        <span className="text-gray-600"> / </span>
                        <span className="text-red-400">{c.losses}</span>
                      </td>
                      <td className={`py-1.5 pr-3 text-right font-semibold ${
                        c.winRate >= 65 ? 'text-green-400'
                        : c.winRate >= 50 ? 'text-yellow-400'
                        : 'text-red-400'
                      }`}>
                        {c.winRate.toFixed(0)}%
                      </td>
                      <td className="py-1.5 pr-3 text-right text-gray-500">
                        ${c.deployed.toFixed(2)}
                      </td>
                      <td className={`py-1.5 pr-3 text-right font-bold ${isPos ? 'text-green-400' : 'text-red-400'}`}>
                        {isPos ? '+' : ''}${c.pnl.toFixed(2)}
                      </td>
                      <td className={`py-1.5 pr-3 text-right font-semibold ${
                        c.roi >= 10  ? 'text-green-400'
                        : c.roi >= 0 ? 'text-yellow-400'
                        : 'text-red-400'
                      }`}>
                        {c.roi >= 0 ? '+' : ''}{c.roi.toFixed(1)}%
                      </td>
                      <td className="py-1.5 pl-2">
                        <div className="h-2 bg-gray-700 rounded-full overflow-hidden" style={{ minWidth: 60 }}>
                          <div
                            className={`h-full rounded-full ${isPos ? 'bg-green-500' : 'bg-red-500'}`}
                            style={{ width: `${barPct}%` }}
                          />
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <button
            onClick={() => setCollapsed(true)}
            className="w-full text-center text-xs text-gray-600 hover:text-gray-400 transition-colors pt-1"
          >
            Collapse ↑
          </button>
        </>
      )}
    </div>
  )
}
