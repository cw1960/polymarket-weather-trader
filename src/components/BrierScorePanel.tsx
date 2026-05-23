import { useState, useEffect } from 'react'
import supabase from '../lib/supabase'
import { TradeSignal } from '../types'

interface CityScore {
  city: string
  score: number
  count: number
}

export default function BrierScorePanel() {
  const [overallScore, setOverallScore] = useState<number | null>(null)
  const [marketScore, setMarketScore] = useState<number | null>(null)
  const [cityScores, setCityScores] = useState<CityScore[]>([])
  const [totalPredictions, setTotalPredictions] = useState(0)
  const [goLiveReady, setGoLiveReady] = useState(false)
  const [winRate, setWinRate] = useState(0)

  useEffect(() => {
    async function load() {
      const thirtyDaysAgo = new Date()
      thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30)

      const { data } = await supabase
        .from('trade_signals')
        .select('*')
        .gte('signal_time', thirtyDaysAgo.toISOString())
        .not('brier_score', 'is', null)

      const scored = (data ?? []) as TradeSignal[]
      if (scored.length === 0) return

      setTotalPredictions(scored.length)

      const ourAvg = scored.reduce((s, r) => s + (r.brier_score ?? 0), 0) / scored.length
      const marketAvg =
        scored.reduce((s, r) => s + Math.pow(r.market_price - 1, 2), 0) / scored.length

      setOverallScore(ourAvg)
      setMarketScore(marketAvg)

      const byCity = scored.reduce<Record<string, TradeSignal[]>>((acc, r) => {
        acc[r.city] = acc[r.city] ?? []
        acc[r.city].push(r)
        return acc
      }, {})

      setCityScores(
        Object.entries(byCity).map(([city, rows]) => ({
          city,
          score: rows.reduce((s, r) => s + (r.brier_score ?? 0), 0) / rows.length,
          count: rows.length,
        }))
      )

      const traded = scored.filter((r) => r.traded && r.actual_outcome !== null)
      const wins = traded.filter(
        (r) => (r.side === 'YES' && r.actual_outcome) || (r.side === 'NO' && !r.actual_outcome)
      )
      const wr = traded.length > 0 ? (wins.length / traded.length) * 100 : 0
      setWinRate(wr)

      const worstCity = Math.max(...Object.values(byCity).map((rows) =>
        rows.reduce((s, r) => s + (r.brier_score ?? 0), 0) / rows.length
      ))
      setGoLiveReady(
        scored.length >= 200 && ourAvg < 0.15 && worstCity <= 0.22 && wr > 65
      )
    }
    load()
  }, [])

  function scoreColor(s: number) {
    if (s < 0.15) return 'text-green-400'
    if (s <= 0.2) return 'text-yellow-400'
    return 'text-red-400'
  }

  // Don't render until there is scored data — brier_score is only written once
  // the resolver has run AND the signal_engine has been updated to compute it.
  if (overallScore == null) return null

  const barWidth = Math.min((overallScore / 0.25) * 100, 100)

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-gray-400 text-sm font-semibold uppercase tracking-wider">
          Model Calibration
        </h2>
        <span
          className={`text-xs px-2 py-0.5 rounded font-bold ${
            goLiveReady ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-400'
          }`}
        >
          {goLiveReady ? 'GO LIVE READY' : 'NOT READY'}
        </span>
      </div>

      <div className="flex items-end gap-4 mb-3">
        <div>
          <div className={`text-4xl font-bold ${scoreColor(overallScore)}`}>
            {overallScore.toFixed(3)}
          </div>
          <div className="text-xs text-gray-500">Brier Score (lower is better)</div>
        </div>
        {marketScore != null && (
          <div className="text-sm text-gray-400 mb-1">
            Market avg: {marketScore.toFixed(3)} · Our model:{' '}
            <span className="text-green-400">
              {Math.round((1 - overallScore / marketScore) * 100)}% better
            </span>
          </div>
        )}
      </div>

      <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
        <span>0</span>
        <span>Target: 0.15</span>
        <span>0.25</span>
      </div>
      <div className="relative h-2 bg-gray-700 rounded mb-3">
        <div
          className={`h-2 rounded ${overallScore < 0.15 ? 'bg-green-500' : overallScore <= 0.2 ? 'bg-yellow-500' : 'bg-red-500'}`}
          style={{ width: `${barWidth}%` }}
        />
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-white opacity-50"
          style={{ left: `${(0.15 / 0.25) * 100}%` }}
        />
      </div>

      <div className="text-xs text-gray-500 mb-3">
        Based on {totalPredictions} predictions · Win rate: {winRate.toFixed(1)}%
      </div>

      <div className="space-y-1">
        {cityScores.map(({ city, score, count }) => (
          <div key={city} className="flex items-center gap-2 text-xs">
            <span className="text-gray-400 w-20">{city}</span>
            <div className="flex-1 h-1.5 bg-gray-700 rounded">
              <div
                className={`h-1.5 rounded ${score < 0.15 ? 'bg-green-500' : score <= 0.22 ? 'bg-yellow-500' : 'bg-red-500'}`}
                style={{ width: `${Math.min((score / 0.25) * 100, 100)}%` }}
              />
            </div>
            <span className={`w-12 text-right font-mono ${scoreColor(score)}`}>
              {score.toFixed(3)}
            </span>
            <span className={`${score > 0.22 ? 'text-yellow-400' : 'text-gray-600'}`}>
              {score > 0.22 ? '⚠' : '✓'}
            </span>
            <span className="text-gray-600 w-10">({count})</span>
          </div>
        ))}
      </div>
    </div>
  )
}
