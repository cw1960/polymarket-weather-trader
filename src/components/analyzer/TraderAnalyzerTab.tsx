import { useState } from 'react'
import { analyze, clearToken, deleteTrader, getToken } from './api'
import type { AnalyzerResponse } from './types'
import PassphraseGate from './PassphraseGate'
import ProfileStatsPanel from './ProfileStats'
import ByDayTable from './ByDayTable'
import StrategyBadge from './StrategyBadge'
import OpenPositionsTable from './OpenPositionsTable'
import WeatherDissectionPanel from './WeatherDissection'
import Commentary from './Commentary'
import TraderList from './TraderList'
import FollowButton from './FollowButton'
import TraderNotesPanel from './TraderNotesPanel'

export default function TraderAnalyzerTab() {
  const [authed, setAuthed] = useState<boolean>(!!getToken())
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [data, setData] = useState<AnalyzerResponse | null>(null)
  const [listRefreshKey, setListRefreshKey] = useState(0)
  const [stage, setStage] = useState('')
  const [detail, setDetail] = useState('')

  async function run(forceRefresh = false, walletOverride?: string) {
    const target = (walletOverride ?? input).trim()
    if (!target) return
    setError('')
    setLoading(true)
    setData(null)
    setProgress(0)
    setStage('starting…')
    setDetail('')
    try {
      const result = await analyze(target, {
        forceRefresh,
        onProgress: (s) => {
          setProgress(s.progress_pct)
          setStage(s.stage || s.status)
          setDetail(s.detail || '')
        },
      })
      setData(result)
      setInput('')
      setListRefreshKey((k) => k + 1)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setStage('')
      setDetail('')
    }
  }

  function handleSelectFromList(wallet: string) {
    run(false, wallet)
    // Scroll back to the top so user sees the loading bar + result, not
    // the list they clicked from.
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  if (!authed) return <PassphraseGate onAuthed={() => setAuthed(true)} />

  return (
    <div className="space-y-4">
      {/* Search bar — always at the top so it's never lost behind results */}
      <div className="bg-gray-800 border-2 border-blue-700/40 rounded-lg p-4 shadow-lg shadow-blue-900/20">
        <label className="block text-xs font-semibold text-blue-300 uppercase tracking-wider mb-2">
          Analyze a Polymarket trader
        </label>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') run() }}
            placeholder="Username (e.g. fridius2) or 0x wallet address"
            className="flex-1 bg-gray-900 text-white text-sm px-3 py-2 rounded border border-gray-600 focus:outline-none focus:border-blue-500 font-mono"
          />
          <button
            onClick={() => run(false)}
            disabled={!input.trim() || loading}
            className="text-sm px-4 py-2 rounded bg-blue-700 hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold transition-colors"
          >
            {loading ? 'Analyzing…' : 'Analyze'}
          </button>
          {data && (
            <button
              onClick={() => run(true, data.identity.address)}
              disabled={loading}
              className="text-sm px-3 py-2 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-gray-300 transition-colors"
              title="Re-fetch current trader from Polymarket (bypass cache)"
            >
              ↻ Refresh
            </button>
          )}
          {data && (
            <button
              onClick={() => { setData(null); setInput(''); window.scrollTo({ top: 0, behavior: 'smooth' }) }}
              className="text-sm px-3 py-2 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
              title="Clear current result"
            >
              ✕ Clear
            </button>
          )}
          {data && (
            <button
              onClick={async () => {
                const name = data.identity.username || data.identity.pseudonym || data.identity.address.slice(0, 10) + '…'
                if (!confirm(`Permanently delete all analyses for ${name}?\n\nThis removes the trader from history and watchlist. Cannot be undone.`)) return
                try {
                  await deleteTrader(data.identity.address)
                  setData(null)
                  setInput('')
                  setListRefreshKey((k) => k + 1)
                  window.scrollTo({ top: 0, behavior: 'smooth' })
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e))
                }
              }}
              className="text-sm px-3 py-2 rounded bg-red-900 hover:bg-red-800 text-red-200 transition-colors"
              title="Permanently delete all data for this trader"
            >
              🗑 Delete
            </button>
          )}
          <button
            onClick={() => { clearToken(); setAuthed(false); setData(null) }}
            className="text-xs px-2 py-2 rounded text-gray-500 hover:text-gray-300"
            title="Lock"
          >
            🔒
          </button>
        </div>
        {loading && (
          <div className="mt-3">
            <div className="h-1.5 bg-gray-900 rounded overflow-hidden">
              <div
                className="h-full bg-blue-600 transition-all duration-300"
                style={{ width: `${Math.max(progress, 1)}%` }}
              />
            </div>
            <div className="text-xs text-gray-500 mt-1 flex items-center gap-2">
              <span className="text-blue-300 font-semibold">{progress.toFixed(0)}%</span>
              <span className="text-gray-300">{stage || 'starting…'}</span>
              {detail && <span className="text-gray-500 italic">— {detail}</span>}
            </div>
          </div>
        )}
        {error && <div className="text-xs text-red-400 mt-2">{error}</div>}
      </div>

      {/* Watchlist + Recent */}
      <TraderList onSelect={handleSelectFromList} refreshKey={listRefreshKey} />

      {data && (
        <>
          <div className="text-xs text-gray-500 flex gap-3">
            <span>run_id: <span className="font-mono text-gray-400">{data.run_id}</span></span>
            <span>fetched: <span className="font-mono text-gray-400">{data.meta.fetch_ms}ms</span></span>
            <span>trades: <span className="font-mono text-gray-400">{data.meta.trade_count.toLocaleString()}</span></span>
            {data.from_cache && <span className="text-yellow-500">⚡ from cache</span>}
            {data.meta.activity_truncated && <span className="text-red-400">⚠ activity truncated at API cap</span>}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2 relative">
              <div className="absolute top-3 right-3 z-10">
                <FollowButton
                  wallet={data.identity.address}
                  onChange={() => setListRefreshKey((k) => k + 1)}
                />
              </div>
              <ProfileStatsPanel identity={data.identity} stats={data.stats} />
            </div>
            <div>
              <StrategyBadge strategy={data.strategy} />
            </div>
          </div>
          <ByDayTable rows={data.by_day} />
          <WeatherDissectionPanel data={data.weather_dissection} />
          <OpenPositionsTable positions={data.open_positions} />
          {/* Personal notes — only shown for followed wallets */}
          <TraderNotesPanel wallet={data.identity.address} />
          <Commentary runId={data.run_id} />
        </>
      )}
    </div>
  )
}
