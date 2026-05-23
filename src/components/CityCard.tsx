import { TradeSignal } from '../types'

interface Props {
  city: string
  signals: TradeSignal[]
}

export default function CityCard({ city, signals }: Props) {
  const best = signals[0]

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-white font-bold">{city}</span>
        <span className="text-xs text-gray-500">{signals.length} signal{signals.length !== 1 ? 's' : ''}</span>
      </div>
      {best ? (
        <div className="text-sm text-gray-400">
          Best edge:{' '}
          <span className="text-yellow-400 font-semibold">+{Math.round(best.edge * 100)}pts</span>{' '}
          on {best.outcome}
        </div>
      ) : (
        <div className="text-sm text-gray-600">No signals today</div>
      )}
    </div>
  )
}
