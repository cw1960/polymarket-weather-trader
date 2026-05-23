import type { Strategy } from './types'

const COLORS: Record<string, string> = {
  'Market Maker':       'bg-purple-900 text-purple-300',
  'Whale':              'bg-yellow-900 text-yellow-300',
  'Tail Scalper':       'bg-pink-900 text-pink-300',
  'Weather Specialist': 'bg-blue-900 text-blue-300',
  'Conviction Trader':  'bg-green-900 text-green-300',
  'Diversified':        'bg-gray-700 text-gray-300',
  'Inactive':           'bg-gray-800 text-gray-500',
}

export default function StrategyBadge({ strategy }: { strategy: Strategy }) {
  const color = COLORS[strategy.label] || 'bg-gray-700 text-gray-300'
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <div className="flex items-center gap-3 mb-2">
        <span className="text-xs text-gray-500 uppercase tracking-wider">Strategy Classification</span>
        <span className={`text-xs font-bold px-2 py-1 rounded ${color}`}>{strategy.label}</span>
      </div>
      {strategy.reasons.length > 0 && (
        <ul className="text-sm text-gray-400 space-y-1">
          {strategy.reasons.map((r, i) => <li key={i}>• {r}</li>)}
        </ul>
      )}
    </div>
  )
}
