import { useState } from 'react'
import { useReports } from '../hooks/useReports'
import { RunReport, CityMetric } from '../types'

// ── Time helpers ──────────────────────────────────────────────────────────────

/** Convert a UTC HH:MM slot string to the browser's local time. */
function utcSlotToLocal(utcSlot: string): string {
  const [h, m] = utcSlot.split(':').map(Number)
  const d = new Date()
  d.setUTCHours(h, m, 0, 0)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** Format any ISO timestamp as local HH:MM. */
function toLocalTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** Abbreviated local timezone label, e.g. "CDT", "EST". */
const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone
  .split('/')
  .pop()
  ?.replace('_', ' ') ?? ''

// ── Colour helpers ────────────────────────────────────────────────────────────

function dotColor(score: 'green' | 'yellow' | 'red'): string {
  return score === 'green'  ? 'bg-green-500'
       : score === 'yellow' ? 'bg-yellow-500'
       : 'bg-red-500'
}

function textColor(score: 'green' | 'yellow' | 'red'): string {
  return score === 'green'  ? 'text-green-400'
       : score === 'yellow' ? 'text-yellow-400'
       : 'text-red-400'
}

function cityStatusColors(status: CityMetric['status']) {
  return {
    row:  status === 'red'    ? 'bg-red-950/40'
        : status === 'yellow' ? 'bg-yellow-950/20'
        : status === 'green'  ? 'bg-green-950/20'
        : '',
    text: status === 'red'    ? 'text-red-400'
        : status === 'yellow' ? 'text-yellow-400'
        : status === 'green'  ? 'text-green-400'
        : 'text-gray-500',
    dot:  status === 'red'    ? 'bg-red-500'
        : status === 'yellow' ? 'bg-yellow-500'
        : status === 'green'  ? 'bg-green-500'
        : 'bg-gray-600',
  }
}

function fmtPct(v: number | null | undefined, decimals = 1): string {
  return v == null ? '—' : `${v.toFixed(decimals)}%`
}

function fmtRoi(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
}

// ── Go-Live Progress Bar ──────────────────────────────────────────────────────

function GoLiveBar({ report }: { report: RunReport | null }) {
  const GO_LIVE_TARGET = 'Jun 1, 2026'

  const criteria = [
    {
      label: '200+ predictions',
      met: (report?.total_predictions_30d ?? 0) >= 200,
      value: report ? `${report.total_predictions_30d} / 200` : '—',
    },
    {
      label: 'Brier < 0.15',
      met: report?.brier_score_30d != null && report.brier_score_30d < 0.15,
      value: report?.brier_score_30d != null ? report.brier_score_30d.toFixed(3) : '—',
    },
    {
      label: 'Worst city ≤ 0.22',
      met: report?.worst_city_brier != null && report.worst_city_brier <= 0.22,
      value: report?.worst_city_brier != null
        ? `${report.worst_city_brier.toFixed(3)} (${report.worst_city_name ?? '?'})`
        : '—',
    },
    {
      label: 'Win rate > 65%',
      met: (report?.win_rate_30d ?? 0) > 65,
      value: fmtPct(report?.win_rate_30d),
    },
  ]

  const criteriaMet = report?.criteria_met ?? 0

  let etaLabel = '—'
  if (report?.projected_go_live) {
    const eta = new Date(report.projected_go_live)
    const today = new Date()
    const daysOut = Math.ceil((eta.getTime() - today.getTime()) / 86400000)
    if (daysOut <= 0) {
      etaLabel = 'Ready now'
    } else {
      etaLabel = `~${eta.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} (${daysOut}d)`
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
            Go-Live Progress
          </h2>
          <div className="text-xs text-gray-500 mt-0.5">Target: {GO_LIVE_TARGET}</div>
        </div>
        <div className="text-right">
          <div className={`text-2xl font-bold ${criteriaMet === 4 ? 'text-green-400' : 'text-gray-300'}`}>
            {criteriaMet} / 4
          </div>
          <div className="text-xs text-gray-500">criteria met</div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mb-3">
        {criteria.map(({ label, met, value }) => (
          <div
            key={label}
            className={`rounded p-2.5 border ${
              met
                ? 'border-green-700 bg-green-900/30'
                : 'border-gray-600 bg-gray-700/40'
            }`}
          >
            <div className={`text-lg font-bold mb-0.5 ${met ? 'text-green-400' : 'text-gray-500'}`}>
              {met ? '✓' : '✗'}
            </div>
            <div className="text-xs text-gray-400 leading-tight mb-1">{label}</div>
            <div className={`text-xs font-mono ${met ? 'text-green-300' : 'text-gray-500'}`}>
              {value}
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between text-xs">
        <div className="text-gray-500">
          {report
            ? `Based on ${report.total_predictions_30d} predictions (30d) · Win rate: ${fmtPct(report.win_rate_30d)}`
            : 'No report data yet — first snapshot writes after the next signal_engine run'}
        </div>
        <div className={`font-semibold ${criteriaMet === 4 ? 'text-green-400' : 'text-gray-400'}`}>
          Projected ETA: {etaLabel}
        </div>
      </div>
    </div>
  )
}

// ── Run Card (expandable) ─────────────────────────────────────────────────────

// Actual UTC times reporter.py fires (matches crontab):
//   02:05 → after resolver     (9:05 PM Monterrey)
//   06:05 → after signal_engine (1:05 AM Monterrey)
//   15:30 → midday snapshot    (10:30 AM Monterrey)
//   21:30 → evening snapshot   (4:30 PM Monterrey)
const SLOTS = ['02:05', '06:05', '15:30', '21:30'] as const

function RunCard({ slot, report }: { slot: string; report: RunReport | null }) {
  const [expanded, setExpanded] = useState(false)

  if (!report) {
    return (
      <div className="bg-gray-800/50 rounded-lg border border-gray-700 p-3 opacity-50">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-gray-600" />
          <span className="text-xs text-gray-500 font-mono">{utcSlotToLocal(slot)}</span>
          <span className="text-xs text-gray-600 ml-2">— scheduled</span>
        </div>
      </div>
    )
  }

  const time = toLocalTime(report.run_time)

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      {/* Header row — always visible */}
      <button
        className="w-full text-left p-3 hover:bg-gray-750 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor(report.health_score)}`} />
          <span className="text-xs text-gray-400 font-mono w-20">{time} <span className="text-gray-600">{LOCAL_TZ}</span></span>
          <span className={`text-xs font-semibold uppercase w-12 ${textColor(report.health_score)}`}>
            {report.health_score}
          </span>
          <span className="text-xs text-gray-300 flex-1 truncate">{report.summary}</span>
          <span className="text-gray-600 text-xs ml-2 flex-shrink-0">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-700 p-3 space-y-3 text-xs">
          {/* Execution */}
          <div>
            <div className="text-gray-500 uppercase tracking-wider mb-1.5">Execution</div>
            <div className="grid grid-cols-3 gap-x-6 gap-y-1 text-gray-300">
              <div>Signals generated: <span className="text-white font-mono">{report.signals_generated}</span></div>
              <div>Orders placed: <span className="text-white font-mono">{report.orders_placed}</span></div>
              <div>Filled: <span className="text-white font-mono">{report.orders_filled}</span></div>
              <div>Phase 1 signals: <span className="text-white font-mono">{report.phase1_signals}</span></div>
              <div>Phase 2 signals: <span className="text-white font-mono">{report.phase2_signals}</span></div>
              <div>Failed orders: <span className={`font-mono ${report.orders_failed > 0 ? 'text-red-400' : 'text-white'}`}>{report.orders_failed}</span></div>
            </div>
          </div>

          {/* Phase 2 fires */}
          {report.phase2_fires?.length > 0 && (
            <div>
              <div className="text-gray-500 uppercase tracking-wider mb-1.5">Phase 2 Fires</div>
              <div className="flex flex-wrap gap-2">
                {report.phase2_fires.map((f, i) => (
                  <span
                    key={i}
                    className="px-2 py-0.5 rounded bg-blue-900/50 text-blue-300 border border-blue-700 font-mono"
                  >
                    {f.city} · {f.bracket} · {(f.confidence * 100).toFixed(0)}% conf
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Calibration */}
          <div>
            <div className="text-gray-500 uppercase tracking-wider mb-1.5">Calibration</div>
            <div className="text-gray-300">
              Avg |Δ|: <span className="text-white font-mono">{report.avg_delta_c != null ? `${report.avg_delta_c.toFixed(3)}°` : '—'}</span>
              <span className="ml-4">
                Uncalibrated cities: <span className={`font-mono ${(report.cities_uncalibrated?.length ?? 0) > 10 ? 'text-yellow-400' : 'text-white'}`}>{report.cities_uncalibrated?.length ?? 0}</span>
              </span>
              {(report.cities_uncalibrated?.length ?? 0) > 0 && (
                <span className="ml-2 text-gray-500">({report.cities_uncalibrated.slice(0, 5).join(', ')}{report.cities_uncalibrated.length > 5 ? `…+${report.cities_uncalibrated.length - 5}` : ''})</span>
              )}
            </div>
          </div>

          {/* Performance */}
          <div>
            <div className="text-gray-500 uppercase tracking-wider mb-1.5">7-Day Performance</div>
            <div className="grid grid-cols-3 gap-x-6 gap-y-1 text-gray-300">
              <div>Win rate: <span className={`font-mono ${(report.win_rate_7d ?? 0) >= 55 ? 'text-green-400' : (report.win_rate_7d ?? 0) >= 40 ? 'text-yellow-400' : 'text-red-400'}`}>{fmtPct(report.win_rate_7d)}</span></div>
              <div>ROI: <span className={`font-mono ${(report.roi_7d ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtRoi(report.roi_7d)}</span></div>
              <div>Resolved: <span className="text-white font-mono">{report.resolved_count_7d}</span></div>
              <div>Phase 1 WR: <span className="font-mono text-white">{fmtPct(report.win_rate_phase1_7d)}</span></div>
              <div>Phase 2 WR: <span className="font-mono text-white">{fmtPct(report.win_rate_phase2_7d)}</span></div>
            </div>
          </div>

          {/* Cities with no signals */}
          {(report.cities_no_signals?.length ?? 0) > 0 && (
            <div>
              <div className="text-gray-500 uppercase tracking-wider mb-1">Cities with No Signals</div>
              <div className="text-gray-400">
                {report.cities_no_signals.join(', ')}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── City Health Grid ──────────────────────────────────────────────────────────

function CityHealthGrid({ metrics }: { metrics: CityMetric[] }) {
  const [showAll, setShowAll] = useState(false)
  const [sortKey, setSortKey] = useState<'status' | 'city' | 'win_rate' | 'roi' | 'delta'>('status')
  const [sortAsc, setSortAsc] = useState(false)

  // Sort order for status: red first, then yellow, green, gray
  const statusOrder = { red: 0, yellow: 1, green: 2, gray: 3 }

  const sorted = [...metrics].sort((a, b) => {
    let diff = 0
    if (sortKey === 'status') {
      diff = (statusOrder[a.status] ?? 4) - (statusOrder[b.status] ?? 4)
      if (diff === 0) diff = a.city.localeCompare(b.city)
    } else if (sortKey === 'city') {
      diff = a.city.localeCompare(b.city)
    } else if (sortKey === 'win_rate') {
      diff = (a.win_rate_7d ?? -999) - (b.win_rate_7d ?? -999)
    } else if (sortKey === 'roi') {
      diff = (a.roi_7d ?? -999) - (b.roi_7d ?? -999)
    } else if (sortKey === 'delta') {
      diff = a.delta_c - b.delta_c
    }
    return sortAsc ? diff : -diff
  })

  const displayed = showAll ? sorted : sorted.slice(0, 20)

  function SortBtn({ k, label }: { k: typeof sortKey; label: string }) {
    const active = sortKey === k
    return (
      <button
        onClick={() => {
          if (active) setSortAsc((v) => !v)
          else { setSortKey(k); setSortAsc(false) }
        }}
        className={`text-xs uppercase tracking-wider ${active ? 'text-white' : 'text-gray-500 hover:text-gray-300'}`}
      >
        {label}{active ? (sortAsc ? ' ↑' : ' ↓') : ''}
      </button>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          City Health Matrix <span className="text-gray-600 font-normal normal-case">(7-day rolling)</span>
        </h2>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500 inline-block" /> ≥55% WR & ROI≥0</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-500 inline-block" /> ≥40% WR or ROI≥-20%</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500 inline-block" /> Below threshold</span>
        </div>
      </div>

      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-700">
            <th className="text-left pb-2 pr-2"><SortBtn k="city" label="City" /></th>
            <th className="text-right pb-2 px-2"><SortBtn k="delta" label="Delta_c" /></th>
            <th className="text-right pb-2 px-2"><SortBtn k="win_rate" label="7d Win%" /></th>
            <th className="text-right pb-2 px-2"><SortBtn k="roi" label="7d ROI" /></th>
            <th className="text-right pb-2 px-2 text-gray-500 uppercase tracking-wider">7d Sigs</th>
            <th className="text-center pb-2 px-2"><SortBtn k="status" label="Status" /></th>
            <th className="text-center pb-2 pl-2 text-gray-500 uppercase tracking-wider">Flag</th>
          </tr>
        </thead>
        <tbody>
          {displayed.map((cm) => {
            const colors = cityStatusColors(cm.status)
            return (
              <tr key={cm.city} className={`border-b border-gray-700/50 ${colors.row}`}>
                <td className="py-1.5 pr-2">
                  <div className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${colors.dot}`} />
                    <span className="text-gray-300 font-medium">{cm.city}</span>
                  </div>
                </td>
                <td className={`py-1.5 px-2 text-right font-mono ${cm.delta_c === 0 ? 'text-gray-600' : cm.delta_c > 0 ? 'text-blue-400' : 'text-orange-400'}`}>
                  {cm.delta_c === 0 ? '0.000' : `${cm.delta_c > 0 ? '+' : ''}${cm.delta_c.toFixed(3)}°`}
                  {cm.delta_samples > 0 && (
                    <span className="text-gray-600 ml-1">({cm.delta_samples})</span>
                  )}
                </td>
                <td className={`py-1.5 px-2 text-right font-mono ${colors.text}`}>
                  {fmtPct(cm.win_rate_7d)}
                </td>
                <td className={`py-1.5 px-2 text-right font-mono ${(cm.roi_7d ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {cm.signals_7d > 0 ? fmtRoi(cm.roi_7d) : '—'}
                </td>
                <td className="py-1.5 px-2 text-right font-mono text-gray-400">
                  {cm.signals_7d}
                </td>
                <td className={`py-1.5 px-2 text-center font-semibold uppercase text-xs ${colors.text}`}>
                  {cm.status === 'gray' ? <span className="text-gray-600">no data</span> : cm.status}
                </td>
                <td className="py-1.5 pl-2 text-center">
                  {cm.flag_review ? (
                    <span
                      title="Flagged for review: consistently RED for 4+ days"
                      className="text-yellow-400 font-bold cursor-help"
                    >
                      ⚠
                    </span>
                  ) : (
                    <span className="text-gray-700">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {metrics.length > 20 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="mt-3 text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          {showAll ? `Show top 20 ↑` : `Show all ${metrics.length} cities ↓`}
        </button>
      )}

      {metrics.length === 0 && (
        <div className="text-center text-gray-600 py-6">
          City health data will appear after the first run report is written.
        </div>
      )}
    </div>
  )
}

// ── History Log ───────────────────────────────────────────────────────────────

function HistoryLog({ reports }: { reports: Partial<RunReport>[] }) {
  const [showCount, setShowCount] = useState(40)

  if (reports.length === 0) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-2">
          Run History
        </h2>
        <div className="text-center text-gray-600 py-6 text-sm">
          No history yet. Reports will appear here after the next signal_engine run.
        </div>
      </div>
    )
  }

  const displayed = reports.slice(0, showCount)

  // Group by date
  const byDate: { date: string; runs: typeof displayed }[] = []
  let currentDate = ''
  for (const r of displayed) {
    const d = r.run_time ? r.run_time.slice(0, 10) : 'unknown'
    if (d !== currentDate) {
      byDate.push({ date: d, runs: [] })
      currentDate = d
    }
    byDate[byDate.length - 1].runs.push(r)
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Run History <span className="text-gray-600 font-normal normal-case">(30 days)</span>
        </h2>
        <span className="text-xs text-gray-500">{reports.length} snapshots</span>
      </div>

      <div className="space-y-3 max-h-96 overflow-y-auto pr-1">
        {byDate.map(({ date, runs }) => (
          <div key={date}>
            <div className="text-xs text-gray-600 uppercase tracking-wider mb-1 sticky top-0 bg-gray-800 py-0.5">
              {new Date(date + 'T12:00:00Z').toLocaleDateString('en-US', {
                weekday: 'short', month: 'short', day: 'numeric',
              })}
            </div>
            <div className="space-y-0.5">
              {runs.map((r) => (
                <div key={r.id} className="flex items-center gap-2 py-0.5">
                  <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    r.health_score === 'green'  ? 'bg-green-500'  :
                    r.health_score === 'yellow' ? 'bg-yellow-500' : 'bg-red-500'
                  }`} />
                  <span className="text-gray-500 font-mono text-xs w-16 flex-shrink-0">
                    {r.run_time ? toLocalTime(r.run_time) : (r.run_slot ?? '—')}
                  </span>
                  <span className={`text-xs w-12 flex-shrink-0 font-mono ${
                    (r.criteria_met ?? 0) === 4 ? 'text-green-400' : 'text-gray-500'
                  }`}>
                    {r.criteria_met ?? 0}/4
                  </span>
                  <span className="text-xs text-gray-400 truncate flex-1">{r.summary}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {reports.length > showCount && (
        <button
          onClick={() => setShowCount((c) => c + 40)}
          className="mt-3 text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          Load more ↓
        </button>
      )}
    </div>
  )
}

// ── Today's Run Timeline ──────────────────────────────────────────────────────

function TodayTimeline({ todayReports }: { todayReports: RunReport[] }) {
  // Match each slot to a report by time proximity (within 4 hours of the UTC slot).
  // This is robust to slot label changes — no string matching required.
  const slotMap = new Map<string, RunReport>()
  for (const slot of SLOTS) {
    const [h, m] = slot.split(':').map(Number)
    const slotMs = (h * 60 + m) * 60_000  // minutes-since-midnight in ms
    const match = todayReports.find(r => {
      const rt = new Date(r.run_time)
      const rtMs = (rt.getUTCHours() * 60 + rt.getUTCMinutes()) * 60_000
      return Math.abs(rtMs - slotMs) < 4 * 3_600_000  // within 4 hours
    })
    if (match) slotMap.set(slot, match)
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
        Today's Runs <span className="text-gray-600 font-normal normal-case text-xs">(your local time)</span>
      </h2>
      <div className="space-y-2">
        {SLOTS.map((slot) => (
          <RunCard key={slot} slot={slot} report={slotMap.get(slot) ?? null} />
        ))}
      </div>
    </div>
  )
}

// ── Main ReportsTab ───────────────────────────────────────────────────────────

interface ReportsTabProps {
  normalizePhase1?: boolean
}

export default function ReportsTab({ normalizePhase1 = false }: ReportsTabProps) {
  const { latestReport, todayReports, recentReports, loading, lastRefreshed, refresh } = useReports()

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-500 text-sm">
        Loading reports…
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Normalization notice */}
      {normalizePhase1 && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg bg-blue-900/30 border border-blue-700/50 text-xs text-blue-300">
          <span className="text-blue-400 flex-shrink-0 mt-0.5">ℹ</span>
          <span>
            <span className="font-semibold">New Model View is on.</span>{' '}
            Pre-computed report snapshots are based on historical sizing and cannot be retroactively adjusted.
            Win rates and Brier scores are unaffected by this view — they measure prediction accuracy, not dollar amounts.
            ROI and P&L figures in run cards reflect actual deployed amounts, not the new model.
          </span>
        </div>
      )}

      {/* Freshness + refresh */}
      <div className="flex items-center justify-between">
        <div className="text-xs text-gray-500">
          {lastRefreshed
            ? `Reports updated ${lastRefreshed.toLocaleTimeString()}`
            : 'Connecting…'}
        </div>
        <button
          onClick={refresh}
          className="text-xs px-2.5 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
        >
          ↻ Refresh
        </button>
      </div>

      {/* Go-live progress */}
      <GoLiveBar report={latestReport} />

      {/* Today's 4 run cards */}
      <TodayTimeline todayReports={todayReports} />

      {/* City health grid */}
      <CityHealthGrid metrics={latestReport?.city_metrics ?? []} />

      {/* 30-day history log */}
      <HistoryLog reports={recentReports as Partial<RunReport>[]} />
    </div>
  )
}
