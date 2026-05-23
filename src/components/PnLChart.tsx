import {
  ResponsiveContainer,
  Area,
  AreaChart,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import { DailyPoint } from '../hooks/useTrades'

interface Props {
  series: DailyPoint[]
  startingBankroll?: number
}

export default function PnLChart({ series, startingBankroll = 1000 }: Props) {
  if (series.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <div className="text-xs text-gray-500 uppercase tracking-wider font-semibold mb-1">Bankroll History</div>
        <p className="text-gray-500 text-sm">No resolved trades yet — chart will populate as markets close.</p>
      </div>
    )
  }

  const current  = series[series.length - 1].value
  const isProfit = current >= startingBankroll
  const pct      = (((current - startingBankroll) / startingBankroll) * 100).toFixed(1)
  const lineColor = isProfit ? '#22c55e' : '#ef4444'

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs text-gray-500 uppercase tracking-wider font-semibold">Bankroll History</div>
        <div className="text-sm text-gray-400">
          ${startingBankroll.toLocaleString()} →{' '}
          <span className="font-bold text-white">${current.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          {'  '}
          <span className={isProfit ? 'text-green-400' : 'text-red-400'}>
            {isProfit ? '+' : ''}{pct}%
          </span>
          <span className="text-gray-600 ml-2">· {series.length} days</span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={series} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={lineColor} stopOpacity={0.18} />
              <stop offset="95%" stopColor={lineColor} stopOpacity={0}    />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="date"
            tick={{ fill: '#6b7280', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: '#6b7280', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v.toLocaleString()}`}
            width={72}
            domain={['auto', 'auto']}
          />
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 6 }}
            labelStyle={{ color: '#9ca3af' }}
            formatter={(v: number, name: string) => {
              if (name === 'value') return [`$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, 'Bankroll']
              const sign = v >= 0 ? '+' : ''
              return [`${sign}$${v.toFixed(2)}`, 'Day P&L']
            }}
          />
          <ReferenceLine y={startingBankroll} stroke="#4b5563" strokeDasharray="4 4" />
          <Area
            type="monotone"
            dataKey="value"
            stroke={lineColor}
            strokeWidth={2}
            fill="url(#pnlGrad)"
            dot={series.length <= 14}
            activeDot={{ r: 4 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
