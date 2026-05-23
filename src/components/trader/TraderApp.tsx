// Top-level Trader application. Sub-routes:
//   • watchlist     — Page 1, default landing
//   • trade-station — Page 2, opened by clicking a watchlist tile
//   • backtest      — Page 3 (built later)
//
// State for "which (city, date) is currently selected" lives here so the
// Watchlist can deep-link into Trade Station without needing a full router.

import { useState } from 'react'
import Watchlist from './Watchlist'
import TradeStation from './TradeStation'
import Backtest from './Backtest'

type TraderTab = 'watchlist' | 'trade-station' | 'backtest'


export default function TraderApp() {
  const [tab, setTab] = useState<TraderTab>('watchlist')
  const [selected, setSelected] = useState<{ city: string; forecastDate: string } | null>(null)

  function handleSelectMarket(city: string, forecastDate: string) {
    setSelected({ city, forecastDate })
    setTab('trade-station')
  }

  return (
    <div className="text-white">
      {/* Trader sub-nav */}
      <div className="border-b border-gray-800 px-6 py-2 flex items-center gap-4">
        <button
          onClick={() => setTab('watchlist')}
          className={`text-xs px-3 py-1.5 rounded ${tab === 'watchlist' ? 'bg-cyan-900/60 text-cyan-200 font-medium' : 'text-gray-400 hover:text-gray-200'}`}
        >
          📊 Watchlist
        </button>
        <button
          onClick={() => setTab('trade-station')}
          disabled={!selected}
          className={`text-xs px-3 py-1.5 rounded ${tab === 'trade-station' ? 'bg-cyan-900/60 text-cyan-200 font-medium' : selected ? 'text-gray-400 hover:text-gray-200' : 'text-gray-700 cursor-not-allowed'}`}
        >
          💹 Trade Station{selected ? ` — ${selected.city} ${selected.forecastDate}` : ''}
        </button>
        <button
          onClick={() => setTab('backtest')}
          className={`text-xs px-3 py-1.5 rounded ${tab === 'backtest' ? 'bg-cyan-900/60 text-cyan-200 font-medium' : 'text-gray-400 hover:text-gray-200'}`}
        >
          🔬 Backtest
        </button>
      </div>

      {tab === 'watchlist' && <Watchlist onSelectMarket={handleSelectMarket} />}
      {tab === 'trade-station' && (
        selected
          ? <TradeStation
              city={selected.city}
              forecastDate={selected.forecastDate}
              onBack={() => setTab('watchlist')}
            />
          : <div className="p-6 text-gray-500 text-sm">Select a market from the Watchlist first.</div>
      )}
      {tab === 'backtest' && <Backtest />}
    </div>
  )
}
