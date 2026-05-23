import { useState, useEffect } from 'react'
import { useSignals } from './hooks/useSignals'
import { useTrades } from './hooks/useTrades'
import { useBankroll } from './hooks/useBankroll'
import { useLadders } from './hooks/useLadders'
import { Trade } from './types'
// Note: useBankroll kept for lastRefreshed staleness tracking
import BrierScorePanel from './components/BrierScorePanel'
import PnLChart from './components/PnLChart'
import BankrollPanel from './components/BankrollPanel'
import CalibrationPanel from './components/CalibrationPanel'
import ShadowTrackerPanel from './components/ShadowTrackerPanel'
import ExitSimPanel from './components/ExitSimPanel'
import ExecutionTelemetryPanel from './components/ExecutionTelemetryPanel'
import { usePrecisionMetrics } from './hooks/usePrecisionMetrics'
import { useShadowTracker } from './hooks/useShadowTracker'
import { useExitSim } from './hooks/useExitSim'
import { useExecutionTelemetry } from './hooks/useExecutionTelemetry'
import LadderPanel from './components/LadderPanel'
import CityPerformancePanel from './components/CityPerformancePanel'
import TradeTable from './components/TradeTable'
import TradeDrawer from './components/TradeDrawer'
import ReportsTab from './components/ReportsTab'
import NotificationsTab from './components/NotificationsTab'
import AnalyticsTab from './components/AnalyticsTab'
import TraderAnalyzerTab from './components/analyzer/TraderAnalyzerTab'
import NextResolutionBadge from './components/NextResolutionBadge'
import MissionControl from './components/MissionControl'
import { useMissionControl } from './hooks/useMissionControl'
import TraderApp from './components/trader/TraderApp'
import supabase from './lib/supabase'

type AppTab = 'trader' | 'mission-control' | 'dashboard' | 'reports' | 'notifications' | 'analytics' | 'trader-analyzer'

function utcClock(): string {
  return new Date().toUTCString().slice(17, 25) + ' UTC'
}

function secondsSince(d: Date | null): number {
  if (!d) return 0
  return Math.floor((Date.now() - d.getTime()) / 1000)
}

function freshnessLabel(secs: number): string {
  if (secs < 5) return 'just now'
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

export default function App() {
  const { signals, loading: sigLoading, refresh: refreshSignals, lastRefreshed: sigRefreshed } = useSignals()
  const { openTrades, tradeHistory, totalPnl, todayPnl, winRate, dailySeries, cityStats, loading: tradeLoading, lastRefreshed: tradeRefreshed,
          normTradeHistory, normTotalPnl, normTodayPnl, normWinRate, normDailySeries } = useTrades()
  const { liveBalance, liveStartingBankroll, loading: bankLoading, lastRefreshed: bankRefreshed } = useBankroll()
  const { ladders, lastRefreshed: ladderRefreshed } = useLadders()
  const { metrics: precisionMetrics, calibration: cityCalibration } = usePrecisionMetrics()
  const { shadow } = useShadowTracker()
  const { summary: exitSim } = useExitSim()
  const { stats: execTelemetry } = useExecutionTelemetry()

  const [activeTab, setActiveTab] = useState<AppTab>('trader')
  // Authoritative mode comes from useMissionControl (which derives from guardrails).
  // The static settings.trading_mode flag is still loaded above for backward
  // compatibility, but the badge below ignores it.
  const mcForBadge = useMissionControl()
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(null)
  const [tradingMode, setTradingMode] = useState<'PAPER' | 'LIVE'>('PAPER')
  const [utcNow, setUtcNow] = useState(utcClock())
  const [tick, setTick] = useState(0)
  const [confirmInput, setConfirmInput] = useState('')
  const [showConfirm, setShowConfirm] = useState(false)
  const [killSwitchActive, setKillSwitchActive] = useState(false)
  const [baselineDate, setBaselineDate] = useState<string | null>(null)
  const [normalizePhase1, setNormalizePhase1] = useState(true)

  // Latest refresh across all hooks
  const latestRefresh = [sigRefreshed, tradeRefreshed, bankRefreshed, ladderRefreshed]
    .filter(Boolean)
    .reduce<Date | null>((max, d) => (!max || (d && d > max) ? d! : max), null)

  useEffect(() => {
    const interval = setInterval(() => {
      setUtcNow(utcClock())
      setTick((t) => t + 1)
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    async function loadSettings() {
      const { data } = await supabase.from('settings').select('key, value')
      const rows = (data ?? []) as { key: string; value: string }[]
      const mode = rows.find((r) => r.key === 'trading_mode')
      const ks   = rows.find((r) => r.key === 'kill_switch')
      const bd   = rows.find((r) => r.key === 'baseline_date')
      if (mode) setTradingMode(mode.value === 'live' ? 'LIVE' : 'PAPER')
      if (ks)   setKillSwitchActive(ks.value === 'true')
      if (bd)   setBaselineDate(bd.value)
    }
    loadSettings()
  }, [])

  async function handleKillSwitch() {
    const newVal = !killSwitchActive
    await supabase.from('settings').update({ value: String(newVal) }).eq('key', 'kill_switch')
    setKillSwitchActive(newVal)
  }

  async function handleModeSwitch() {
    if (tradingMode === 'PAPER') {
      setShowConfirm(true)
    } else {
      await supabase.from('settings').update({ value: 'paper' }).eq('key', 'trading_mode')
      setTradingMode('PAPER')
    }
  }

  async function confirmLive() {
    if (confirmInput === 'CONFIRM') {
      await supabase.from('settings').update({ value: 'live' }).eq('key', 'trading_mode')
      setTradingMode('LIVE')
      setShowConfirm(false)
      setConfirmInput('')
    }
  }

  function handleRefresh() {
    refreshSignals()
  }

  const loading = sigLoading || tradeLoading || bankLoading
  const staleness = secondsSince(latestRefresh)

  // Pick raw or normalized trade data based on toggle
  const displayHistory     = normalizePhase1 ? normTradeHistory : tradeHistory
  const displayTotalPnl    = normalizePhase1 ? normTotalPnl     : totalPnl
  const displayTodayPnl    = normalizePhase1 ? normTodayPnl     : todayPnl
  const displayWinRate     = normalizePhase1 ? normWinRate      : winRate
  const displayDailySeries = normalizePhase1 ? normDailySeries  : dailySeries
  // tick dependency forces re-render every second so the staleness label stays live
  void tick

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <div className="border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold tracking-tight">Weather Trader</h1>
          <span
            className={`text-xs px-2 py-0.5 rounded font-bold ${
              mcForBadge.effectiveTradingMode === 'PAPER' ? 'bg-orange-900 text-orange-300' : 'bg-green-900 text-green-300'
            }`}
            title="Derived from guardrail state (Mission Control). Not the static settings.trading_mode flag."
          >
            {mcForBadge.effectiveTradingMode} MODE
          </span>
          {killSwitchActive && (
            <span className="text-xs px-2 py-0.5 rounded font-bold bg-red-900 text-red-300">
              KILL SWITCH ON
            </span>
          )}
        </div>
        <div className="flex items-center gap-4">
          <NextResolutionBadge />
          <span className="text-gray-700">|</span>
          <span className="text-xs text-gray-500">
            {latestRefresh ? `Updated ${freshnessLabel(staleness)}` : 'Loading...'}
          </span>
          <span className="text-xs text-gray-600">{utcNow}</span>
          <button
            onClick={handleRefresh}
            className="text-xs px-2.5 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Tab navigation */}
      <div className="border-b border-gray-800 px-6 flex items-center gap-1">
        {([['trader', '💹 Trader'], ['mission-control', 'Mission Control'], ['dashboard', 'Dashboard'], ['reports', 'Reports'], ['notifications', 'Notifications'], ['analytics', 'Analytics'], ['trader-analyzer', 'Trader Analyzer']] as [AppTab, string][]).map(([tab, label]) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
              activeTab === tab
                ? 'border-blue-500 text-white'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Trader tab — new manual-trading app (added 2026-05-22) */}
      {activeTab === 'trader' && <TraderApp />}

      {/* Mission Control tab */}
      {activeTab === 'mission-control' && <MissionControl />}

      {/* Reports tab */}
      {activeTab === 'reports' && (
        <div className="px-6 py-4">
          <ReportsTab normalizePhase1={normalizePhase1} />
        </div>
      )}

      {/* Notifications tab */}
      {activeTab === 'notifications' && (
        <div className="px-6 py-4">
          <NotificationsTab />
        </div>
      )}

      {/* Analytics tab */}
      {activeTab === 'analytics' && (
        <div className="px-6 py-4">
          <AnalyticsTab />
        </div>
      )}

      {/* Trader Analyzer tab */}
      {activeTab === 'trader-analyzer' && (
        <div className="px-6 py-4">
          <TraderAnalyzerTab />
        </div>
      )}

      {/* Dashboard tab */}
      {activeTab === 'dashboard' && <div className="px-6 py-4 space-y-4">
        {/* Metric Cards */}
        <BankrollPanel
          totalPnl={displayTotalPnl}
          todayPnl={displayTodayPnl}
          openPositions={openTrades.length}
          winRate={displayWinRate}
          resolvedCount={displayHistory.length}
          liveBalance={liveBalance}
          liveStartingBankroll={liveStartingBankroll}
        />

        {/* Positions — moved up to sit beneath the Bankroll metrics so you can
            see balance, P&L, and current exposure together without scrolling. */}
        <div>
          <div className="mb-2">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
              Positions
            </h2>
          </div>
          <TradeTable
            open={openTrades}
            history={displayHistory}
            onSelect={setSelectedTrade}
          />
        </div>

        {/* Precision metrics + calibration table */}
        <CalibrationPanel
          metrics={precisionMetrics}
          calibration={cityCalibration}
        />

        {/* Execution telemetry — live fill quality (real money only) */}
        <ExecutionTelemetryPanel stats={execTelemetry} />

        {/* Shadow tracker — hypothetical performance at higher price caps */}
        <ShadowTrackerPanel shadow={shadow} />

        {/* Exit simulation — hypothetical post-lock exit strategies */}
        <ExitSimPanel summary={exitSim} />

        {/* Trading Mode Control */}
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold text-gray-300 mb-1">Trading Mode</div>
              <div className="text-xs text-gray-500">
                Current:{' '}
                <span className={tradingMode === 'PAPER' ? 'text-orange-400' : 'text-green-400'}>
                  {tradingMode}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setNormalizePhase1((v) => !v)}
                className={`text-xs px-3 py-1.5 rounded font-semibold transition-colors ${
                  normalizePhase1
                    ? 'bg-blue-700 hover:bg-blue-600 text-white'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                }`}
                title="P1=observation ($0.01), P2 rescaled to $150/day budget with $20 cap"
              >
                {normalizePhase1 ? 'New Model View: ON' : 'New Model View: OFF'}
              </button>
              <button
                onClick={handleKillSwitch}
                className={`text-xs px-3 py-1.5 rounded font-semibold transition-colors ${
                  killSwitchActive
                    ? 'bg-red-700 hover:bg-red-600 text-white'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                }`}
              >
                {killSwitchActive ? 'Kill Switch: ON' : 'Kill Switch: OFF'}
              </button>
              <button
                onClick={handleModeSwitch}
                className="text-xs px-3 py-1.5 rounded font-semibold bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
              >
                Switch to {tradingMode === 'PAPER' ? 'LIVE' : 'PAPER'}
              </button>
            </div>
          </div>
          {showConfirm && (
            <div className="mt-3 flex items-center gap-2">
              <input
                type="text"
                placeholder='Type "CONFIRM" to enable live trading'
                value={confirmInput}
                onChange={(e) => setConfirmInput(e.target.value)}
                className="flex-1 bg-gray-700 text-white text-sm px-3 py-1.5 rounded border border-gray-600 focus:outline-none focus:border-red-500"
              />
              <button
                onClick={confirmLive}
                disabled={confirmInput !== 'CONFIRM'}
                className="text-xs px-3 py-1.5 rounded font-semibold bg-red-700 hover:bg-red-600 text-white disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Confirm
              </button>
              <button
                onClick={() => {
                  setShowConfirm(false)
                  setConfirmInput('')
                }}
                className="text-xs px-3 py-1.5 rounded font-semibold bg-gray-700 text-gray-400"
              >
                Cancel
              </button>
            </div>
          )}
        </div>

        {/* Brier Score */}
        <BrierScorePanel />

        {/* P&L Chart */}
        <PnLChart series={displayDailySeries} startingBankroll={liveStartingBankroll ?? undefined} />

        {/* City-level performance breakdown */}
        <CityPerformancePanel trades={displayHistory} baselineDate={baselineDate} />

        {/* Ladder grid analytics */}
        <LadderPanel ladders={ladders} signals={signals} />
      </div>}

      {/* Trade detail drawer */}
      <TradeDrawer
        trade={selectedTrade}
        onClose={() => setSelectedTrade(null)}
      />
    </div>
  )
}
