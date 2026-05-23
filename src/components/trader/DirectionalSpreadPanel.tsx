// DirectionalSpreadPanel — interactive position builder + Kelly-sized
// allocator.
//
// Flow:
//   1. Per-bracket: user sees market YES ask (= market's implied
//      probability), and can OVERRIDE it with their own probability
//      estimate. Edits expose edge.
//   2. User clicks YES / NO buttons to construct a position.
//   3. Each selected bet has an editable $ allocation. Default = auto
//      (Kelly-fractional based on user's probabilities).
//   4. Per-outcome P&L table shows actual payoff if each bracket wins.
//   5. Aggregate stats: total stake, expected value (under user's
//      probabilities), worst-case P&L, best-case P&L.
//
// Math:
//   For each bet i:
//     cost_i        = ask price (per share)
//     p_i           = user's belief that THIS bet wins
//                     YES_X → user_prob(X);   NO_X → 1 − user_prob(X)
//     edge_i        = p_i − cost_i             (per-share EV)
//     EV-per-$      = p_i / cost_i − 1
//     Kelly_i       = (p_i − cost_i) / (1 − cost_i)   [single-bet Kelly]
//   Auto-size:
//     If Kelly_i > 0: stake_i = Kelly_i × kellyScale × bankroll
//     Else: 0
//   bankroll = the user's stake-budget input.

import { useMemo, useState } from 'react'
import type { LiveBracketTick } from '../../hooks/trader/useLivePolymarketEvent'


interface Props {
  liveByBracket: Record<string, LiveBracketTick>
  bracketsOrder?: { bracket_label: string; bracket_low_native: number | null; bracket_high_native: number | null }[]
}


interface BracketRow {
  label: string
  low: number
  high: number
  unit: 'F' | 'C'
  yesAsk: number | null         // best ask on YES = what you pay to BUY yes
  noAsk: number | null          // best ask on NO  = what you pay to BUY no
  yesMid: number | null         // midpoint (bid+ask)/2 = market's implied probability
                                //  (this is what Polymarket's UI displays as the "X%" number)
}


function parseLabel(label: string): { low: number; high: number; unit: 'F' | 'C' } | null {
  const leMatch = label.match(/^≤(-?\d+(?:\.\d+)?)°([FC])$/)
  const geMatch = label.match(/^≥(-?\d+(?:\.\d+)?)°([FC])$/)
  const rangeMatch = label.match(/^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)°([FC])$/)
  const singleMatch = label.match(/^(-?\d+(?:\.\d+)?)°([FC])$/)
  if (leMatch)
    return { low: -Infinity, high: parseFloat(leMatch[1]) + 0.5, unit: leMatch[2] as 'F'|'C' }
  if (geMatch)
    return { low: parseFloat(geMatch[1]) - 0.5, high: Infinity, unit: geMatch[2] as 'F'|'C' }
  if (rangeMatch)
    return { low: parseFloat(rangeMatch[1]) - 0.5, high: parseFloat(rangeMatch[2]) + 0.5, unit: rangeMatch[3] as 'F'|'C' }
  if (singleMatch)
    return { low: parseFloat(singleMatch[1]) - 0.5, high: parseFloat(singleMatch[1]) + 0.5, unit: singleMatch[2] as 'F'|'C' }
  return null
}


type BetSide = 'YES' | 'NO'


export default function DirectionalSpreadPanel({ liveByBracket, bracketsOrder }: Props) {
  const rows: BracketRow[] = useMemo(() => {
    const out: BracketRow[] = []
    for (const [label, tick] of Object.entries(liveByBracket)) {
      const parsed = parseLabel(label)
      if (!parsed) continue
      const yesAsk = tick.best_ask ?? tick.yes_price ?? null
      const noAsk  = tick.best_bid != null ? 1 - tick.best_bid : tick.no_price ?? null
      // Midpoint = market's "true" implied probability (matches what
      // Polymarket UI displays as the percentage). When we only have one
      // side, fall back to last-trade.
      const yesMid = (tick.best_bid != null && tick.best_ask != null)
        ? (tick.best_bid + tick.best_ask) / 2
        : tick.yes_price ?? null
      out.push({ label, ...parsed, yesAsk, noAsk, yesMid })
    }
    if (bracketsOrder) {
      const idx = new Map(bracketsOrder.map((b, i) => [b.bracket_label, i]))
      out.sort((a, b) => (idx.get(a.label) ?? 99) - (idx.get(b.label) ?? 99))
    } else {
      out.sort((a, b) => a.low - b.low)
    }
    return out
  }, [liveByBracket, bracketsOrder])

  // --- State ---------------------------------------------------------------

  // User's probability override per bracket. Stored as 0..1. If not present
  // for a label, we default to that bracket's market YES ask.
  const [myProbs, setMyProbs] = useState<Record<string, number>>({})

  // Selected bets and their $ allocations. Key = "SIDE:label", value = $.
  // When the user toggles a bet ON, an auto-sized default lands in here.
  const [betAmounts, setBetAmounts] = useState<Record<string, number>>({})

  // Bankroll for the Kelly auto-sizer. Acts as the "max you'd commit to
  // this position" budget.
  const [bankroll, setBankroll] = useState<number>(100)

  // Fractional Kelly multiplier. Pure Kelly = 1.0 (theoretically
  // log-optimal but high variance). 0.25 = quarter Kelly is the practical
  // default — much smoother equity curve, sacrifices a bit of long-run
  // growth for stability.
  const [kellyScale, setKellyScale] = useState<number>(0.25)

  // --- Derived -------------------------------------------------------------

  // Sum of raw midpoints across all brackets. Because of the bid-ask
  // spread this usually isn't exactly 100% — typically a few points off.
  // We use it to NORMALIZE the default probabilities so the "no manual
  // overrides" case sums cleanly to 100%.
  const midSum = useMemo(() => {
    return rows.reduce((s, r) => s + (r.yesMid ?? r.yesAsk ?? 0), 0)
  }, [rows])

  function effectiveProb(label: string): number {
    if (label in myProbs) return myProbs[label]
    const r = rows.find((x) => x.label === label)
    const rawMid = r?.yesMid ?? r?.yesAsk ?? 0
    // Default: NORMALIZED midpoint so all brackets sum to 100% even though
    // the raw market midpoints don't (due to bid-ask spread).
    return midSum > 0 ? rawMid / midSum : rawMid
  }

  const probSum = rows.reduce((s, r) => s + effectiveProb(r.label), 0)
  const probsCoherent = Math.abs(probSum - 1) < 0.05   // within 5pp of summing to 100%

  function betKey(side: BetSide, label: string): string { return `${side}:${label}` }
  function parseKey(key: string): { side: BetSide; label: string } {
    const idx = key.indexOf(':')
    return { side: key.slice(0, idx) as BetSide, label: key.slice(idx + 1) }
  }

  // For each ENTIRE bracket list (not just selected), compute per-bet stats.
  // This powers the table even before any bet is selected — so the user
  // can see edge before clicking.
  function betStats(side: BetSide, label: string) {
    const r = rows.find((x) => x.label === label)
    if (!r) return null
    const cost = side === 'YES' ? r.yesAsk : r.noAsk
    if (cost == null || cost <= 0 || cost >= 1) return null
    const winProb = side === 'YES' ? effectiveProb(label) : 1 - effectiveProb(label)
    const edge = winProb - cost                          // per-share EV
    const evPerDollar = winProb / cost - 1               // EV per $ staked
    const kelly = (winProb - cost) / (1 - cost)          // single-bet Kelly fraction
    return { cost, winProb, edge, evPerDollar, kelly }
  }

  // --- Auto-sizing ---------------------------------------------------------

  // Given current selections + bankroll + kellyScale, compute the auto
  // allocation. Negative-Kelly bets get $0 (the math says don't bet them).
  function autoAmountFor(side: BetSide, label: string): number {
    const s = betStats(side, label)
    if (!s) return 0
    if (s.kelly <= 0) return 0
    return s.kelly * kellyScale * bankroll
  }

  function reauto() {
    const next: Record<string, number> = {}
    for (const key of Object.keys(betAmounts)) {
      const { side, label } = parseKey(key)
      next[key] = autoAmountFor(side, label)
    }
    setBetAmounts(next)
  }

  function toggleBet(side: BetSide, label: string) {
    const key = betKey(side, label)
    setBetAmounts((prev) => {
      const next = { ...prev }
      if (key in next) delete next[key]
      else next[key] = autoAmountFor(side, label)    // default to Kelly-sized $
      return next
    })
  }

  function setAmount(key: string, amount: number) {
    setBetAmounts((prev) => ({ ...prev, [key]: Math.max(0, amount) }))
  }

  function clearAll() { setBetAmounts({}) }

  function resetProbs() { setMyProbs({}) }

  // --- Position math -------------------------------------------------------

  const selectedKeys = Object.keys(betAmounts)
  const totalStake = selectedKeys.reduce((s, k) => s + (betAmounts[k] || 0), 0)

  // For each possible winning bracket, compute the position's payout.
  interface OutcomeRow {
    label: string
    payout: number          // dollars
    pnl: number             // dollars (payout - totalStake)
    prob: number            // user's normalized probability
    contribEV: number       // prob × pnl  (contribution to total EV)
  }
  const outcomes: OutcomeRow[] = useMemo(() => {
    const probDenom = probSum > 0 ? probSum : 1     // safe normalization
    return rows.map((winRow) => {
      let payout = 0
      for (const key of selectedKeys) {
        const { side, label } = parseKey(key)
        const r = rows.find((x) => x.label === label)
        if (!r) continue
        const cost = side === 'YES' ? r.yesAsk : r.noAsk
        if (cost == null || cost <= 0) continue
        const shares = (betAmounts[key] || 0) / cost
        // Does this bet win if winRow is the actual winner?
        const wins = side === 'YES' ? (label === winRow.label) : (label !== winRow.label)
        if (wins) payout += shares
      }
      const pnl = payout - totalStake
      const prob = effectiveProb(winRow.label) / probDenom
      return { label: winRow.label, payout, pnl, prob, contribEV: prob * pnl }
    })
  }, [rows, selectedKeys, betAmounts, totalStake, probSum, myProbs])

  const expectedValue = outcomes.reduce((s, o) => s + o.contribEV, 0)
  const minPnl = outcomes.length ? Math.min(...outcomes.map((o) => o.pnl)) : 0
  const maxPnl = outcomes.length ? Math.max(...outcomes.map((o) => o.pnl)) : 0
  const guaranteedProfit = selectedKeys.length > 0 && minPnl > 0
  const guaranteedLoss   = selectedKeys.length > 0 && maxPnl < 0

  // --- Render --------------------------------------------------------------

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <div className="text-lg font-semibold text-gray-100">🎯 Directional spread</div>
          <div className="text-xs text-gray-400">
            Edit <span className="text-cyan-300">My %</span> per bracket (defaults to market).
            Click <span className="text-emerald-300">YES</span>/<span className="text-red-300">NO</span> to bet.
            EV-per-$ shows your edge; Kelly suggests sizing. All live.
          </div>
        </div>
        <div className="flex gap-2 text-xs">
          <button onClick={resetProbs}  className="px-2 py-1 rounded border border-gray-700 hover:border-cyan-700 text-gray-300">reset my %</button>
          <button onClick={reauto}      className="px-2 py-1 rounded border border-gray-700 hover:border-emerald-700 text-gray-300">re-auto size</button>
          <button onClick={clearAll}    className="px-2 py-1 rounded border border-gray-700 hover:border-red-700 text-gray-300">clear bets</button>
        </div>
      </div>

      {/* Probability sanity warning */}
      {!probsCoherent && (
        <div className="mb-3 text-xs px-3 py-1.5 rounded border border-amber-800/60 bg-amber-950/30 text-amber-300">
          ⚠ Your bracket probabilities sum to {(probSum * 100).toFixed(0)}% (should be ~100%).
          Outcome probabilities are auto-normalized for EV math, but consider rebalancing
          if you want intuitive results.
        </div>
      )}

      {/* Bracket table */}
      <div className="rounded border border-gray-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-gray-400 border-b border-gray-800 bg-gray-900/40">
            <tr>
              <th className="text-left  py-1.5 px-2 font-normal">Bracket</th>
              <th className="text-right py-1.5 px-2 font-normal" title="Market midpoint = market's implied probability (matches the % shown on Polymarket)">Mkt prob</th>
              <th className="text-center py-1.5 px-2 font-normal" title="Your probability estimate. Defaults to market midpoint.">My %</th>
              <th className="text-right py-1.5 px-2 font-normal" title="Best ask on YES — what you'd pay to buy YES right now">YES ask</th>
              <th className="text-right py-1.5 px-2 font-normal">YES edge / $</th>
              <th className="text-center py-1.5 px-2 font-normal">Bet YES</th>
              <th className="text-right py-1.5 px-2 font-normal" title="Best ask on NO — what you'd pay to buy NO right now">NO ask</th>
              <th className="text-right py-1.5 px-2 font-normal">NO edge / $</th>
              <th className="text-center py-1.5 px-2 font-normal">Bet NO</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const yesS = betStats('YES', r.label)
              const noS  = betStats('NO', r.label)
              const yesOn = betKey('YES', r.label) in betAmounts
              const noOn  = betKey('NO', r.label) in betAmounts
              const myProb = effectiveProb(r.label)
              return (
                <tr key={r.label} className="border-t border-gray-900 hover:bg-gray-900/30">
                  <td className="py-2 px-2 text-gray-200 font-medium">{r.label}</td>
                  <td className="py-2 px-2 text-right font-mono text-gray-300">
                    {r.yesMid != null ? `${(r.yesMid * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-2">
                    <input
                      type="number" min={0} max={100} step={1}
                      value={Math.round(myProb * 100)}
                      onChange={(e) => {
                        const v = Math.max(0, Math.min(100, Number(e.target.value) || 0)) / 100
                        setMyProbs((prev) => ({ ...prev, [r.label]: v }))
                      }}
                      className={`w-16 bg-gray-900 border ${r.label in myProbs ? 'border-cyan-700' : 'border-gray-700'} rounded px-1 py-0.5 text-right font-mono text-xs`}
                    />
                    <span className="text-gray-500 text-xs ml-1">%</span>
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-xs text-gray-400">
                    {r.yesAsk != null ? `${(r.yesAsk * 100).toFixed(1)}¢` : '—'}
                  </td>
                  <td className={`py-2 px-2 text-right font-mono text-xs ${yesS && yesS.evPerDollar > 0 ? 'text-emerald-300' : yesS && yesS.evPerDollar < 0 ? 'text-red-300' : 'text-gray-500'}`}>
                    {yesS ? `${(yesS.evPerDollar * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-2 text-center">
                    <button
                      onClick={() => toggleBet('YES', r.label)}
                      className={`px-3 py-1 rounded text-xs font-semibold border transition-colors ${
                        yesOn ? 'bg-emerald-600 border-emerald-500 text-white'
                             : 'border-gray-700 text-gray-500 hover:border-emerald-700 hover:text-emerald-300'
                      }`}
                    >
                      {yesOn ? 'YES ✓' : 'YES'}
                    </button>
                  </td>
                  <td className="py-2 px-2 text-right font-mono text-xs text-gray-400">
                    {r.noAsk != null ? `${(r.noAsk * 100).toFixed(1)}¢` : '—'}
                  </td>
                  <td className={`py-2 px-2 text-right font-mono text-xs ${noS && noS.evPerDollar > 0 ? 'text-emerald-300' : noS && noS.evPerDollar < 0 ? 'text-red-300' : 'text-gray-500'}`}>
                    {noS ? `${(noS.evPerDollar * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-2 px-2 text-center">
                    <button
                      onClick={() => toggleBet('NO', r.label)}
                      className={`px-3 py-1 rounded text-xs font-semibold border transition-colors ${
                        noOn ? 'bg-red-600 border-red-500 text-white'
                             : 'border-gray-700 text-gray-500 hover:border-red-700 hover:text-red-300'
                      }`}
                    >
                      {noOn ? 'NO ✓' : 'NO'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Sizing controls + selected bets */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4 mt-4">
        {/* Selected bets — editable $ amounts */}
        <div className="rounded border border-gray-800 overflow-hidden">
          <div className="px-3 py-1.5 text-xs uppercase tracking-wider text-gray-500 border-b border-gray-800 bg-gray-900/40">
            Selected positions
          </div>
          {selectedKeys.length === 0 ? (
            <div className="p-4 text-sm text-gray-500">
              No bets selected. Click YES or NO in the table above to add a bet.
              The default $ allocation will be Kelly-sized using your probability estimates.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-gray-500">
                <tr>
                  <th className="text-left  py-1.5 px-2 font-normal">Bet</th>
                  <th className="text-right py-1.5 px-2 font-normal">Cost</th>
                  <th className="text-right py-1.5 px-2 font-normal">My win %</th>
                  <th className="text-right py-1.5 px-2 font-normal">EV / $</th>
                  <th className="text-right py-1.5 px-2 font-normal">Kelly</th>
                  <th className="text-right py-1.5 px-2 font-normal">Stake $</th>
                  <th className="text-right py-1.5 px-2 font-normal">Shares</th>
                </tr>
              </thead>
              <tbody>
                {selectedKeys.map((key) => {
                  const { side, label } = parseKey(key)
                  const s = betStats(side, label)
                  if (!s) return null
                  const amount = betAmounts[key] || 0
                  const shares = amount / s.cost
                  return (
                    <tr key={key} className="border-t border-gray-900">
                      <td className="py-2 px-2">
                        <span className={`text-xs font-semibold ${side === 'YES' ? 'text-emerald-300' : 'text-red-300'}`}>{side}</span>
                        <span className="ml-2 text-gray-200">{label}</span>
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-gray-400">{(s.cost * 100).toFixed(1)}¢</td>
                      <td className="py-2 px-2 text-right font-mono text-cyan-300">{(s.winProb * 100).toFixed(0)}%</td>
                      <td className={`py-2 px-2 text-right font-mono ${s.evPerDollar > 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                        {(s.evPerDollar * 100).toFixed(1)}%
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-gray-300">
                        {s.kelly > 0 ? `${(s.kelly * 100).toFixed(1)}%` : '—'}
                      </td>
                      <td className="py-2 px-2 text-right">
                        <input
                          type="number" min={0} step={0.5}
                          value={Math.round(amount * 100) / 100}
                          onChange={(e) => setAmount(key, Number(e.target.value) || 0)}
                          className="w-20 bg-gray-900 border border-gray-700 rounded px-1 py-0.5 text-right font-mono text-xs"
                        />
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-gray-400">{shares.toFixed(1)}</td>
                    </tr>
                  )
                })}
                <tr className="border-t-2 border-gray-700 bg-gray-900/40">
                  <td colSpan={5} className="py-2 px-2 text-right text-xs uppercase tracking-wider text-gray-500">Total stake</td>
                  <td className="py-2 px-2 text-right font-mono text-base text-gray-100">${totalStake.toFixed(2)}</td>
                  <td></td>
                </tr>
              </tbody>
            </table>
          )}
        </div>

        {/* Sizing & summary */}
        <div className="space-y-3">
          <div className="rounded border border-gray-800 bg-gray-950/60 p-3 space-y-2">
            <div className="text-xs uppercase tracking-wider text-gray-500">Kelly auto-sizer</div>
            <div className="flex items-baseline justify-between">
              <label className="text-sm text-gray-400">Bankroll</label>
              <div className="flex items-baseline gap-1">
                <span className="text-gray-500 text-sm">$</span>
                <input
                  type="number" min={0} step={10}
                  value={bankroll}
                  onChange={(e) => setBankroll(Math.max(0, Number(e.target.value) || 0))}
                  className="w-24 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-right font-mono text-base"
                />
              </div>
            </div>
            <div>
              <label className="text-sm text-gray-400 flex items-baseline justify-between">
                Kelly fraction <span className="text-cyan-300 font-mono">{kellyScale.toFixed(2)}×</span>
              </label>
              <input
                type="range" min={0.05} max={1} step={0.05}
                value={kellyScale}
                onChange={(e) => setKellyScale(Number(e.target.value))}
                className="w-full mt-1"
              />
              <div className="text-[10px] text-gray-500 mt-1">
                0.25× = quarter Kelly (recommended). 1× = full Kelly (high variance).
                Lower = safer, higher = more aggressive.
              </div>
            </div>
            <div className="text-[11px] text-gray-500 pt-1 border-t border-gray-800">
              Click <span className="text-emerald-300">re-auto size</span> above to apply Kelly sizing to all current bets.
              The auto-sized $ amount = (Kelly fraction) × (Kelly multiplier) × (Bankroll).
              Negative-edge bets get $0.
            </div>
          </div>

          <div className="rounded border border-gray-800 bg-gray-950/60 p-3 space-y-1.5">
            <div className="text-xs uppercase tracking-wider text-gray-500">Position summary</div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-400">Total stake</span>
              <span className="font-mono text-gray-100">${totalStake.toFixed(2)}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-400">Expected value (your probs)</span>
              <span className={`font-mono ${expectedValue >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                {expectedValue >= 0 ? '+' : ''}${expectedValue.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-400">Best case P&L</span>
              <span className={`font-mono ${maxPnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                {selectedKeys.length === 0 ? '—' : `${maxPnl >= 0 ? '+' : ''}$${maxPnl.toFixed(2)}`}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-400">Worst case P&L</span>
              <span className={`font-mono ${minPnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                {selectedKeys.length === 0 ? '—' : `${minPnl >= 0 ? '+' : ''}$${minPnl.toFixed(2)}`}
              </span>
            </div>
            {guaranteedProfit && (
              <div className="text-xs text-emerald-300 border border-emerald-700 rounded px-2 py-1">
                🟢 Guaranteed profit — every outcome wins
              </div>
            )}
            {guaranteedLoss && (
              <div className="text-xs text-red-300 border border-red-700 rounded px-2 py-1">
                🔴 Guaranteed loss — no outcome can win
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Per-outcome payoff table */}
      {selectedKeys.length > 0 && (
        <div className="mt-4 rounded border border-gray-800">
          <div className="px-3 py-1.5 text-xs uppercase tracking-wider text-gray-500 border-b border-gray-800 bg-gray-900/40">
            Per-outcome P&L (one row per possible winning bracket)
          </div>
          <table className="w-full text-sm">
            <thead className="text-gray-500">
              <tr className="border-b border-gray-800">
                <th className="text-left  py-1.5 px-3 font-normal">If winner is…</th>
                <th className="text-right py-1.5 px-3 font-normal">Your prob</th>
                <th className="text-right py-1.5 px-3 font-normal">Payout</th>
                <th className="text-right py-1.5 px-3 font-normal">P&L</th>
                <th className="text-right py-1.5 px-3 font-normal">EV contrib</th>
              </tr>
            </thead>
            <tbody>
              {outcomes.map((o) => {
                const isMin = o.pnl === minPnl
                const isMax = o.pnl === maxPnl && maxPnl !== minPnl
                return (
                  <tr key={o.label} className={`border-t border-gray-900 ${isMin ? 'bg-red-950/20' : isMax ? 'bg-emerald-950/20' : ''}`}>
                    <td className="py-1.5 px-3 text-gray-300">
                      {o.label}
                      {isMin && <span className="ml-2 text-xs text-red-400">worst</span>}
                      {isMax && <span className="ml-2 text-xs text-emerald-400">best</span>}
                    </td>
                    <td className="py-1.5 px-3 text-right font-mono text-cyan-300">{(o.prob * 100).toFixed(1)}%</td>
                    <td className="py-1.5 px-3 text-right font-mono text-gray-300">${o.payout.toFixed(2)}</td>
                    <td className={`py-1.5 px-3 text-right font-mono ${o.pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                      {o.pnl >= 0 ? '+' : ''}${o.pnl.toFixed(2)}
                    </td>
                    <td className={`py-1.5 px-3 text-right font-mono text-xs ${o.contribEV >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {o.contribEV >= 0 ? '+' : ''}${o.contribEV.toFixed(3)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
