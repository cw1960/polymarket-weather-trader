// Backtest harness UI.
//
// Reads from the historical_* tables backfilled by scripts/backfill_history.py.
// User picks a strategy + parameters, harness iterates over every resolved
// (city, date) event, runs the strategy, settles each trade against the
// known winning bracket, and reports aggregate P&L + per-event detail.

import { useMemo, useState } from 'react'
import {
  useHistoricalDataStatus, useHistoricalEvents, useHistoricalEvent,
  type HistoricalEvent, type HistoricalEventDetail,
} from '../../hooks/trader/useHistoricalData'
import {
  STRATEGIES, settleTrade,
  type Strategy, type BacktestSettlement,
} from '../../lib/backtestStrategies'
import supabase from '../../lib/supabase'
import SweepChart from './SweepChart'


// In-memory cache of fetched event details, keyed by "city|date". Lets a
// sweep avoid re-fetching the same N events for every parameter value.
const eventCache: Record<string, HistoricalEventDetail> = {}

async function getEventDetail(city: string, date: string): Promise<HistoricalEventDetail> {
  const k = `${city}|${date}`
  if (eventCache[k]) return eventCache[k]
  const d = await fetchEventDetail(city, date)
  eventCache[k] = d
  return d
}


function useRunBacktest(strategy: Strategy, params: Record<string, number | string>, stakeUsd: number, events: HistoricalEvent[]) {
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const [results, setResults] = useState<BacktestSettlement[]>([])
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setRunning(true)
    setError(null)
    setResults([])
    setProgress({ done: 0, total: events.length })
    const out: BacktestSettlement[] = []
    for (let i = 0; i < events.length; i++) {
      const ev = events[i]
      try {
        const detail = await getEventDetail(ev.city, ev.forecast_date)
        const trades = strategy.run(detail, params, stakeUsd)
        for (const t of trades) out.push(settleTrade(t, ev.winning_bracket_label))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
      setProgress({ done: i + 1, total: events.length })
    }
    setResults(out)
    setRunning(false)
  }

  return { run, running, progress, results, error }
}


// Sweep one numeric parameter through a list of values. Caches event
// details so the data fetch only happens once for the whole sweep.
interface SweepPoint { paramValue: number; roi: number; winRate: number; n: number; totalPnl: number }
function useRunSweep(
  strategy: Strategy,
  baseParams: Record<string, number | string>,
  sweepKey: string,
  sweepValues: number[],
  stakeUsd: number,
  events: HistoricalEvent[],
) {
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState({ stage: 'idle' as 'idle'|'fetching'|'running'|'done', done: 0, total: 0 })
  const [sweepResults, setSweepResults] = useState<SweepPoint[]>([])
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setRunning(true)
    setError(null)
    setSweepResults([])
    // Stage 1: fetch every event into cache (parallel batches of 6).
    setProgress({ stage: 'fetching', done: 0, total: events.length })
    const BATCH = 6
    for (let i = 0; i < events.length; i += BATCH) {
      const slice = events.slice(i, i + BATCH)
      await Promise.all(slice.map(async (ev) => {
        try { await getEventDetail(ev.city, ev.forecast_date) }
        catch (err) { setError(err instanceof Error ? err.message : String(err)) }
      }))
      setProgress({ stage: 'fetching', done: Math.min(i + BATCH, events.length), total: events.length })
    }
    // Stage 2: for each sweep value, run strategy across all cached events.
    setProgress({ stage: 'running', done: 0, total: sweepValues.length })
    const out: SweepPoint[] = []
    for (let i = 0; i < sweepValues.length; i++) {
      const v = sweepValues[i]
      const params = { ...baseParams, [sweepKey]: v }
      let totalPnl = 0
      let totalNotional = 0
      let n = 0
      let wins = 0
      for (const ev of events) {
        const k = `${ev.city}|${ev.forecast_date}`
        const detail = eventCache[k]
        if (!detail) continue
        const trades = strategy.run(detail, params, stakeUsd)
        for (const t of trades) {
          const s = settleTrade(t, ev.winning_bracket_label)
          totalPnl += s.pnl_usd
          totalNotional += s.notional_usd
          n++
          if (s.pnl_usd > 0) wins++
        }
      }
      out.push({
        paramValue: v,
        roi: totalNotional > 0 ? totalPnl / totalNotional : 0,
        winRate: n > 0 ? wins / n : 0,
        n, totalPnl,
      })
      setProgress({ stage: 'running', done: i + 1, total: sweepValues.length })
    }
    setSweepResults(out)
    setProgress({ stage: 'done', done: sweepValues.length, total: sweepValues.length })
    setRunning(false)
  }

  return { run, running, progress, sweepResults, error }
}


// One-shot fetch — same pattern as useHistoricalEvent but as a plain
// async function so the loop can await it.
async function fetchEventDetail(city: string, forecastDate: string): Promise<HistoricalEventDetail> {
  const dayStart = new Date(forecastDate + 'T00:00:00Z')
  const dayEnd = new Date(dayStart.getTime() + 36 * 3600 * 1000)
  const [resR, priceR, obsR] = await Promise.all([
    supabase.from('historical_event_resolutions')
      .select('city, forecast_date, winning_bracket_label, day_max_temp_c, day_max_temp_f, day_max_local_hour')
      .eq('city', city).eq('forecast_date', forecastDate).maybeSingle(),
    supabase.from('historical_bracket_prices')
      .select('bracket_label, bracket_unit, bracket_low_native, bracket_high_native, condition_id, recorded_at, yes_price')
      .eq('city', city).eq('forecast_date', forecastDate)
      .order('recorded_at', { ascending: true }).limit(20000),
    supabase.from('historical_temp_observations')
      .select('observed_at, temp_f, temp_c')
      .eq('city', city)
      .gte('observed_at', dayStart.toISOString())
      .lte('observed_at', dayEnd.toISOString())
      .order('observed_at', { ascending: true }).limit(200),
  ])
  if (resR.error) throw resR.error
  if (priceR.error) throw priceR.error
  if (obsR.error) throw obsR.error
  type Row = NonNullable<typeof priceR.data>[number]
  const byBracket = new Map<string, Row[]>()
  for (const r of priceR.data ?? []) {
    const lbl = r.bracket_label as string
    if (!byBracket.has(lbl)) byBracket.set(lbl, [])
    byBracket.get(lbl)!.push(r as Row)
  }
  const brackets = [...byBracket.entries()].map(([lbl, rows]) => ({
    bracket_label: lbl,
    bracket_unit: ((rows[0].bracket_unit as string) || 'C') as 'F' | 'C',
    bracket_low_native: rows[0].bracket_low_native as number | null,
    bracket_high_native: rows[0].bracket_high_native as number | null,
    condition_id: rows[0].condition_id as string,
    points: rows.map((r) => ({
      ms: new Date(r.recorded_at as string).getTime(),
      yes_price: r.yes_price as number,
    })),
  })).sort((a, b) => (a.bracket_low_native ?? 0) - (b.bracket_low_native ?? 0))
  return {
    event: resR.data as HistoricalEvent | null,
    brackets,
    observations: (obsR.data ?? []).map((o) => ({
      ms: new Date(o.observed_at as string).getTime(),
      temp_f: o.temp_f as number,
      temp_c: o.temp_c as number,
    })),
  }
}


export default function Backtest() {
  const status = useHistoricalDataStatus()
  const { events } = useHistoricalEvents(null)

  const [strategyKey, setStrategyKey] = useState<string>(STRATEGIES[0].key)
  const strategy = STRATEGIES.find((s) => s.key === strategyKey)!
  const [params, setParams] = useState<Record<string, number | string>>({ ...strategy.params })
  const [stakeUsd, setStakeUsd] = useState<number>(10)
  const [cityFilter, setCityFilter] = useState<string>('')
  const [dateFromFilter, setDateFromFilter] = useState<string>('')

  // Filter events by user-chosen city / date-from before running
  const filteredEvents = useMemo(() => events.filter((e) => {
    if (cityFilter && e.city !== cityFilter) return false
    if (dateFromFilter && e.forecast_date < dateFromFilter) return false
    return true
  }), [events, cityFilter, dateFromFilter])

  const harness = useRunBacktest(strategy, params, stakeUsd, filteredEvents)

  // Sweep mode state
  const numericParamKeys = Object.entries(strategy.params)
    .filter(([_, v]) => typeof v === 'number').map(([k]) => k)
  const [sweepMode, setSweepMode] = useState(false)
  const [sweepKey, setSweepKey] = useState<string>(numericParamKeys[0] ?? '')
  const [sweepFrom, setSweepFrom] = useState<number>(12)
  const [sweepTo, setSweepTo]     = useState<number>(22)
  const [sweepStep, setSweepStep] = useState<number>(2)

  const sweepValues = useMemo(() => {
    const arr: number[] = []
    if (sweepStep <= 0) return arr
    for (let v = sweepFrom; v <= sweepTo + 1e-9; v += sweepStep) {
      arr.push(Number(v.toFixed(6)))
      if (arr.length > 200) break
    }
    return arr
  }, [sweepFrom, sweepTo, sweepStep])

  const sweep = useRunSweep(strategy, params, sweepKey, sweepValues, stakeUsd, filteredEvents)

  // Aggregate stats
  const stats = useMemo(() => {
    const r = harness.results
    const n = r.length
    if (n === 0) return null
    const totalPnl = r.reduce((s, t) => s + t.pnl_usd, 0)
    const totalNotional = r.reduce((s, t) => s + t.notional_usd, 0)
    const wins = r.filter((t) => t.pnl_usd > 0).length
    const breakevens = r.filter((t) => Math.abs(t.pnl_usd) < 0.001).length
    const losses = n - wins - breakevens
    return {
      n,
      totalPnl,
      totalNotional,
      roi: totalNotional > 0 ? totalPnl / totalNotional : 0,
      winRate: n > 0 ? wins / n : 0,
      avgPnl: n > 0 ? totalPnl / n : 0,
      wins, breakevens, losses,
    }
  }, [harness.results])

  // Unique cities for the filter dropdown
  const cities = useMemo(() => {
    const set = new Set<string>()
    for (const e of events) set.add(e.city)
    return [...set].sort()
  }, [events])

  return (
    <div className="text-white p-6 space-y-4">
      <div>
        <h2 className="text-xl font-bold text-gray-100">🔬 Backtest harness</h2>
        <p className="text-sm text-gray-400 mt-1">
          Replays a strategy against every resolved (city, date) event in the historical backfill.
          P&amp;L uses (payoff − entry) × shares, with each event sized to your chosen stake.
        </p>
      </div>

      {/* Data status */}
      <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div className="flex items-baseline justify-between mb-2">
          <div className="text-base font-semibold text-gray-200">Historical data status</div>
          {status.loading && <div className="text-xs text-gray-500">refreshing…</div>}
        </div>
        {status.error ? (
          <div className="text-sm text-red-400">Error: {status.error}</div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <Stat label="Resolved events" value={status.eventCount.toLocaleString()} />
            <Stat label="Earliest" value={status.earliest ?? '—'} />
            <Stat label="Latest" value={status.latest ?? '—'} />
            <Stat label="Price rows" value={status.priceRowCount?.toLocaleString() ?? '—'} />
          </div>
        )}
        <div className="text-xs text-gray-500 mt-2">
          Backfill runs nightly at 04:30 UTC. This panel auto-refreshes every 30s while a backfill is in progress.
        </div>
      </div>

      {/* Strategy + params + filters */}
      <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div className="text-base font-semibold text-gray-200 mb-3">Configure run</div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="space-y-3">
            <Field label="Strategy">
              <select
                value={strategyKey}
                onChange={(e) => { setStrategyKey(e.target.value); setParams({ ...STRATEGIES.find((s) => s.key === e.target.value)!.params }) }}
                className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm"
              >
                {STRATEGIES.map((s) => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
              </select>
            </Field>
            <div className="text-xs text-gray-400 leading-relaxed">{strategy.description}</div>
            {Object.entries(strategy.params).map(([k]) => (
              <Field key={k} label={k.replace(/_/g, ' ')}>
                <input
                  type="text"
                  value={String(params[k] ?? '')}
                  onChange={(e) => {
                    const v = e.target.value
                    setParams((p) => ({ ...p, [k]: isNaN(Number(v)) ? v : Number(v) }))
                  }}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm font-mono"
                />
              </Field>
            ))}
          </div>
          <div className="space-y-3">
            <Field label="Stake per event (USD)">
              <input
                type="number" min={1} step={1}
                value={stakeUsd}
                onChange={(e) => setStakeUsd(Number(e.target.value) || 10)}
                className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm font-mono"
              />
            </Field>
            <Field label="City filter (optional)">
              <select
                value={cityFilter}
                onChange={(e) => setCityFilter(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm"
              >
                <option value="">(all cities)</option>
                {cities.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
            <Field label="From date (optional, YYYY-MM-DD)">
              <input
                type="text" placeholder="2026-04-01"
                value={dateFromFilter}
                onChange={(e) => setDateFromFilter(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm font-mono"
              />
            </Field>
            <Field label="Events that will be tested">
              <div className="text-sm font-mono text-cyan-300">{filteredEvents.length.toLocaleString()}</div>
            </Field>
          </div>
        </div>
        {/* Sweep mode toggle + config */}
        <div className="mt-4 border-t border-gray-800 pt-4">
          <label className="inline-flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
            <input type="checkbox" checked={sweepMode} onChange={(e) => setSweepMode(e.target.checked)} />
            Sweep mode (run strategy over a range of one parameter)
          </label>
          {sweepMode && numericParamKeys.length > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
              <Field label="Parameter">
                <select
                  value={sweepKey}
                  onChange={(e) => setSweepKey(e.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm"
                >
                  {numericParamKeys.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </Field>
              <Field label="From">
                <input type="number" value={sweepFrom} onChange={(e) => setSweepFrom(Number(e.target.value))}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm font-mono" />
              </Field>
              <Field label="To">
                <input type="number" value={sweepTo} onChange={(e) => setSweepTo(Number(e.target.value))}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm font-mono" />
              </Field>
              <Field label={`Step (yields ${sweepValues.length} runs)`}>
                <input type="number" step="0.01" value={sweepStep} onChange={(e) => setSweepStep(Number(e.target.value))}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm font-mono" />
              </Field>
            </div>
          )}
        </div>

        <div className="mt-4 flex items-center gap-3">
          {!sweepMode ? (
            <button
              onClick={harness.run}
              disabled={harness.running || filteredEvents.length === 0}
              className="bg-cyan-700 hover:bg-cyan-600 disabled:bg-gray-800 disabled:text-gray-600 text-cyan-50 font-medium px-4 py-2 rounded text-sm"
            >
              {harness.running ? `Running… (${harness.progress.done} / ${harness.progress.total})` : `Run backtest`}
            </button>
          ) : (
            <button
              onClick={sweep.run}
              disabled={sweep.running || filteredEvents.length === 0 || sweepValues.length === 0}
              className="bg-violet-700 hover:bg-violet-600 disabled:bg-gray-800 disabled:text-gray-600 text-violet-50 font-medium px-4 py-2 rounded text-sm"
            >
              {sweep.running
                ? sweep.progress.stage === 'fetching'
                  ? `Fetching events… (${sweep.progress.done} / ${sweep.progress.total})`
                  : `Sweeping… (${sweep.progress.done} / ${sweep.progress.total})`
                : `Run sweep (${sweepValues.length} runs × ${filteredEvents.length} events)`}
            </button>
          )}
          {harness.error && <span className="text-xs text-red-400">Error: {harness.error}</span>}
          {sweep.error && <span className="text-xs text-red-400">Sweep error: {sweep.error}</span>}
        </div>
      </div>

      {/* Sweep results */}
      {sweep.sweepResults.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
          <div className="flex items-baseline justify-between mb-3">
            <div className="text-base font-semibold text-gray-200">
              Sweep — {sweep.sweepResults.length} runs of <span className="text-violet-300">{sweepKey}</span>
            </div>
            <div className="text-xs text-gray-500">
              {filteredEvents.length} events per run. Cached once, run-time scales with sweep size.
            </div>
          </div>
          <div className="overflow-x-auto">
            <SweepChart
              points={sweep.sweepResults.map((s) => ({
                paramValue: s.paramValue, roi: s.roi, winRate: s.winRate, n: s.n, totalPnl: s.totalPnl,
              }))}
              paramName={sweepKey}
              width={Math.max(600, sweep.sweepResults.length * 80)}
              height={240}
            />
          </div>
          <div className="text-[11px] text-gray-500 mt-2 leading-snug">
            Each bar = ROI for that param value across all filtered events. Look for monotonic trends
            (e.g. ROI rising as <code>hour_utc</code> increases = late entries beat early). A single
            spike at one value with surrounding bars near zero is usually overfitting — confirm it
            still works on a different city / date range before believing it.
          </div>

          {/* Table */}
          <div className="mt-3 max-h-72 overflow-y-auto border border-gray-800 rounded">
            <table className="w-full text-sm">
              <thead className="text-gray-400 sticky top-0 bg-gray-950">
                <tr>
                  <th className="text-left  py-1.5 px-2 font-normal">{sweepKey}</th>
                  <th className="text-right py-1.5 px-2 font-normal">ROI</th>
                  <th className="text-right py-1.5 px-2 font-normal">Win rate</th>
                  <th className="text-right py-1.5 px-2 font-normal">N</th>
                  <th className="text-right py-1.5 px-2 font-normal">Total P&L</th>
                </tr>
              </thead>
              <tbody>
                {sweep.sweepResults.map((s, i) => (
                  <tr key={i} className="border-t border-gray-900">
                    <td className="px-2 py-1 font-mono text-gray-300">{s.paramValue}</td>
                    <td className={`px-2 py-1 text-right font-mono ${s.roi >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                      {(s.roi * 100).toFixed(2)}%
                    </td>
                    <td className="px-2 py-1 text-right font-mono text-gray-300">{(s.winRate * 100).toFixed(1)}%</td>
                    <td className="px-2 py-1 text-right font-mono text-gray-400">{s.n}</td>
                    <td className={`px-2 py-1 text-right font-mono ${s.totalPnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                      {s.totalPnl >= 0 ? '+' : ''}${s.totalPnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Single-run results */}
      {stats && (
        <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
          <div className="text-base font-semibold text-gray-200 mb-3">Results — {stats.n} trades</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <Stat label="Total P&L" value={`$${stats.totalPnl.toFixed(2)}`} valueColor={stats.totalPnl >= 0 ? 'text-emerald-300' : 'text-red-300'} />
            <Stat label="ROI" value={`${(stats.roi * 100).toFixed(2)}%`} valueColor={stats.roi >= 0 ? 'text-emerald-300' : 'text-red-300'} />
            <Stat label="Win rate" value={`${(stats.winRate * 100).toFixed(1)}%`} />
            <Stat label="Avg P&L / trade" value={`$${stats.avgPnl.toFixed(3)}`} />
            <Stat label="Wins" value={stats.wins.toLocaleString()} />
            <Stat label="Losses" value={stats.losses.toLocaleString()} />
            <Stat label="Breakevens" value={stats.breakevens.toLocaleString()} />
            <Stat label="Notional traded" value={`$${stats.totalNotional.toFixed(0)}`} />
          </div>

          <div className="mt-4 max-h-72 overflow-y-auto border border-gray-800 rounded">
            <table className="w-full text-xs">
              <thead className="text-gray-400 sticky top-0 bg-gray-950">
                <tr>
                  <th className="text-left  py-1.5 px-2 font-normal">Date</th>
                  <th className="text-left  py-1.5 px-2 font-normal">City</th>
                  <th className="text-left  py-1.5 px-2 font-normal">Bracket</th>
                  <th className="text-right py-1.5 px-2 font-normal">Entry</th>
                  <th className="text-right py-1.5 px-2 font-normal">Won?</th>
                  <th className="text-right py-1.5 px-2 font-normal">P&L</th>
                </tr>
              </thead>
              <tbody>
                {harness.results.slice(0, 500).map((t, i) => (
                  <tr key={i} className="border-t border-gray-900">
                    <td className="px-2 py-1 text-gray-400">{t.forecast_date}</td>
                    <td className="px-2 py-1 text-gray-300">{t.city}</td>
                    <td className="px-2 py-1 text-cyan-200">{t.bracket_label}</td>
                    <td className="px-2 py-1 text-right font-mono text-gray-300">{(t.entry_price * 100).toFixed(1)}¢</td>
                    <td className="px-2 py-1 text-right font-mono">{t.won_event ? '✓' : '·'}</td>
                    <td className={`px-2 py-1 text-right font-mono ${t.pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                      {t.pnl_usd >= 0 ? '+' : ''}${t.pnl_usd.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {harness.results.length > 500 && (
              <div className="px-2 py-1.5 text-xs text-gray-500">Showing first 500 of {harness.results.length.toLocaleString()} trades.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}


function Stat({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`text-lg font-mono ${valueColor ?? 'text-gray-100'}`}>{value}</div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      {children}
    </label>
  )
}
