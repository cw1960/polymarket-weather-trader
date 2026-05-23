import { useState } from 'react'
import { commentary as fetchCommentary } from './api'
import type { BucketStat, CommentaryResponse, MonitorPosition, StructuredCommentary } from './types'

const REC_COLOR: Record<string, string> = {
  disqualify:     'bg-red-900 text-red-200',
  ignore:         'bg-gray-700 text-gray-300',
  learn:          'bg-blue-900 text-blue-200',
  measure_first:  'bg-yellow-900 text-yellow-200',
  counter:        'bg-orange-900 text-orange-200',
  copy:           'bg-green-900 text-green-200',
}

const REC_TOOLTIP: Record<string, string> = {
  disqualify:     "Adversarial check refutes the apparent edge — measuring further won't help",
  ignore:         "Not relevant to our strategy",
  learn:          "Useful insight but no action",
  measure_first:  "Track defined criteria before acting",
  counter:        "Take the other side when our model disagrees",
  copy:           "Mirror the trader's positions",
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(2)}K`
  return `${sign}$${abs.toFixed(2)}`
}

function GateIcon({ value }: { value: boolean | null }) {
  if (value === true)  return <span className="text-green-400">✓</span>
  if (value === false) return <span className="text-red-400">✗</span>
  return <span className="text-gray-500">—</span>
}

/**
 * Renders the True P&L Estimate vs Net Cashflow reconciliation.
 *
 * - consistent  → green; the trader's per-bucket P&L is internally
 *                 consistent with their actual wallet cashflow.
 * - inflated    → red;   per-bucket totals overstate reality.
 * - deflated    → yellow; per-bucket totals understate reality.
 * - other       → gray;  insufficient data.
 *
 * This panel addresses the "we're just speculating" problem head-on:
 * if the two numbers reconcile, we're not speculating anymore.
 */
function ConsistencyCheckPanel({
  cc,
}: {
  cc: NonNullable<StructuredCommentary['consistency_check']>
}) {
  const palette = {
    consistent:        { bg: 'bg-green-950/30',  border: 'border-green-700/50',  tag: 'bg-green-700 text-green-100',  label: 'BOOKS RECONCILE'      },
    inflated:          { bg: 'bg-red-950/30',    border: 'border-red-700/50',    tag: 'bg-red-700 text-red-100',      label: 'PER-BUCKET INFLATED' },
    deflated:          { bg: 'bg-yellow-950/30', border: 'border-yellow-700/50', tag: 'bg-yellow-700 text-yellow-100', label: 'PER-BUCKET DEFLATED' },
    insufficient_data: { bg: 'bg-gray-800/40',   border: 'border-gray-600/50',   tag: 'bg-gray-700 text-gray-300',     label: 'NOT ENOUGH DATA'     },
  }
  const p = palette[cc.match_quality as keyof typeof palette] || palette.insufficient_data
  const diff = cc.true_pnl_estimate_total_usd - cc.net_cashflow_usd
  return (
    <div className={`border ${p.border} ${p.bg} rounded-lg p-3`}>
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
          True P&L Reconciliation
        </h4>
        <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${p.tag}`}>
          {p.label}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3 mb-2">
        <div>
          <div className="text-[10px] text-gray-500 uppercase">True Est total</div>
          <div className="text-sm font-mono font-bold text-blue-300">
            {fmtUsd(cc.true_pnl_estimate_total_usd)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-500 uppercase">Net cashflow</div>
          <div className="text-sm font-mono font-bold text-gray-200">
            {fmtUsd(cc.net_cashflow_usd)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-gray-500 uppercase">Difference</div>
          <div className={`text-sm font-mono font-bold ${
            Math.abs(diff) < 2000 ? 'text-green-400' : Math.abs(diff) < 5000 ? 'text-yellow-400' : 'text-red-400'
          }`}>
            {diff >= 0 ? '+' : ''}{fmtUsd(diff)}
          </div>
        </div>
      </div>
      <p className="text-xs text-gray-300 leading-relaxed">{cc.interpretation}</p>
    </div>
  )
}

function BucketTable({ rows, title, emptyLabel }: { rows: BucketStat[]; title: string; emptyLabel: string }) {
  return (
    <div>
      <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">{title}</h4>
      {!rows || rows.length === 0 ? (
        <p className="text-xs text-gray-500 italic">{emptyLabel}</p>
      ) : (
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-gray-500 border-b border-gray-700">
              <th className="text-left pr-2 pb-1">Bucket</th>
              <th className="text-right pr-2 pb-1">n</th>
              <th className="text-right pr-2 pb-1">Win%</th>
              <th className="text-right pr-2 pb-1" title="Resolved P&L only (closed + on-chain resolved)">P&amp;L</th>
              <th className="text-right pr-2 pb-1 text-blue-400" title="Resolved P&L + Open MTM (current best-bid valuation)">True Est</th>
              <th className="text-right pr-2 pb-1">ROI</th>
              <th className="text-left pl-3 pb-1">Note</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const pnlClr = r.pnl_usd > 0 ? 'text-green-400' : r.pnl_usd < 0 ? 'text-red-400' : 'text-gray-400'
              const trueEst = r.true_pnl_estimate
              const teClr = trueEst == null ? 'text-gray-500'
                          : trueEst > 0 ? 'text-green-400'
                          : trueEst < 0 ? 'text-red-400' : 'text-gray-400'
              return (
                <tr key={i} className="border-b border-gray-800/50">
                  <td className="pr-2 py-1 text-gray-300">{r.bucket}</td>
                  <td className="pr-2 py-1 text-right text-white">{r.n_resolved}</td>
                  <td className="pr-2 py-1 text-right text-gray-300">{r.win_rate_pct?.toFixed(1)}%</td>
                  <td className={`pr-2 py-1 text-right ${pnlClr}`}>{fmtUsd(r.pnl_usd)}</td>
                  <td className={`pr-2 py-1 text-right font-semibold ${teClr}`}>
                    {trueEst == null ? '—' : fmtUsd(trueEst)}
                  </td>
                  <td className={`pr-2 py-1 text-right ${pnlClr}`}>{r.roi_pct == null ? '—' : `${r.roi_pct >= 0 ? '+' : ''}${r.roi_pct.toFixed(0)}%`}</td>
                  <td className="pl-3 py-1 text-gray-500">{r.note}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

function StructuredView({ c, modelInfo }: { c: StructuredCommentary; modelInfo: React.ReactNode }) {
  const recColor = REC_COLOR[c.recommendation] || 'bg-gray-700 text-gray-300'
  const allGatesPass = c.gates.a_validated_zone && c.gates.b_supporting_stat_cited && c.gates.c_kelly_row_matches === true

  return (
    <div className="space-y-4">
      {/* Headline: summary + recommendation badge */}
      <div className="flex items-start gap-3">
        <div className="flex-1 text-sm text-gray-200 leading-relaxed">{c.strategy_summary}</div>
        <span
          className={`shrink-0 text-xs font-bold px-3 py-1.5 rounded uppercase tracking-wider ${recColor}`}
          title={REC_TOOLTIP[c.recommendation] || ''}
        >
          {c.recommendation.replace('_', ' ')}
        </span>
      </div>

      {/* Consistency check — the True P&L Estimate vs Net Cashflow reconciliation.
          When match_quality=consistent, the trader's per-bucket numbers can be
          trusted. When inflated/deflated, the books don't reconcile and per-bucket
          P&L should be treated with caution. */}
      {c.consistency_check && (
        <ConsistencyCheckPanel cc={c.consistency_check} />
      )}

      {/* Trajectory summary — only when AI saw it as non-trivial */}
      {c.trajectory_summary && (
        <div className="border border-purple-700/40 bg-purple-950/20 rounded-lg p-3">
          <h4 className="text-xs font-semibold text-purple-300 uppercase tracking-wider mb-1">
            Trajectory (recent vs lifetime)
          </h4>
          <p className="text-sm text-gray-200 leading-relaxed">{c.trajectory_summary}</p>
        </div>
      )}

      {/* Wins / Losses tables — only render losses table if there ARE losses */}
      <div className={`grid ${c.losses && c.losses.length > 0 ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1'} gap-4 pt-2 border-t border-gray-700`}>
        <BucketTable rows={c.wins} title="Where they win (proven — n≥10 resolved)" emptyLabel="No bucket has ≥10 resolved profitable trades." />
        {c.losses && c.losses.length > 0 && (
          <BucketTable rows={c.losses} title="Where they lose (proven — n≥10 resolved)" emptyLabel="" />
        )}
      </div>
      {(!c.losses || c.losses.length === 0) && c.wins && c.wins.length > 0 && (
        <p className="text-xs text-gray-500 italic -mt-2">
          All proven buckets are profitable. No losing buckets meet the n≥10 threshold.
        </p>
      )}

      {/* Speculative open bets — open exposure without resolved track record */}
      {c.speculative_open_bets && c.speculative_open_bets.length > 0 && (
        <div className="border border-yellow-700/40 bg-yellow-950/20 rounded-lg p-3">
          <h4 className="text-xs font-semibold text-yellow-300 uppercase tracking-wider mb-2">
            Speculative open bets (UNRESOLVED — not proven)
          </h4>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-500 border-b border-gray-700">
                <th className="text-left pr-2 pb-1">Bucket</th>
                <th className="text-right pr-2 pb-1">Open n</th>
                <th className="text-right pr-2 pb-1">MTM</th>
                <th className="text-right pr-2 pb-1">Best</th>
                <th className="text-right pr-2 pb-1">Worst</th>
                <th className="text-left pl-3 pb-1">Note</th>
              </tr>
            </thead>
            <tbody>
              {c.speculative_open_bets.map((r, i) => (
                <tr key={i} className="border-b border-gray-800/50">
                  <td className="pr-2 py-1 text-gray-300">{r.bucket}</td>
                  <td className="pr-2 py-1 text-right text-white">{r.n_open}</td>
                  <td className={`pr-2 py-1 text-right ${r.open_mtm_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {fmtUsd(r.open_mtm_pnl)}
                  </td>
                  <td className="pr-2 py-1 text-right text-green-500/70">{fmtUsd(r.open_best_pnl)}</td>
                  <td className="pr-2 py-1 text-right text-red-500/70">{fmtUsd(r.open_worst_pnl)}</td>
                  <td className="pl-3 py-1 text-gray-400">{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[10px] text-gray-500 italic mt-1">
            These positions haven't resolved yet. Treated separately from proven wins/losses
            so the trader's track record isn't conflated with their open speculation.
          </p>
        </div>
      )}

      {/* Per-city breakdown of shared cities */}
      {c.shared_city_breakdown && c.shared_city_breakdown.length > 0 && (
        <div className="border border-blue-700/40 bg-blue-950/15 rounded-lg p-3">
          <h4 className="text-xs font-semibold text-blue-300 uppercase tracking-wider mb-2">
            Shared-city breakdown (where their edge lives)
          </h4>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-500 border-b border-gray-700">
                <th className="text-left pr-2 pb-1">City</th>
                <th className="text-left pr-2 pb-1">Verdict</th>
                <th className="text-left pr-2 pb-1">Best bucket</th>
                <th className="text-right pr-2 pb-1">Best P&amp;L</th>
                <th className="text-left pr-2 pb-1">Worst bucket</th>
                <th className="text-right pr-2 pb-1">Worst P&amp;L</th>
                <th className="text-left pl-3 pb-1">Note</th>
              </tr>
            </thead>
            <tbody>
              {c.shared_city_breakdown.map((r, i) => {
                const verdictClr = {
                  profitable: 'text-green-400',
                  losing:     'text-red-400',
                  mixed:      'text-yellow-400',
                  thin:       'text-gray-500',
                }[r.verdict as string] || 'text-gray-400'
                return (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="pr-2 py-1 text-gray-300 capitalize">{r.city}</td>
                    <td className={`pr-2 py-1 ${verdictClr}`}>{r.verdict}</td>
                    <td className="pr-2 py-1 text-gray-400">{r.best_bucket}</td>
                    <td className={`pr-2 py-1 text-right ${r.best_pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {fmtUsd(r.best_pnl_usd)}
                    </td>
                    <td className="pr-2 py-1 text-gray-400">{r.worst_bucket}</td>
                    <td className={`pr-2 py-1 text-right ${r.worst_pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {fmtUsd(r.worst_pnl_usd)}
                    </td>
                    <td className="pl-3 py-1 text-gray-400">{r.note}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Anti-precedent ranking */}
      {c.anti_precedent_ranking && c.anti_precedent_ranking.n_priors_analyzed > 0 && (
        <div className="border border-orange-700/40 bg-orange-950/15 rounded-lg p-3">
          <h4 className="text-xs font-semibold text-orange-300 uppercase tracking-wider mb-1">
            Class ranking — {c.anti_precedent_ranking.class_label}s analyzed so far
          </h4>
          <div className="text-xs font-mono text-gray-400 mb-1">
            this trader: <span className={c.anti_precedent_ranking.this_traders_cashflow_usd >= 0 ? 'text-green-400' : 'text-red-400'}>
              {fmtUsd(c.anti_precedent_ranking.this_traders_cashflow_usd)}
            </span>
            {' · '}
            class aggregate: <span className={c.anti_precedent_ranking.priors_aggregate_cashflow_usd >= 0 ? 'text-green-400' : 'text-red-400'}>
              {fmtUsd(c.anti_precedent_ranking.priors_aggregate_cashflow_usd)}
            </span>
            {' · '}
            percentile: <span className="text-orange-300">{c.anti_precedent_ranking.percentile_in_class.replace('_', ' ')}</span>
          </div>
          <p className="text-sm text-gray-200 leading-relaxed">{c.anti_precedent_ranking.interpretation}</p>
        </div>
      )}

      {/* Replicability — does this work for US? */}
      {c.replicability && (
        <div className={`border rounded-lg p-3 ${
          c.replicability.score === 'copyable'      ? 'border-green-700/50 bg-green-950/20'
          : c.replicability.score === 'partial'    ? 'border-yellow-700/50 bg-yellow-950/20'
          : 'border-red-700/50 bg-red-950/20'
        }`}>
          <div className="flex items-center justify-between mb-1">
            <h4 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
              Replicability — can WE do this?
            </h4>
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase ${
              c.replicability.score === 'copyable'      ? 'bg-green-700 text-green-100'
              : c.replicability.score === 'partial'    ? 'bg-yellow-700 text-yellow-100'
              : 'bg-red-700 text-red-100'
            }`}>
              {c.replicability.score.replace('_', ' ')}
            </span>
          </div>
          <p className="text-sm text-gray-200 leading-relaxed mb-1">{c.replicability.explanation}</p>
          {c.replicability.blocking_factors && c.replicability.blocking_factors.length > 0 && (
            <div className="text-xs text-gray-400 font-mono">
              blocking: {c.replicability.blocking_factors.join(' · ')}
            </div>
          )}
        </div>
      )}

      {/* Lessons for us — the most actionable section */}
      {c.lessons_for_us && c.lessons_for_us.length > 0 && (
        <div className="border-2 border-green-700/60 bg-green-950/25 rounded-lg p-3">
          <h4 className="text-sm font-semibold text-green-300 uppercase tracking-wider mb-2">
            📋 Lessons for us
          </h4>
          <ul className="space-y-1.5">
            {c.lessons_for_us.map((l, i) => (
              <li key={i} className="text-sm text-gray-100 leading-relaxed flex gap-2">
                <span className="text-green-400 font-bold shrink-0">→</span>
                <span>{l}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Recommendation explainer — plain English */}
      {c.recommendation_explainer && (
        <div className="border border-gray-700 bg-gray-800/50 rounded-lg p-3">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
            Why this recommendation
          </h4>
          <p className="text-sm text-gray-200 leading-relaxed italic">{c.recommendation_explainer}</p>
        </div>
      )}

      {/* Overlap */}
      <div className="border-t border-gray-700 pt-3">
        <div className="flex items-center gap-2 mb-1">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Overlap with our edge zone</h4>
          <span className="text-xs font-mono text-gray-500">
            verdict={c.overlap.verdict} · our_resolved={c.overlap.our_validated_resolved_count}
          </span>
        </div>
        <p className="text-sm text-gray-200 leading-relaxed">{c.overlap.explanation}</p>
        {c.overlap.shared_cities?.length > 0 && (
          <div className="text-xs text-gray-500 font-mono mt-1">
            shared cities: {c.overlap.shared_cities.join(', ')}
          </div>
        )}
      </div>

      {/* Gates */}
      <div className="border-t border-gray-700 pt-3">
        <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Action gates</h4>
        <table className="text-xs font-mono">
          <tbody>
            <tr>
              <td className="pr-3 py-0.5"><GateIcon value={c.gates.a_validated_zone} /></td>
              <td className="text-gray-400">(a) Operates in our validated edge zone</td>
            </tr>
            <tr>
              <td className="pr-3 py-0.5"><GateIcon value={c.gates.b_supporting_stat_cited} /></td>
              <td className="text-gray-400">(b) Supporting stat cited from data</td>
            </tr>
            <tr>
              <td className="pr-3 py-0.5"><GateIcon value={c.gates.c_kelly_row_matches} /></td>
              <td className="text-gray-400">(c) Sizing matches a valid Kelly row{c.kelly_sizing_row !== null && c.kelly_sizing_row !== undefined ? ` (row ${c.kelly_sizing_row})` : ''}</td>
            </tr>
          </tbody>
        </table>
        <p className="text-sm text-gray-300 mt-2 leading-relaxed">{c.gates.explanation}</p>
        {!allGatesPass && (
          <p className="text-xs text-yellow-400 mt-1 italic">
            At least one gate fails → default recommendation is <code>measure_first</code>.
          </p>
        )}
      </div>

      {/* Validation plan */}
      {c.validation_plan && (
        <div className="border-t border-gray-700 pt-3 bg-blue-950/20 -mx-4 px-4 py-2">
          <h4 className="text-xs font-semibold text-blue-300 uppercase tracking-wider mb-1">Validation plan</h4>
          <p className="text-sm text-gray-200 leading-relaxed">{c.validation_plan}</p>
        </div>
      )}

      {/* Monitor positions — actionable, market-by-market */}
      {c.monitor_positions && c.monitor_positions.length > 0 && (
        <div className="border-t border-gray-700 pt-3 bg-green-950/15 -mx-4 px-4 py-2">
          <h4 className="text-xs font-semibold text-green-300 uppercase tracking-wider mb-2">
            🎯 Monitor — high-conviction positions in our cities
          </h4>
          <div className="space-y-2">
            {c.monitor_positions.map((m, i) => (
              <MonitorRow key={i} m={m} />
            ))}
          </div>
        </div>
      )}

      {/* Adversarial check */}
      <div className="border-t border-gray-700 pt-3 bg-red-950/10 -mx-4 px-4 py-2">
        <h4 className="text-xs font-semibold text-red-300 uppercase tracking-wider mb-1">⚠ Adversarial check</h4>
        <p className="text-sm text-gray-200 leading-relaxed">{c.adversarial_check}</p>
      </div>

      {modelInfo}
    </div>
  )
}

function MonitorRow({ m }: { m: MonitorPosition }) {
  const urgencyClr = m.urgency === 'high' ? 'text-red-300'
    : m.urgency === 'medium' ? 'text-yellow-300' : 'text-gray-400'
  const sideClr = m.trader_side?.toLowerCase() === 'no' ? 'text-red-400' : 'text-green-400'
  const sign = m.trader_cost_usd < 0 ? '-' : ''
  const cost = Math.abs(m.trader_cost_usd)
  const costFmt = cost >= 1000 ? `${sign}$${(cost / 1000).toFixed(2)}K` : `${sign}$${cost.toFixed(0)}`
  return (
    <div className="bg-gray-900/40 rounded p-2.5 border border-gray-700/50">
      <div className="flex items-start justify-between gap-3 mb-1">
        <div className="text-sm text-gray-200 leading-snug flex-1">{m.market_title}</div>
        <span className={`text-xs font-bold uppercase shrink-0 ${urgencyClr}`}>{m.urgency}</span>
      </div>
      <div className="flex items-center gap-3 text-xs font-mono text-gray-500 mb-1.5">
        <span>trader: <span className={sideClr}>{m.trader_side?.toUpperCase()}</span> @ <span className="text-gray-300">${m.trader_entry_price.toFixed(3)}</span></span>
        <span>cost: <span className="text-gray-300">{costFmt}</span></span>
      </div>
      <div className="text-xs text-green-200 leading-relaxed">→ {m.fade_trigger}</div>
    </div>
  )
}

function RawFallback({ markdown, parseError }: { markdown: string; parseError: string | null }) {
  return (
    <div>
      <div className="text-xs text-yellow-400 mb-2 italic">
        ⚠ Could not parse structured output — displaying raw response.
        {parseError && <span className="ml-1 text-gray-500">({parseError})</span>}
      </div>
      <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono">{markdown}</pre>
    </div>
  )
}

export default function Commentary({ runId }: { runId: number }) {
  const [data, setData] = useState<CommentaryResponse | null>(null)
  const [loading, setLoading] = useState<'standard' | 'deep' | ''>('')
  const [error, setError] = useState('')
  const [showRaw, setShowRaw] = useState(false)

  async function run(mode: 'standard' | 'deep') {
    setError('')
    setLoading(mode)
    setShowRaw(false)
    try {
      const result = await fetchCommentary(runId, mode)
      setData(result)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading('')
    }
  }

  const modelInfo = data && (
    <div className="pt-3 border-t border-gray-700 text-xs text-gray-500 font-mono flex gap-4 flex-wrap items-center">
      <span>model: <span className="text-gray-400">{data.model_used}</span></span>
      <span>cost: <span className="text-gray-400">${data.cost_usd.toFixed(4)}</span></span>
      {data.tokens && (
        <>
          <span>in: <span className="text-gray-400">{data.tokens.input}</span></span>
          <span>out: <span className="text-gray-400">{data.tokens.output}</span></span>
          {data.tokens.cache_read > 0 && <span>cached: <span className="text-gray-400">{data.tokens.cache_read}</span></span>}
        </>
      )}
      {data.structured && (
        <button
          onClick={() => setShowRaw((v) => !v)}
          className="ml-auto text-gray-500 hover:text-gray-300"
        >
          {showRaw ? 'hide raw' : 'show raw'}
        </button>
      )}
    </div>
  )

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">AI Commentary</h3>
        <div className="flex gap-2">
          <button
            onClick={() => run('standard')}
            disabled={!!loading}
            className="text-xs px-3 py-1.5 rounded bg-blue-700 hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold transition-colors"
          >
            {loading === 'standard' ? 'Analyzing…' : data ? 'Re-run (Sonnet)' : 'Analyze (Sonnet)'}
          </button>
          <button
            onClick={() => run('deep')}
            disabled={!!loading}
            className="text-xs px-3 py-1.5 rounded bg-purple-700 hover:bg-purple-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold transition-colors"
            title="Slower, deeper analysis using Opus 4.7 (~10¢)"
          >
            {loading === 'deep' ? 'Thinking…' : 'Deep Dive (Opus)'}
          </button>
        </div>
      </div>

      {error && <div className="text-xs text-red-400 mb-3">{error}</div>}

      {!data && !loading && (
        <p className="text-sm text-gray-500 italic">
          Click "Analyze" to generate a structured analysis using your current strategy context.
        </p>
      )}

      {data && data.structured && !showRaw && (
        <StructuredView c={data.structured} modelInfo={modelInfo} />
      )}

      {data && (showRaw || !data.structured) && (
        <>
          <RawFallback markdown={data.markdown} parseError={data.parse_error} />
          {modelInfo}
        </>
      )}
    </div>
  )
}
