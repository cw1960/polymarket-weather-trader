import { useMissionControl, type GuardrailStatus, type FlagState, type DecisionRow, type LiveActivityRow, type ResultRow } from '../hooks/useMissionControl'

// ── small UI helpers ───────────────────────────────────────────────────────

function secondsAgoLabel(s: number | null): { text: string; color: string } {
  if (s == null) return { text: 'no activity yet', color: 'text-gray-500' }
  if (s < 30)   return { text: `${s}s ago`,            color: 'text-green-400' }
  if (s < 90)   return { text: `${s}s ago`,            color: 'text-green-400' }
  if (s < 300)  return { text: `${Math.floor(s/60)}m ${s%60}s ago`, color: 'text-yellow-400' }
  if (s < 900)  return { text: `${Math.floor(s/60)}m ago`,          color: 'text-yellow-500' }
  return        { text: `${Math.floor(s/60)}m ago — STALE`,         color: 'text-red-400' }
}

function ActivityRow({ r }: { r: LiveActivityRow }) {
  const isSelected = r.side_selected != null
  const isPaper    = (r.size_usd ?? 0) <= 1.0
  const sideColor  = r.side_selected === 'NO' ? 'text-orange-400'
                   : r.side_selected === 'YES' ? 'text-green-400'
                   : 'text-gray-500'
  const gateBadge  = r.gate_passed
    ? <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-green-900/60 text-green-300">GATE✓</span>
    : <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-800 text-gray-500">below gate</span>
  const actionBadge = isSelected
    ? (isPaper
        ? <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-orange-900/60 text-orange-300">📝 PAPER</span>
        : <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-blue-900/60 text-blue-300">💰 LIVE</span>)
    : <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-900 text-gray-600">skip</span>
  const t = new Date(r.evaluated_at)
  const hh = t.getUTCHours().toString().padStart(2,'0')
  const mm = t.getUTCMinutes().toString().padStart(2,'0')
  const ss = t.getUTCSeconds().toString().padStart(2,'0')
  return (
    <div className="grid grid-cols-[60px_120px_70px_1fr_60px_60px_60px_70px_50px_70px] gap-2 items-center py-1 px-2 border-b border-gray-900 hover:bg-gray-900/40 text-xs">
      <span className="font-mono text-gray-500">{hh}:{mm}:{ss}</span>
      <span className="text-gray-300 truncate">{r.city}</span>
      <span className="text-gray-400">{r.cycle === 'phase2_sweep' ? 'NO sweep' : r.cycle}</span>
      <span className="font-mono text-gray-300">{r.bracket_label}</span>
      <span className={`font-mono ${sideColor}`}>{r.model_prob_no != null ? `${(r.model_prob_no * 100).toFixed(0)}%` : '—'}</span>
      <span className="font-mono text-gray-500">{r.no_price != null ? `${(r.no_price * 100).toFixed(0)}¢` : '—'}</span>
      <span className="font-mono text-gray-400">{r.edge_no != null ? `+${(r.edge_no * 100).toFixed(0)}pp` : '—'}</span>
      <span>{gateBadge}</span>
      <span className="font-mono text-gray-300">{r.side_selected ?? '—'}</span>
      <span>{actionBadge}</span>
    </div>
  )
}

function ResultRowDisplay({ r }: { r: ResultRow }) {
  const sideColor  = r.side === 'NO' ? 'text-orange-400' : 'text-green-400'
  const wonBadge   = r.won
    ? <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-green-900/60 text-green-300">✓ WON</span>
    : <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-900/60 text-red-300">✗ LOST</span>
  const pnlColor   = r.simulated_pnl >= 0 ? 'text-green-400' : 'text-red-400'
  const pnlStr     = r.simulated_pnl >= 0 ? `+$${r.simulated_pnl.toFixed(2)}` : `-$${Math.abs(r.simulated_pnl).toFixed(2)}`
  const phaseLabel = r.signal_phase === 'phase2_sweep' ? 'NO sweep'
                   : r.signal_phase === 'phase2'       ? 'YES lock'
                   : r.signal_phase ?? '—'
  const t = new Date(r.resolved_at)
  const datelabel = t.toUTCString().slice(5, 16) // "21 May 2026"
  const tlabel    = t.toUTCString().slice(17, 22) // "16:00"
  return (
    <div className="grid grid-cols-[110px_120px_70px_1fr_60px_70px_70px_90px] gap-2 items-center py-1 px-2 border-b border-gray-900 hover:bg-gray-900/40 text-xs">
      <span className="font-mono text-gray-500">{datelabel} {tlabel}</span>
      <span className="text-gray-300 truncate">{r.city}</span>
      <span className="text-gray-400">{phaseLabel}</span>
      <span className="font-mono text-gray-300">{r.side === 'NO' ? 'NO ' : 'YES '}{r.outcome}</span>
      <span className={`font-mono ${sideColor}`}>{(r.market_price * 100).toFixed(0)}¢</span>
      <span>{wonBadge}</span>
      <span className={`font-mono ${pnlColor}`}>{pnlStr}</span>
      <span className="text-[10px] text-gray-600">at week-1 size</span>
    </div>
  )
}

function LiveResultsPanel({ lr }: { lr: import('../hooks/useMissionControl').LiveResults }) {
  const wrColor  = (lr.winRate ?? 0) >= 0.55 ? 'text-green-400'
                 : (lr.winRate ?? 0) >= 0.45 ? 'text-yellow-400' : 'text-red-400'
  const pnlColor = lr.simulatedCumulativePnl >= 0 ? 'text-green-400' : 'text-red-400'
  const pnlNoColor = lr.simulatedCumulativePnlNoOnly >= 0 ? 'text-green-400' : 'text-red-400'
  const pnlStr  = (n: number) => (n >= 0 ? '+' : '−') + '$' + Math.abs(n).toFixed(2)
  const evStr   = (n: number | null) => n == null ? '—' : `${n >= 0 ? '+' : ''}${(n * 100).toFixed(1)}%`
  const evColor = (n: number | null) =>
    n == null            ? 'text-gray-500'
    : n >= 0.05          ? 'text-green-400'
    : n >= 0             ? 'text-yellow-400'
    : n >= -0.02         ? 'text-orange-400'
    :                      'text-red-400'
  return (
    <div className="rounded-lg border-2 border-purple-700/60 bg-purple-950/20 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-xs uppercase tracking-wider text-purple-300 font-bold">📊 Live Results</span>
          <span className="text-xs text-gray-400">
            EV per $ is the real profit metric — not win rate. Sim sizing: $5 NO sweep, $3 YES lock.
          </span>
        </div>
        <div className="text-xs text-gray-500">Updates as markets resolve (~daily after midnight UTC)</div>
      </div>

      {/* HEADLINE ROW — EV-centric */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <div className="rounded border-2 border-purple-700/40 bg-black/40 p-3">
          <div className="text-xs text-purple-300 uppercase font-bold">★ Realized EV / $</div>
          <div className={`text-3xl font-mono ${evColor(lr.realizedEvPerDollarNoOnly)}`}>
            {evStr(lr.realizedEvPerDollarNoOnly)}
          </div>
          <div className="text-xs text-gray-400 mt-1">NO-side only — the actual strategy</div>
          <div className="text-xs text-gray-600 mt-1">target ≥ +5% · pause if &lt; -2% (last 50)</div>
        </div>
        <div className="rounded border border-gray-800 bg-black/30 p-3">
          <div className="text-xs text-gray-500 uppercase">Last-50 NO EV / $</div>
          <div className={`text-2xl font-mono ${evColor(lr.realizedEvPerDollarLast50)}`}>
            {evStr(lr.realizedEvPerDollarLast50)}
          </div>
          <div className="text-xs text-gray-400 mt-1">rolling 50-trade window</div>
          <div className="text-xs text-gray-600 mt-1">EV-guardrail input</div>
        </div>
        <div className="rounded border border-gray-800 bg-black/30 p-3">
          <div className="text-xs text-gray-500 uppercase">Sim cumulative P&L</div>
          <div className={`text-2xl font-mono ${pnlNoColor}`}>{pnlStr(lr.simulatedCumulativePnlNoOnly)}</div>
          <div className="text-xs text-gray-400 mt-1">NO side, week-1 sizes</div>
          <div className="text-xs text-gray-600 mt-1">
            best: {pnlStr(lr.bestTradePnl)} · worst: {pnlStr(lr.worstTradePnl)}
          </div>
        </div>
        <div className="rounded border border-gray-800 bg-black/30 p-3">
          <div className="text-xs text-gray-500 uppercase">Resolved</div>
          <div className="text-2xl font-mono text-purple-300">{lr.totalResolved}</div>
          <div className="text-xs text-gray-400 mt-1">paper trades closed</div>
          <div className="text-xs text-gray-600 mt-1">
            {lr.totalWins}W · {lr.totalResolved - lr.totalWins}L · WR{' '}
            <span className={wrColor}>{lr.winRate != null ? `${(lr.winRate * 100).toFixed(0)}%` : '—'}</span>
          </div>
        </div>
      </div>

      {/* legacy all-phases P&L kept as small footnote so it's still accessible */}
      <div className="text-[10px] text-gray-600 mb-3">
        All-phases sim P&L (includes Phase 1 paper at $0):{' '}
        <span className={`font-mono ${pnlColor}`}>{pnlStr(lr.simulatedCumulativePnl)}</span>
        {'  '}— useful only for cross-checking; the strategy is NO sweep.
      </div>

      {/* Stream header */}
      <div className="grid grid-cols-[110px_120px_70px_1fr_60px_70px_70px_90px] gap-2 items-center py-1 px-2 border-b border-gray-700 text-[10px] uppercase tracking-wider text-gray-500 font-bold">
        <span>Resolved (UTC)</span>
        <span>City</span>
        <span>Cycle</span>
        <span>Trade</span>
        <span>Price</span>
        <span>Result</span>
        <span>Sim P&L</span>
        <span></span>
      </div>

      {/* Stream */}
      <div className="max-h-[480px] overflow-y-auto">
        {lr.recentStream.length === 0 ? (
          <div className="px-3 py-6 text-sm text-gray-500 text-center">
            No resolved trades yet since testing baseline.
            The first paper trades will resolve overnight UTC as markets close.
          </div>
        ) : (
          lr.recentStream.map((r) => <ResultRowDisplay key={r.id} r={r} />)
        )}
      </div>
    </div>
  )
}

function EdgeQualityPanel({ lr }: { lr: import('../hooks/useMissionControl').LiveResults }) {
  const evStr   = (n: number) => `${n >= 0 ? '+' : ''}${(n * 100).toFixed(1)}%`
  const evColor = (n: number) =>
    n >= 0.10  ? 'text-green-400'
    : n >= 0   ? 'text-yellow-400'
    : n >= -0.05 ? 'text-orange-400'
    :              'text-red-400'

  function bucketTable(title: string, rows: import('../hooks/useMissionControl').EvBucket[], formatBucket: (b: number | string) => string) {
    if (rows.length === 0) return null
    return (
      <div className="rounded border border-gray-800 bg-black/20 p-3">
        <div className="text-xs uppercase tracking-wider text-gray-400 mb-2 font-bold">{title}</div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-600 border-b border-gray-800">
              <th className="text-left py-1">Bucket</th>
              <th className="text-right py-1">n</th>
              <th className="text-right py-1">WR</th>
              <th className="text-right py-1">EV/$</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={String(b.bucket)} className="border-b border-gray-900">
                <td className="py-1 font-mono text-gray-300">{formatBucket(b.bucket)}</td>
                <td className="py-1 text-right font-mono text-gray-400">{b.n}</td>
                <td className="py-1 text-right font-mono text-gray-400">
                  {b.n >= 3 ? `${(b.winRate * 100).toFixed(0)}%` : '—'}
                </td>
                <td className={`py-1 text-right font-mono ${b.n >= 3 ? evColor(b.evPerDollar) : 'text-gray-600'}`}>
                  {b.n >= 3 ? evStr(b.evPerDollar) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="text-[10px] text-gray-600 mt-1">Buckets with n&lt;3 hidden; not statistically meaningful.</div>
      </div>
    )
  }

  const cityTop    = lr.evByCity.slice(0, 5)
  const cityBottom = lr.evByCity.slice(-5).reverse()

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs uppercase tracking-wider text-gray-400 font-bold">🔬 Edge Quality (NO-side only)</div>
        <div className="text-xs text-gray-500">EV per $ broken out by model prob, market price, and city</div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {bucketTable('EV by predicted NO prob',  lr.evByProbBucket,  (b) => `${(Number(b) * 100).toFixed(0)}%`)}
        {bucketTable('EV by NO entry price',     lr.evByPriceBucket, (b) => `${(Number(b) * 100).toFixed(0)}¢`)}
        <div className="rounded border border-gray-800 bg-black/20 p-3">
          <div className="text-xs uppercase tracking-wider text-gray-400 mb-2 font-bold">EV by city (top 5 / bottom 5)</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="text-left py-1">City</th>
                <th className="text-right py-1">n</th>
                <th className="text-right py-1">EV/$</th>
              </tr>
            </thead>
            <tbody>
              {cityTop.map((b) => (
                <tr key={'t-' + String(b.bucket)} className="border-b border-gray-900">
                  <td className="py-1 text-gray-300">{String(b.bucket)}</td>
                  <td className="py-1 text-right font-mono text-gray-400">{b.n}</td>
                  <td className={`py-1 text-right font-mono ${b.n >= 3 ? evColor(b.evPerDollar) : 'text-gray-600'}`}>
                    {b.n >= 3 ? evStr(b.evPerDollar) : '—'}
                  </td>
                </tr>
              ))}
              <tr><td colSpan={3} className="py-1 text-center text-gray-600 text-[10px]">— bottom 5 —</td></tr>
              {cityBottom.map((b) => (
                <tr key={'b-' + String(b.bucket)} className="border-b border-gray-900">
                  <td className="py-1 text-gray-300">{String(b.bucket)}</td>
                  <td className="py-1 text-right font-mono text-gray-400">{b.n}</td>
                  <td className={`py-1 text-right font-mono ${b.n >= 3 ? evColor(b.evPerDollar) : 'text-gray-600'}`}>
                    {b.n >= 3 ? evStr(b.evPerDollar) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function LiveBotActivity({ la }: { la: import('../hooks/useMissionControl').LiveActivity }) {
  const heart = secondsAgoLabel(la.secondsSinceActivity)
  return (
    <div className="rounded-lg border-2 border-cyan-700/60 bg-cyan-950/20 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-xs uppercase tracking-wider text-cyan-300 font-bold">🤖 Live Bot Activity</span>
          <span className={`text-sm font-mono ${heart.color}`}>
            ⬤ last cycle: {heart.text}
          </span>
        </div>
        <div className="text-xs text-gray-500">
          (auto-refreshes every 10s — temp_monitor cron fires every 5 min)
        </div>
      </div>

      {/* Big stat boxes */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
        <div className="rounded border border-gray-800 bg-black/30 p-3">
          <div className="text-xs text-gray-500 uppercase">Last hour</div>
          <div className="text-2xl font-mono text-cyan-300">{la.lastHourEvals.toLocaleString()}</div>
          <div className="text-xs text-gray-400 mt-1">brackets evaluated</div>
          <div className="text-xs text-gray-600 mt-2">
            {la.lastHourGatePass} passed gate · {la.lastHourSelected} selected for trade
          </div>
        </div>
        <div className="rounded border border-gray-800 bg-black/30 p-3">
          <div className="text-xs text-gray-500 uppercase">Today (UTC)</div>
          <div className="text-2xl font-mono text-cyan-300">{la.todayEvals.toLocaleString()}</div>
          <div className="text-xs text-gray-400 mt-1">brackets evaluated</div>
          <div className="text-xs text-gray-600 mt-2">
            {la.todayGatePass} passed gate · {la.todaySelected} selected for trade
          </div>
        </div>
        <div className="rounded border border-gray-800 bg-black/30 p-3">
          <div className="text-xs text-gray-500 uppercase">Pass rate</div>
          <div className="text-2xl font-mono text-cyan-300">
            {la.todayEvals > 0 ? `${(la.todayGatePass / la.todayEvals * 100).toFixed(1)}%` : '—'}
          </div>
          <div className="text-xs text-gray-400 mt-1">of brackets cleared gate</div>
          <div className="text-xs text-gray-600 mt-2">
            (expect roughly 5-15% on healthy data)
          </div>
        </div>
      </div>

      {/* Stream header */}
      <div className="grid grid-cols-[60px_120px_70px_1fr_60px_60px_60px_70px_50px_70px] gap-2 items-center py-1 px-2 border-b border-gray-700 text-[10px] uppercase tracking-wider text-gray-500 font-bold">
        <span>Time (UTC)</span>
        <span>City</span>
        <span>Cycle</span>
        <span>Bracket</span>
        <span>p_NO</span>
        <span>NO price</span>
        <span>Edge</span>
        <span>Gate</span>
        <span>Side</span>
        <span>Action</span>
      </div>

      {/* Stream rows */}
      <div className="max-h-[520px] overflow-y-auto">
        {la.recentStream.length === 0 ? (
          <div className="px-3 py-6 text-sm text-gray-500 text-center">
            No activity yet. Once temp_monitor fires its next cycle and any city passes 14:00 local, rows will stream in here.
          </div>
        ) : (
          la.recentStream.map((r) => <ActivityRow key={r.id} r={r} />)
        )}
      </div>

      <div className="mt-2 text-[10px] text-gray-600">
        Gate-passing rows shown first, then most recent non-passing rows. Sized for ~25 rows; scroll for more.
      </div>
    </div>
  )
}

function StatusDot({ blocked }: { blocked: boolean }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${
        blocked ? 'bg-red-500' : 'bg-green-500'
      }`}
    />
  )
}

function GuardrailCard({ g }: { g: GuardrailStatus }) {
  return (
    <div
      className={`rounded-lg border p-3 ${
        g.blocked ? 'border-red-700 bg-red-950/40' : 'border-green-800 bg-green-950/30'
      }`}
    >
      <div className="flex items-center gap-2">
        <StatusDot blocked={g.blocked} />
        <span className="text-sm font-medium text-gray-200">{g.label}</span>
      </div>
      <div className="mt-1 text-xs text-gray-400 leading-tight">{g.reason}</div>
      {g.metric && <div className="mt-1 text-xs font-mono text-gray-500">{g.metric}</div>}
    </div>
  )
}

function FlagRow({ f }: { f: FlagState }) {
  const isCriticalOff = f.key === 'phase2_paused' && f.value !== '1'
  const isYesOn = f.key === 'phase2_yes_locks_enabled' && f.value === '1'
  const valueColor =
    isCriticalOff
      ? 'text-red-400'
      : isYesOn
        ? 'text-yellow-400'
        : f.value === '1'
          ? 'text-yellow-400'
          : 'text-gray-300'
  return (
    <div className="grid grid-cols-[1fr_auto] gap-3 items-start py-2 border-b border-gray-800 last:border-b-0">
      <div>
        <div className="text-sm font-medium text-gray-200">{f.label}</div>
        <div className="text-xs text-gray-500 mt-0.5">{f.description}</div>
        <code className="block text-[10px] text-gray-600 mt-1">{f.key}</code>
      </div>
      <div className={`text-2xl font-mono ${valueColor}`}>
        {f.value === null ? '—' : f.value}
      </div>
    </div>
  )
}

function DecisionRow({ d }: { d: DecisionRow }) {
  const size = Number(d.filled_size_usd ?? d.recommended_position ?? 0)
  const actionColor =
    d.inferredAction === 'real-money'
      ? 'text-blue-400'
      : d.inferredAction === 'observation'
        ? 'text-gray-500'
        : d.inferredAction === 'failed'
          ? 'text-red-400'
          : 'text-yellow-400'
  const when = new Date(d.signal_time).toUTCString().slice(5, 22) + ' UTC'
  const edge = d.edge != null ? `${(d.edge * 100).toFixed(1)}pp` : '—'
  const modelP = d.model_probability != null ? `${(d.model_probability * 100).toFixed(0)}%` : '—'
  const price  = d.market_price != null ? `${(d.market_price * 100).toFixed(0)}¢` : '—'
  return (
    <tr className="border-b border-gray-900 hover:bg-gray-900/40">
      <td className="py-1.5 px-2 text-xs text-gray-500 font-mono">{when}</td>
      <td className="py-1.5 px-2 text-xs text-gray-300">{d.city}</td>
      <td className="py-1.5 px-2 text-xs">
        <span className={d.side === 'YES' ? 'text-green-400' : 'text-orange-400'}>{d.side}</span>{' '}
        <span className="text-gray-400">{d.outcome}</span>
      </td>
      <td className="py-1.5 px-2 text-xs font-mono text-gray-400">{modelP} / {price}</td>
      <td className="py-1.5 px-2 text-xs font-mono text-gray-400">{edge}</td>
      <td className="py-1.5 px-2 text-xs font-mono text-gray-200">${size.toFixed(2)}</td>
      <td className={`py-1.5 px-2 text-xs font-medium ${actionColor}`}>{d.inferredAction}</td>
      <td className="py-1.5 px-2 text-xs font-mono">
        {d.pnl_usd != null ? (
          <span className={d.pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'}>
            {d.pnl_usd >= 0 ? '+' : ''}${d.pnl_usd.toFixed(2)}
          </span>
        ) : (
          <span className="text-gray-600">—</span>
        )}
      </td>
    </tr>
  )
}

// ── main component ─────────────────────────────────────────────────────────

export default function MissionControl() {
  const mc = useMissionControl()
  const allClear = mc.guardrails.every((g) => !g.blocked)

  return (
    <div className="px-6 py-4 space-y-5">
      {/* Paper-testing mode banner (always visible at top) */}
      <div
        className={`rounded-lg p-2 border ${
          mc.effectiveTradingMode === 'PAPER'
            ? 'border-orange-700 bg-orange-950/30'
            : 'border-green-700 bg-green-950/30'
        }`}
      >
        <div className="flex items-center justify-between">
          <div>
            <span className={`text-sm font-bold ${mc.effectiveTradingMode === 'PAPER' ? 'text-orange-300' : 'text-green-300'}`}>
              {mc.effectiveTradingMode === 'PAPER' ? '📝 PAPER TESTING MODE' : '💰 LIVE TRADING MODE'}
            </span>
            <span className="text-xs text-gray-400 ml-3">
              Mode is derived from guardrail state — not a manual flag. PAPER until every guardrail is clear AND YES-locks are enabled.
            </span>
          </div>
          {mc.paperTesting && (
            <div className="text-xs text-gray-400">
              Testing since {mc.paperTesting.baselineIso.slice(0, 10)}
            </div>
          )}
        </div>
      </div>

      {/* 🤖 LIVE BOT ACTIVITY — the panel you actually want to watch */}
      {mc.liveActivity && <LiveBotActivity la={mc.liveActivity} />}

      {/* 📊 LIVE RESULTS — resolved paper trades + simulated P&L */}
      {mc.liveResults && <LiveResultsPanel lr={mc.liveResults} />}

      {/* 🔬 EDGE QUALITY — EV broken out by prob / price / city */}
      {mc.liveResults && <EdgeQualityPanel lr={mc.liveResults} />}

      {/* Top banner — overall trade allowance */}
      <div
        className={`rounded-lg p-3 border ${
          allClear
            ? 'border-green-700 bg-green-950/40'
            : 'border-red-700 bg-red-950/40'
        }`}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <StatusDot blocked={!allClear} />
            <div>
              <div className="text-sm font-medium text-gray-100">
                {allClear ? 'All guardrails clear — trades allowed' : 'Trades blocked by at least one guardrail'}
              </div>
              <div className="text-xs text-gray-400 mt-0.5">
                Polled live from system_config + trade_signals. Refreshes every 30s.
              </div>
            </div>
          </div>
          <div className="text-xs text-gray-500">
            {mc.lastRefreshed ? `Updated ${mc.lastRefreshed.toLocaleTimeString()}` : 'Loading...'}
            <button
              onClick={mc.refresh}
              className="ml-3 px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            >
              ↻
            </button>
          </div>
        </div>
      </div>

      {/* Guardrail row */}
      <div>
        <div className="text-xs uppercase tracking-wider text-gray-500 mb-2">Guardrails</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {mc.guardrails.map((g) => <GuardrailCard key={g.name} g={g} />)}
        </div>
      </div>

      {/* Flags + Sizing side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-gray-500 mb-2">Operational Flags</div>
          {mc.flags.map((f) => <FlagRow key={f.key} f={f} />)}
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-gray-500 hover:text-gray-300">
              How to flip a flag (intentional friction — Rule 6)
            </summary>
            <pre className="mt-2 text-[10px] text-gray-400 leading-tight whitespace-pre-wrap">{`Run from Supabase SQL editor or VPS:

UPDATE system_config SET value = '0' WHERE key = 'phase2_paused';

Always verify on the Mission Control page that the
value updated as expected before any further work.`}</pre>
          </details>
        </div>

        <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-gray-500 mb-2">Current Sizing Window</div>
          {mc.sizing ? (
            <div className="space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-400">Week:</span>
                <span className="font-mono text-gray-200">{mc.sizing.week_label}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Window:</span>
                <span className="font-mono text-gray-200">{mc.sizing.start_date} → {mc.sizing.end_date}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">YES lock size:</span>
                <span className="font-mono text-gray-200">${mc.sizing.phase2_yes_size_usd.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">NO sweep size / bracket:</span>
                <span className="font-mono text-gray-200">${mc.sizing.phase2_no_sweep_size_usd.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Max NO brackets / city:</span>
                <span className="font-mono text-gray-200">{mc.sizing.phase2_no_sweep_max_per_city}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Deployment cap:</span>
                <span className="font-mono text-gray-200">{mc.sizing.deployment_cap_pct}% of bankroll</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Kelly fraction:</span>
                <span className="font-mono text-gray-200">{mc.sizing.kelly_fraction.toFixed(3)}</span>
              </div>
              {mc.sizing.notes && (
                <div className="text-xs text-gray-500 mt-2 pt-2 border-t border-gray-800">{mc.sizing.notes}</div>
              )}
            </div>
          ) : (
            <div className="text-sm text-yellow-400">
              ⚠ No sizing_schedule row covers today.
              <div className="text-xs text-gray-500 mt-1">
                The sizing module falls back to $0.01 safe defaults — any trade attempt becomes an observation.
                Insert a row for the current week to enable real-money sizing.
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Paper testing — segregated view since baseline */}
      {mc.paperTesting && (
        <div className="rounded-lg border border-orange-900/60 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-orange-400 mb-3">
            Paper Testing Since {mc.paperTesting.baselineIso.slice(0, 10)}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-3">
            <div className="rounded border border-gray-800 bg-gray-900/40 p-2">
              <div className="text-xs text-gray-500">Total signals</div>
              <div className="text-2xl font-mono text-gray-100">{mc.paperTesting.totalSignals}</div>
              <div className="text-[10px] text-gray-600">
                P1 {mc.paperTesting.phase1Signals} · P2-YES {mc.paperTesting.phase2YesSignals} · Sweep {mc.paperTesting.phase2SweepSignals}
              </div>
            </div>
            <div className="rounded border border-gray-800 bg-gray-900/40 p-2">
              <div className="text-xs text-gray-500">Resolved</div>
              <div className="text-2xl font-mono text-gray-100">{mc.paperTesting.resolvedSignals}</div>
              <div className="text-[10px] text-gray-600">
                of {mc.paperTesting.totalSignals} total
              </div>
            </div>
            <div className="rounded border border-gray-800 bg-gray-900/40 p-2">
              <div className="text-xs text-gray-500">Wins</div>
              <div className="text-2xl font-mono text-green-400">{mc.paperTesting.resolvedWins}</div>
              <div className="text-[10px] text-gray-600">
                vs {mc.paperTesting.resolvedSignals - mc.paperTesting.resolvedWins} losses
              </div>
            </div>
            <div className="rounded border border-gray-800 bg-gray-900/40 p-2">
              <div className="text-xs text-gray-500">Win rate</div>
              <div className="text-2xl font-mono text-gray-100">
                {mc.paperTesting.winRate != null ? `${(mc.paperTesting.winRate * 100).toFixed(0)}%` : '—'}
              </div>
              <div className="text-[10px] text-gray-600">
                {mc.paperTesting.resolvedSignals < 10 ? 'need ≥10 resolved for meaning' : 'rolling rate'}
              </div>
            </div>
            <div className="rounded border border-gray-800 bg-gray-900/40 p-2">
              <div className="text-xs text-gray-500">Cities covered</div>
              <div className="text-2xl font-mono text-gray-100">{mc.paperTesting.citiesCovered}</div>
              <div className="text-[10px] text-gray-600">of 50 universe</div>
            </div>
          </div>

          {mc.paperTesting.calibrationBins.length > 0 && (
            <div className="mt-3">
              <div className="text-xs text-gray-500 mb-1">Live calibration — model prob bucket vs actual win rate</div>
              <div className="overflow-x-auto">
                <table className="text-xs">
                  <thead className="text-gray-600">
                    <tr>
                      <th className="px-2 py-1 text-left">model_prob</th>
                      {mc.paperTesting.calibrationBins.map((b) => (
                        <th key={b.bucket} className="px-2 py-1 text-right">{b.bucket.toFixed(1)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-t border-gray-800">
                      <td className="px-2 py-1 text-gray-500">n</td>
                      {mc.paperTesting.calibrationBins.map((b) => (
                        <td key={b.bucket} className="px-2 py-1 text-right font-mono text-gray-300">{b.n}</td>
                      ))}
                    </tr>
                    <tr>
                      <td className="px-2 py-1 text-gray-500">actual win rate</td>
                      {mc.paperTesting.calibrationBins.map((b) => (
                        <td key={b.bucket} className="px-2 py-1 text-right font-mono text-gray-200">
                          {b.n >= 5 ? `${(b.rate * 100).toFixed(0)}%` : '—'}
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="text-[10px] text-gray-600 mt-1">
                Rows with n&lt;5 show "—". Healthy calibration: rate ≈ model_prob (e.g. 0.7 bucket wins ~70%).
              </div>
            </div>
          )}

          {mc.paperTesting.totalSignals === 0 && (
            <div className="text-sm text-gray-500">
              No signals yet since baseline. Phase 1 fires every 6h; Phase 2 fires when temperature locks. Check back after the next signal_engine cron.
            </div>
          )}
        </div>
      )}

      {/* Recent Phase 2 / sweep decisions */}
      <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div className="text-xs uppercase tracking-wider text-gray-500 mb-2">
          Last 15 Phase 2 / Sweep Decisions
        </div>
        {mc.recentDecisions.length === 0 ? (
          <div className="text-sm text-gray-500">No Phase 2 or sweep signals yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-gray-500 border-b border-gray-800">
                <tr>
                  <th className="py-1.5 px-2 font-medium">Time (UTC)</th>
                  <th className="py-1.5 px-2 font-medium">City</th>
                  <th className="py-1.5 px-2 font-medium">Side / Outcome</th>
                  <th className="py-1.5 px-2 font-medium">Model / Price</th>
                  <th className="py-1.5 px-2 font-medium">Edge</th>
                  <th className="py-1.5 px-2 font-medium">Size</th>
                  <th className="py-1.5 px-2 font-medium">Action</th>
                  <th className="py-1.5 px-2 font-medium">P&L</th>
                </tr>
              </thead>
              <tbody>
                {mc.recentDecisions.map((d) => <DecisionRow key={d.id} d={d} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Guardrail-event audit log */}
      {mc.recentGuardrailEvents.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
          <div className="text-xs uppercase tracking-wider text-gray-500 mb-2">
            Recent Guardrail Events
          </div>
          <table className="w-full text-left text-xs">
            <thead className="text-gray-500 border-b border-gray-800">
              <tr>
                <th className="py-1.5 px-2 font-medium">Fired</th>
                <th className="py-1.5 px-2 font-medium">Guardrail</th>
                <th className="py-1.5 px-2 font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {mc.recentGuardrailEvents.map((e) => (
                <tr key={e.id} className="border-b border-gray-900">
                  <td className="py-1.5 px-2 font-mono text-gray-500">
                    {new Date(e.fired_at).toUTCString().slice(5, 22)}
                  </td>
                  <td className="py-1.5 px-2 text-red-400">{e.guardrail}</td>
                  <td className="py-1.5 px-2 font-mono text-gray-400">
                    {e.details_json ? JSON.stringify(e.details_json) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
