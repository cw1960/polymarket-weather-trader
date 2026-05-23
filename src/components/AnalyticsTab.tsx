import { useState } from 'react'
import {
  ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Cell,
  LineChart, Line,
  ComposedChart, Area,
  ScatterChart, Scatter,
  ReferenceLine, Legend,
} from 'recharts'
import { useAnalytics, CityEdgeRow, ScatterPoint } from '../hooks/useAnalytics'

// ── Design tokens ─────────────────────────────────────────────────────────────
const CARD = 'bg-gray-800 rounded-lg border border-gray-700 p-4'
const TICK = { fill: '#6b7280', fontSize: 11 }
const GRID = '#374151'
const GREEN  = '#22c55e'
const RED    = '#ef4444'
const BLUE   = '#3b82f6'
const YELLOW = '#f59e0b'
const PURPLE = '#a855f7'

function pnlColor(v: number) { return v >= 0 ? GREEN : RED }

// ── Shared chart tooltip ───────────────────────────────────────────────────────
const tooltipStyle = {
  contentStyle: { background: '#1f2937', border: '1px solid #374151', borderRadius: 6, fontSize: 12 },
  labelStyle:   { color: '#9ca3af' },
}

// ── Section wrapper ───────────────────────────────────────────────────────────
function Section({ title, description, children }: {
  title: string; description: string; children: React.ReactNode
}) {
  return (
    <div className="space-y-3">
      <div className="border-b border-gray-700 pb-2">
        <h2 className="text-sm font-bold text-white uppercase tracking-wider">{title}</h2>
        <p className="text-xs text-gray-500 mt-0.5">{description}</p>
      </div>
      {children}
    </div>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return <div className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-3">{children}</div>
}

function Empty({ msg }: { msg: string }) {
  return <p className="text-xs text-gray-600 italic">{msg}</p>
}

// ── §1A  Calibration Curve ────────────────────────────────────────────────────
function CalibrationCurve({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.calibration.length) return <Empty msg="No resolved trades yet." />
  return (
    <div className={CARD}>
      <CardTitle>Calibration — Predicted vs Actual Win Rate</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Blue = model's predicted confidence. Amber = actual win rate.
        Amber bar shorter than blue = model is overconfident in that tier.
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data.calibration} barGap={2}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={v => `${(v * 100).toFixed(0)}%`} tick={TICK} axisLine={false} tickLine={false} domain={[0, 1]} />
          <Tooltip
            {...tooltipStyle}
            formatter={(v: number, name: string) => [`${(v * 100).toFixed(1)}%`, name]}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <Bar dataKey="predicted" name="Predicted" fill={BLUE}   opacity={0.7} radius={[3, 3, 0, 0]} />
          <Bar dataKey="actual"    name="Actual"    fill={YELLOW} opacity={0.9} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-2 flex flex-wrap gap-3">
        {data.calibration.map(b => (
          <span key={b.label} className="text-xs text-gray-600">
            {b.label}:{' '}
            <span className="text-gray-400">{b.count} trades</span>
            {' · '}
            <span className={b.actual >= b.predicted ? 'text-green-400' : 'text-red-400'}>
              {(b.actual * 100).toFixed(0)}% actual
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── §1B  Brier Score Trend ────────────────────────────────────────────────────
function BrierTrend({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.brierTrend.length) return <Empty msg="Need more data." />
  const points = data.brierTrend.filter(p => p.brier7d !== null || p.brier30d !== null)
  if (!points.length) return <Empty msg="Need ≥3 trades in a 7-day window to compute Brier score." />
  return (
    <div className={CARD}>
      <CardTitle>Brier Score Trend (lower = better)</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Rolling 7-day and 30-day Brier scores. Perfect = 0, random = 0.25.
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={points}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" tick={TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={TICK} axisLine={false} tickLine={false} domain={[0, 0.30]} />
          <Tooltip {...tooltipStyle} formatter={(v: number, name: string) => [v?.toFixed(4), name]} />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <ReferenceLine y={0.25} stroke="#6b7280" strokeDasharray="4 4" label={{ value: 'random', fill: '#6b7280', fontSize: 10 }} />
          <Line type="monotone" dataKey="brier7d"  name="7-day"  stroke={YELLOW} strokeWidth={1.5} dot={false} connectNulls />
          <Line type="monotone" dataKey="brier30d" name="30-day" stroke={BLUE}   strokeWidth={2}   dot={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── §1C  Edge Decay ───────────────────────────────────────────────────────────
function EdgeDecay({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.edgeDecay.length) return <Empty msg="No data." />
  return (
    <div className={CARD}>
      <CardTitle>Edge Decay by Time Since Entry</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Win rate per age bucket at resolution. Shorter-held positions winning more = entering too early.
      </p>
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={data.edgeDecay}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis yAxisId="wr" tickFormatter={v => `${(v * 100).toFixed(0)}%`} tick={TICK} axisLine={false} tickLine={false} domain={[0, 1]} />
          <YAxis yAxisId="pnl" orientation="right" tick={TICK} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
          <Tooltip
            {...tooltipStyle}
            formatter={(v: number, name: string) =>
              name === 'Win Rate' ? [`${(v * 100).toFixed(1)}%`, name] : [`$${v.toFixed(2)}`, name]
            }
          />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <ReferenceLine yAxisId="wr" y={0.5} stroke="#6b7280" strokeDasharray="4 4" />
          <Bar yAxisId="wr"  dataKey="winRate"    name="Win Rate"   fill={BLUE}  opacity={0.7} radius={[3, 3, 0, 0]}>
            {data.edgeDecay.map((d, i) => <Cell key={i} fill={d.winRate >= 0.5 ? GREEN : RED} />)}
          </Bar>
          <Line yAxisId="pnl" type="monotone" dataKey="avgNormPnl" name="Avg P&L" stroke={YELLOW} strokeWidth={2} dot={{ r: 3 }} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="mt-2 flex flex-wrap gap-3">
        {data.edgeDecay.map(b => (
          <span key={b.label} className="text-xs text-gray-600">
            {b.label}: <span className="text-gray-400">{b.count}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── §2A  City Edge Table ──────────────────────────────────────────────────────
type SortKey = keyof CityEdgeRow
function CityEdgeTable({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  const [sortKey, setSortKey] = useState<SortKey>('totalNormPnl')
  const [asc,     setAsc]     = useState(false)

  if (!data?.cityEdge.length) return <Empty msg="No data." />

  const rows = [...data.cityEdge].sort((a, b) => {
    const diff = (a[sortKey] as number) - (b[sortKey] as number)
    return asc ? diff : -diff
  })

  function th(key: SortKey, label: string, align = 'right') {
    const active = sortKey === key
    return (
      <th
        className={`px-3 py-2 text-${align} text-xs font-semibold cursor-pointer select-none
          ${active ? 'text-blue-400' : 'text-gray-500 hover:text-gray-300'}`}
        onClick={() => { if (sortKey === key) setAsc(v => !v); else { setSortKey(key); setAsc(false) } }}
      >
        {label}{active ? (asc ? ' ↑' : ' ↓') : ''}
      </th>
    )
  }

  return (
    <div className={CARD}>
      <CardTitle>City-Level Edge (click columns to sort)</CardTitle>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-700">
              {th('city',         'City',       'left')}
              {th('trades',       'Trades')}
              {th('wins',         'W')}
              {th('losses',       'L')}
              {th('winRate',      'Win %')}
              {th('avgNormPnl',   'Avg P&L')}
              {th('totalNormPnl', 'Total P&L')}
              {th('brier',        'Brier')}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700/40">
            {rows.map(r => (
              <tr key={r.city} className="hover:bg-gray-700/20 transition-colors">
                <td className="px-3 py-2 text-left font-semibold text-white">{r.city}</td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-400">{r.trades}</td>
                <td className="px-3 py-2 text-right tabular-nums text-green-400">{r.wins}</td>
                <td className="px-3 py-2 text-right tabular-nums text-red-400">{r.losses}</td>
                <td className={`px-3 py-2 text-right tabular-nums font-semibold ${r.winRate >= 0.5 ? 'text-green-400' : 'text-red-400'}`}>
                  {(r.winRate * 100).toFixed(0)}%
                </td>
                <td className={`px-3 py-2 text-right tabular-nums font-mono ${pnlColor(r.avgNormPnl)}`}>
                  {r.avgNormPnl >= 0 ? '+' : ''}${r.avgNormPnl.toFixed(2)}
                </td>
                <td className={`px-3 py-2 text-right tabular-nums font-mono font-bold ${pnlColor(r.totalNormPnl)}`}>
                  {r.totalNormPnl >= 0 ? '+' : ''}${r.totalNormPnl.toFixed(2)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-400 font-mono">{r.brier.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── §2B  Market Price Distribution ───────────────────────────────────────────
function PriceDistrib({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.priceDistrib.length) return <Empty msg="No data." />
  return (
    <div className={CARD}>
      <CardTitle>Entry Price Distribution — Wins vs Losses</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Where in the price range are we finding edge (or losing it)?
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data.priceDistrib}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis tick={TICK} axisLine={false} tickLine={false} />
          <Tooltip {...tooltipStyle} />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <Bar dataKey="wins"   name="Wins"   stackId="a" fill={GREEN} radius={[0, 0, 0, 0]} />
          <Bar dataKey="losses" name="Losses" stackId="a" fill={RED}   radius={[3, 3, 0, 0]} opacity={0.8} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── §2C  Outcome Bias ─────────────────────────────────────────────────────────
function OutcomeBias({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.outcomeBias.length) return <Empty msg="No data." />
  return (
    <div className={CARD}>
      <CardTitle>YES vs NO Outcome Bias</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Are we systematically over-betting one direction?
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data.outcomeBias}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis tick={TICK} axisLine={false} tickLine={false} />
          <Tooltip {...tooltipStyle} />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <Bar dataKey="wins"   name="Wins"   fill={GREEN} radius={[0, 0, 0, 0]} />
          <Bar dataKey="losses" name="Losses" fill={RED}   radius={[0, 0, 0, 0]} opacity={0.8} />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-2 flex gap-4">
        {data.outcomeBias.map(r => (
          <span key={r.label} className="text-xs">
            <span className="text-gray-500">{r.label} win rate: </span>
            <span className={r.winRate >= 0.5 ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
              {(r.winRate * 100).toFixed(0)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── §3A  P&L by Confidence Tier ───────────────────────────────────────────────
function PnLByTier({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.pnlByTier.length) return <Empty msg="No data." />
  return (
    <div className={CARD}>
      <CardTitle>Avg Normalized P&L by Confidence Tier</CardTitle>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data.pnlByTier}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis yAxisId="pnl" tick={TICK} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
          <YAxis yAxisId="wr"  orientation="right" tick={TICK} axisLine={false} tickLine={false}
            tickFormatter={v => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
          <Tooltip
            {...tooltipStyle}
            formatter={(v: number, name: string) =>
              name === 'Win %' ? [`${(v * 100).toFixed(1)}%`, name] : [`$${v.toFixed(2)}`, name]
            }
          />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <ReferenceLine yAxisId="pnl" y={0} stroke="#6b7280" />
          <Bar yAxisId="pnl" dataKey="avgNormPnl" name="Avg P&L" radius={[3, 3, 0, 0]}>
            {data.pnlByTier.map((d, i) => <Cell key={i} fill={pnlColor(d.avgNormPnl)} />)}
          </Bar>
          <Line yAxisId="wr" type="monotone" dataKey="winRate" name="Win %" stroke={BLUE} strokeWidth={2} dot={{ r: 4 }} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="mt-2 flex flex-wrap gap-3">
        {data.pnlByTier.map(t => (
          <span key={t.label} className="text-xs text-gray-600">
            {t.label}: <span className="text-gray-400">{t.wins}W / {t.losses}L</span>
            {' · '}
            <span className={pnlColor(t.totalNormPnl) === GREEN ? 'text-green-400' : 'text-red-400'}>
              total {t.totalNormPnl >= 0 ? '+' : ''}${t.totalNormPnl.toFixed(2)}
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── §3B  P&L by Hour of Entry ─────────────────────────────────────────────────
function PnLByHour({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.pnlByHour.length) return <Empty msg="No data." />
  return (
    <div className={CARD}>
      <CardTitle>Avg Normalized P&L by UTC Hour of Entry</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Forecast model freshness varies through the day — some windows may be systematically better.
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data.pnlByHour}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="hour" tick={TICK} axisLine={false} tickLine={false} interval={2} />
          <YAxis tick={TICK} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
          <Tooltip {...tooltipStyle} formatter={(v: number) => [`$${v.toFixed(2)}`, 'Avg P&L']} />
          <ReferenceLine y={0} stroke="#6b7280" />
          <Bar dataKey="avgNormPnl" name="Avg P&L" radius={[3, 3, 0, 0]}>
            {data.pnlByHour.map((d, i) => <Cell key={i} fill={pnlColor(d.avgNormPnl)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── §3C  P&L by Days to Resolution ───────────────────────────────────────────
function PnLByDays({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.pnlByDays.length) return <Empty msg="No data." />
  return (
    <div className={CARD}>
      <CardTitle>Avg Normalized P&L by Days Held Before Resolution</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        If short-held trades win more, consider entering closer to resolution.
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data.pnlByDays}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis yAxisId="pnl" tick={TICK} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
          <YAxis yAxisId="wr"  orientation="right" tick={TICK} axisLine={false} tickLine={false}
            tickFormatter={v => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
          <Tooltip
            {...tooltipStyle}
            formatter={(v: number, name: string) =>
              name === 'Win %' ? [`${(v * 100).toFixed(1)}%`, name] : [`$${v.toFixed(2)}`, name]
            }
          />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <ReferenceLine yAxisId="pnl" y={0} stroke="#6b7280" />
          <Bar yAxisId="pnl" dataKey="avgNormPnl" name="Avg P&L" radius={[3, 3, 0, 0]}>
            {data.pnlByDays.map((d, i) => <Cell key={i} fill={pnlColor(d.avgNormPnl)} />)}
          </Bar>
          <Line yAxisId="wr" type="monotone" dataKey="winRate" name="Win %" stroke={BLUE} strokeWidth={2} dot={{ r: 4 }} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="mt-2 flex flex-wrap gap-3">
        {data.pnlByDays.map(d => (
          <span key={d.label} className="text-xs text-gray-600">
            {d.label}: <span className="text-gray-400">{d.trades} trades</span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── §4A  Drawdown Chart ───────────────────────────────────────────────────────
function DrawdownChart({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.drawdown.length) return <Empty msg="No data." />
  const maxDD = Math.min(...data.drawdown.map(d => d.drawdown))
  return (
    <div className={CARD}>
      <CardTitle>Cumulative P&L & Drawdown (normalized Phase 2)</CardTitle>
      <div className="flex items-center gap-4 mb-2">
        <span className="text-xs text-gray-500">
          Max drawdown: <span className="text-red-400 font-semibold">${maxDD.toFixed(2)}</span>
        </span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data.drawdown}>
          <defs>
            <linearGradient id="cumGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={GREEN} stopOpacity={0.15} />
              <stop offset="95%" stopColor={GREEN} stopOpacity={0}    />
            </linearGradient>
            <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={RED} stopOpacity={0.30} />
              <stop offset="95%" stopColor={RED} stopOpacity={0}    />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" tick={TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={TICK} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
          <Tooltip {...tooltipStyle} formatter={(v: number, name: string) => [`$${v.toFixed(2)}`, name]} />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <ReferenceLine y={0} stroke="#6b7280" />
          <Area type="monotone" dataKey="drawdown" name="Drawdown" fill="url(#ddGrad)" stroke={RED} strokeWidth={1.5} fillOpacity={1} />
          <Area type="monotone" dataKey="cumPnl"   name="Cum P&L"  fill="url(#cumGrad)" stroke={GREEN} strokeWidth={2} fillOpacity={1} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── §4B  Daily Budget Utilization ────────────────────────────────────────────
function BudgetUtilization({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.budgetUtil.length) return <Empty msg="No data." />
  const BUDGET = 150
  const avgUtil = data.budgetUtil.reduce((s, d) => s + d.deployed, 0) / data.budgetUtil.length
  return (
    <div className={CARD}>
      <CardTitle>Daily Phase 2 Budget Utilization (last 30 days)</CardTitle>
      <div className="flex items-center gap-4 mb-2">
        <span className="text-xs text-gray-500">
          Budget: <span className="text-white font-semibold">${BUDGET}/day</span>
        </span>
        <span className="text-xs text-gray-500">
          Avg deployed: <span className="text-blue-400 font-semibold">${avgUtil.toFixed(2)}</span>
          {' '}({(avgUtil / BUDGET * 100).toFixed(0)}%)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data.budgetUtil}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" tick={TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={TICK} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} domain={[0, 200]} />
          <Tooltip {...tooltipStyle} formatter={(v: number) => [`$${v.toFixed(2)}`, 'Deployed']} />
          <ReferenceLine y={BUDGET} stroke={YELLOW} strokeDasharray="4 4" label={{ value: '$150 cap', fill: '#f59e0b', fontSize: 10, position: 'right' }} />
          <Bar dataKey="deployed" name="Deployed" fill={BLUE} opacity={0.8} radius={[3, 3, 0, 0]}>
            {data.budgetUtil.map((d, i) => (
              <Cell key={i} fill={d.deployed >= BUDGET * 0.8 ? GREEN : d.deployed >= BUDGET * 0.4 ? BLUE : YELLOW} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── §5A  Model Prob vs Market Price Scatter ───────────────────────────────────
function ModelScatter({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.scatter.length) return <Empty msg="Need model_probability data." />
  const wins   = data.scatter.filter(s => s.won)
  const losses = data.scatter.filter(s => !s.won)

  const CustomDot = (props: { cx?: number; cy?: number; payload?: ScatterPoint; fill: string }) => {
    const { cx = 0, cy = 0 } = props
    return <circle cx={cx} cy={cy} r={4} fill={props.fill} fillOpacity={0.65} stroke="none" />
  }

  return (
    <div className={CARD}>
      <CardTitle>Model Probability vs Entry Price</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Points above the diagonal = model sees more edge than the market.
        Green = won, Red = lost.
      </p>
      <ResponsiveContainer width="100%" height={260}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis
            type="number" dataKey="marketPrice" name="Market Price"
            domain={[0, 1]} tickFormatter={v => `${(v * 100).toFixed(0)}¢`}
            tick={TICK} axisLine={false} tickLine={false}
            label={{ value: 'Market Price', position: 'insideBottom', offset: -4, fill: '#6b7280', fontSize: 10 }}
          />
          <YAxis
            type="number" dataKey="modelProb" name="Model Prob"
            domain={[0.5, 1]} tickFormatter={v => `${(v * 100).toFixed(0)}%`}
            tick={TICK} axisLine={false} tickLine={false}
            label={{ value: 'Model Prob', angle: -90, position: 'insideLeft', fill: '#6b7280', fontSize: 10 }}
          />
          <Tooltip
            {...tooltipStyle}
            content={({ payload }) => {
              if (!payload?.length) return null
              const p = payload[0].payload as typeof data.scatter[0]
              return (
                <div style={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6, padding: '8px 12px', fontSize: 12 }}>
                  <div className="font-semibold text-white">{p.city}</div>
                  <div className="text-gray-400">Market: {(p.marketPrice * 100).toFixed(1)}¢</div>
                  <div className="text-gray-400">Model: {(p.modelProb * 100).toFixed(1)}%</div>
                  <div className={p.won ? 'text-green-400' : 'text-red-400'}>
                    {p.won ? '✅ Won' : '❌ Lost'} · {p.normPnl >= 0 ? '+' : ''}${p.normPnl.toFixed(2)}
                  </div>
                </div>
              )
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          {/* Diagonal reference line (no-edge) rendered as a scatter series */}
          <Scatter
            name="Wins"
            data={wins}
            fill={GREEN}
            shape={<CustomDot fill={GREEN} />}
          />
          <Scatter
            name="Losses"
            data={losses}
            fill={RED}
            shape={<CustomDot fill={RED} />}
          />
        </ScatterChart>
      </ResponsiveContainer>
      <p className="text-xs text-gray-600 mt-1">
        {wins.length} wins · {losses.length} losses shown
        {data.scatter.length < (data.totalResolved) && ` · ${data.totalResolved - data.scatter.length} trades missing model_probability`}
      </p>
    </div>
  )
}

// ── §5B  Edge Histogram ───────────────────────────────────────────────────────
function EdgeHistogram({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data?.edgeHistogram.length) return <Empty msg="Need model_probability data." />
  return (
    <div className={CARD}>
      <CardTitle>Edge Distribution (Model Prob − Market Price)</CardTitle>
      <p className="text-xs text-gray-600 mb-2">
        Trades with bigger edge gaps should win more. If they don't, the model is miscalibrated.
      </p>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data.edgeHistogram}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis yAxisId="count" tick={TICK} axisLine={false} tickLine={false} />
          <YAxis yAxisId="wr" orientation="right" tick={TICK} axisLine={false} tickLine={false}
            tickFormatter={v => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
          <Tooltip
            {...tooltipStyle}
            formatter={(v: number, name: string) =>
              name === 'Win Rate' ? [`${(v * 100).toFixed(1)}%`, name] : [v, name]
            }
          />
          <Legend wrapperStyle={{ fontSize: 11, color: '#9ca3af' }} />
          <ReferenceLine yAxisId="wr" y={0.5} stroke="#6b7280" strokeDasharray="4 4" />
          <Bar yAxisId="count" dataKey="count" name="Trades" fill={PURPLE} opacity={0.7} radius={[3, 3, 0, 0]} />
          <Line yAxisId="wr" type="monotone" dataKey="winRate" name="Win Rate" stroke={YELLOW} strokeWidth={2} dot={{ r: 4 }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── §5C  Funnel Stats ─────────────────────────────────────────────────────────
function FunnelCards({ data }: { data: ReturnType<typeof useAnalytics>['data'] }) {
  if (!data) return null
  const { funnel } = data
  const cards = [
    { label: 'Phase 1 Signals',        value: funnel.phase1Total.toLocaleString(),                    sub: 'last 90 days',         color: 'text-blue-400'   },
    { label: 'Phase 2 Triggers',        value: funnel.phase2Total.toLocaleString(),                    sub: 'generated from P1',    color: 'text-purple-400' },
    { label: 'Conversion Rate',         value: `${(funnel.conversionPct * 100).toFixed(1)}%`,          sub: 'P1 → P2',              color: 'text-yellow-400' },
    { label: 'Phase 2 Resolved',        value: funnel.phase2Resolved.toLocaleString(),                 sub: 'with outcomes',        color: 'text-green-400'  },
  ]
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {cards.map(c => (
        <div key={c.label} className={`${CARD} text-center`}>
          <div className={`text-2xl font-bold tabular-nums ${c.color}`}>{c.value}</div>
          <div className="text-xs text-white font-semibold mt-1">{c.label}</div>
          <div className="text-xs text-gray-500 mt-0.5">{c.sub}</div>
        </div>
      ))}
    </div>
  )
}

// ── Main tab ──────────────────────────────────────────────────────────────────
export default function AnalyticsTab() {
  const { data, loading, lastRefreshed, refresh } = useAnalytics()

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-500 text-sm">
        Computing analytics…
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="text-xs text-gray-500">
          Calibrated cities only · $45/trade · price cap 30¢ · $350/day budget.
          {data && <span className="ml-2 text-gray-600">{data.totalResolved} Phase 2 trades · 90-day window</span>}
        </div>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <span className="text-xs text-gray-600">Updated {lastRefreshed.toLocaleTimeString()}</span>
          )}
          <button
            onClick={refresh}
            className="text-xs px-2.5 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* §1  Model Quality */}
      <Section
        title="1 · Model Quality & Calibration"
        description="Is the model's confidence meaningful? Are high-confidence trades actually winning more?"
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <CalibrationCurve data={data} />
          <BrierTrend data={data} />
        </div>
        <EdgeDecay data={data} />
      </Section>

      {/* §2  City & Market */}
      <Section
        title="2 · City & Market Breakdown"
        description="Which cities are profitable? Are we biased toward YES or NO? Where in the price curve do we win?"
      >
        <CityEdgeTable data={data} />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <PriceDistrib data={data} />
          <OutcomeBias  data={data} />
        </div>
      </Section>

      {/* §3  P&L Attribution */}
      <Section
        title="3 · P&L Attribution"
        description="Where does our edge actually come from? Confidence tier, time of entry, and holding period."
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <PnLByTier data={data} />
          <PnLByHour data={data} />
        </div>
        <PnLByDays data={data} />
      </Section>

      {/* §4  Risk & Exposure */}
      <Section
        title="4 · Risk & Exposure"
        description="Peak-to-trough drawdown and how much of our daily budget we're actually using."
      >
        <DrawdownChart    data={data} />
        <BudgetUtilization data={data} />
      </Section>

      {/* §5  Signal Health */}
      <Section
        title="5 · Signal Health"
        description="Is the model finding real edge vs market price? How does the funnel look from Phase 1 to Phase 2?"
      >
        <FunnelCards data={data} />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ModelScatter  data={data} />
          <EdgeHistogram data={data} />
        </div>
      </Section>
    </div>
  )
}
