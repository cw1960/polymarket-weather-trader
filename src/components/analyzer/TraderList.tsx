import { useEffect, useState } from 'react'
import {
  deleteTrader, fetchHistory, fetchWatchlist,
  setAnnotations, setWatchlistLabel, unfollowWallet,
} from './api'
import type { RunSummary, WatchlistEntry } from './types'

function fmtUsd(n: number): string {
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`
  return `${sign}$${abs.toFixed(0)}`
}

function fmtAge(iso: string | null): string {
  if (!iso) return '—'
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function shortAddr(w: string): string {
  return w.slice(0, 6) + '…' + w.slice(-4)
}

const STRATEGY_COLOR: Record<string, string> = {
  'Market Maker':       'text-purple-300',
  'Whale':              'text-yellow-300',
  'Tail Scalper':       'text-pink-300',
  'Weather Specialist': 'text-blue-300',
  'Conviction Trader':  'text-green-300',
  'Diversified':        'text-gray-300',
  'Inactive':           'text-gray-500',
}

function Row({
  entry, isFollowed, onClick, onUnfollow, onRename, onHeadline, onDelete,
}: {
  entry: RunSummary
  isFollowed?: boolean
  onClick: () => void
  onUnfollow?: () => void
  onRename?: (label: string) => void
  onHeadline?: (headline: string) => void
  onDelete?: () => void
}) {
  const display = entry.username || entry.pseudonym || shortAddr(entry.wallet)
  const strategyClr = STRATEGY_COLOR[entry.strategy_label] || 'text-gray-400'
  const pnlClr = entry.net_cashflow_usd >= 0 ? 'text-green-400' : 'text-red-400'

  const labelRaw    = (entry as WatchlistEntry).label
  const headlineRaw = entry.headline || ''
  const [editing, setEditing] = useState(false)
  const [labelDraft, setLabelDraft] = useState(labelRaw || '')
  const [editingHeadline, setEditingHeadline] = useState(false)
  const [headlineDraft, setHeadlineDraft] = useState(headlineRaw)

  return (
    <div className="bg-gray-800 hover:bg-gray-700/60 border border-gray-700 rounded-lg p-3 transition-colors">
      <div className="flex items-start justify-between gap-2">
        <button onClick={onClick} className="text-left flex-1 min-w-0">
          <div className="flex items-baseline gap-2 mb-1">
            <span className="text-sm font-semibold text-white truncate">{display}</span>
            {entry.strategy_label && (
              <span className={`text-xs ${strategyClr}`}>{entry.strategy_label}</span>
            )}
          </div>
          <div className="text-xs text-gray-500 font-mono truncate">{shortAddr(entry.wallet)}</div>
          {labelRaw && !editing && (
            <div className="text-xs text-blue-300 italic mt-1 truncate">{labelRaw}</div>
          )}
          {editing && (
            <input
              autoFocus
              value={labelDraft}
              onChange={(e) => setLabelDraft(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') {
                  onRename?.(labelDraft)
                  setEditing(false)
                } else if (e.key === 'Escape') {
                  setLabelDraft(labelRaw || '')
                  setEditing(false)
                }
              }}
              onBlur={() => { onRename?.(labelDraft); setEditing(false) }}
              placeholder="short label…"
              className="mt-1 w-full bg-gray-900 text-blue-200 text-xs px-1 py-0.5 rounded border border-gray-600 focus:outline-none focus:border-blue-500"
            />
          )}

          {/* HEADLINE — short personal tag that sits on every card for
              fast scanning.  Stored in `analyzer_annotations` keyed by
              wallet — does NOT require the trader to be followed. */}
          {editingHeadline ? (
            <input
              autoFocus
              value={headlineDraft}
              onChange={(e) => setHeadlineDraft(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') {
                  onHeadline?.(headlineDraft)
                  setEditingHeadline(false)
                } else if (e.key === 'Escape') {
                  setHeadlineDraft(headlineRaw)
                  setEditingHeadline(false)
                }
              }}
              onBlur={() => { onHeadline?.(headlineDraft); setEditingHeadline(false) }}
              placeholder='headline (e.g. "👀 fade candidate")'
              maxLength={80}
              className="mt-2 w-full bg-gray-900 text-amber-100 text-sm font-semibold px-2 py-1 rounded border border-amber-600/50 focus:outline-none focus:border-amber-400"
            />
          ) : (
            <div
              onClick={(e) => { e.stopPropagation(); setEditingHeadline(true) }}
              className={
                headlineRaw
                  ? "mt-2 px-2 py-1 rounded bg-amber-900/30 border border-amber-700/40 text-amber-200 text-sm font-semibold cursor-text hover:bg-amber-900/50 transition-colors"
                  : "mt-2 px-2 py-1 rounded border border-dashed border-gray-600 text-gray-500 text-xs italic cursor-text hover:border-gray-500 hover:text-gray-400 transition-colors"
              }
              title="Click to edit headline"
            >
              {headlineRaw || "+ add headline"}
            </div>
          )}
        </button>
        <div className="flex flex-col gap-1">
          {isFollowed && (
            <>
              <button
                onClick={(e) => { e.stopPropagation(); setEditing((v) => !v) }}
                className="text-xs text-gray-500 hover:text-gray-300"
                title="Edit note"
              >
                ✎
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onUnfollow?.() }}
                className="text-xs text-gray-500 hover:text-yellow-400"
                title="Unfollow (keeps run history)"
              >
                ✕
              </button>
            </>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation()
              const name = entry.username || entry.pseudonym || shortAddr(entry.wallet)
              if (confirm(`Permanently delete all analyses for ${name}?\n\nThis removes the trader from history and watchlist. Cannot be undone.`)) {
                onDelete?.()
              }
            }}
            className="text-xs text-gray-500 hover:text-red-400"
            title="Delete all analyses for this trader"
          >
            🗑
          </button>
        </div>
      </div>
      <button onClick={onClick} className="block w-full text-left mt-2">
        <div className="flex gap-3 text-xs font-mono text-gray-400">
          <span>trades=<span className="text-gray-200">{entry.total_trades.toLocaleString()}</span></span>
          <span>mkts=<span className="text-gray-200">{entry.unique_markets.toLocaleString()}</span></span>
          <span>cash=<span className={pnlClr}>{fmtUsd(entry.net_cashflow_usd)}</span></span>
          {entry.weather_share >= 0.5 && (
            <span className="text-blue-300">{Math.round(entry.weather_share * 100)}% wx</span>
          )}
        </div>
        <div className="text-xs text-gray-500 mt-1">analyzed {fmtAge(entry.fetched_at)}</div>
      </button>
    </div>
  )
}

interface Props {
  onSelect: (wallet: string) => void
  refreshKey: number   // bump to force reload after analyze
}

export default function TraderList({ onSelect, refreshKey }: Props) {
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([])
  const [history, setHistory] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'watchlist' | 'history'>('watchlist')

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      try {
        const [w, h] = await Promise.all([fetchWatchlist(), fetchHistory(50)])
        if (cancelled) return
        setWatchlist(w.entries)
        setHistory(h.runs)
      } catch (_e) {
        /* silent — list is non-critical */
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [refreshKey])

  async function handleUnfollow(wallet: string) {
    try {
      await unfollowWallet(wallet)
      setWatchlist((prev) => prev.filter((e) => e.wallet !== wallet))
    } catch (_e) { /* show error inline? minor */ }
  }

  async function handleRename(wallet: string, label: string) {
    try {
      await setWatchlistLabel(wallet, label)
      setWatchlist((prev) => prev.map((e) => e.wallet === wallet ? { ...e, label } : e))
    } catch (_e) { /* */ }
  }

  async function handleHeadline(wallet: string, headline: string) {
    try {
      await setAnnotations(wallet, { headline })
      // Update both lists so the edit shows in whichever tab is active
      setWatchlist((prev) => prev.map((e) => e.wallet === wallet ? { ...e, headline } : e))
      setHistory((prev)   => prev.map((e) => e.wallet === wallet ? { ...e, headline } : e))
    } catch (_e) { /* */ }
  }

  async function handleDelete(wallet: string) {
    try {
      await deleteTrader(wallet)
      setWatchlist((prev) => prev.filter((e) => e.wallet !== wallet))
      setHistory((prev) => prev.filter((e) => e.wallet !== wallet))
    } catch (_e) { /* */ }
  }

  const watchedWallets = new Set(watchlist.map((w) => w.wallet))
  const historyFiltered = history.filter((h) => !watchedWallets.has(h.wallet))
  const rows = tab === 'watchlist' ? watchlist : historyFiltered

  return (
    <div className="bg-gray-850 rounded-lg border border-gray-700 p-3 mb-4">
      <div className="flex items-center gap-1 mb-3">
        <button
          onClick={() => setTab('watchlist')}
          className={`text-xs font-semibold px-3 py-1 rounded-l ${
            tab === 'watchlist' ? 'bg-blue-700 text-white' : 'bg-gray-700 text-gray-400 hover:text-gray-200'
          }`}
        >
          ⭐ Watchlist
          {watchlist.length > 0 && <span className="ml-1.5 text-gray-300">({watchlist.length})</span>}
        </button>
        <button
          onClick={() => setTab('history')}
          className={`text-xs font-semibold px-3 py-1 rounded-r ${
            tab === 'history' ? 'bg-blue-700 text-white' : 'bg-gray-700 text-gray-400 hover:text-gray-200'
          }`}
        >
          🕓 Recent
          {historyFiltered.length > 0 && <span className="ml-1.5 text-gray-300">({historyFiltered.length})</span>}
        </button>
      </div>

      {loading && <div className="text-xs text-gray-500">Loading…</div>}

      {!loading && rows.length === 0 && (
        <div className="text-xs text-gray-500 italic">
          {tab === 'watchlist'
            ? 'No traders followed yet. Analyze a wallet, then click the ⭐ to follow.'
            : 'No prior analyses yet.'}
        </div>
      )}

      {!loading && rows.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 max-h-96 overflow-y-auto">
          {rows.map((entry) => (
            <Row
              key={entry.wallet}
              entry={entry}
              isFollowed={tab === 'watchlist'}
              onClick={() => onSelect(entry.wallet)}
              onUnfollow={tab === 'watchlist' ? () => handleUnfollow(entry.wallet) : undefined}
              onRename={tab === 'watchlist' ? (l) => handleRename(entry.wallet, l) : undefined}
              onHeadline={(h) => handleHeadline(entry.wallet, h)}
              onDelete={() => handleDelete(entry.wallet)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
