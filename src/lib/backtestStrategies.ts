// Pluggable backtest strategy framework.
//
// A Strategy takes the full HistoricalEventDetail and returns a list of
// Trade events: (timestamp, side, bracket_label, entry_price, qty).
// The harness then settles each trade against the eventual resolution
// (winning bracket) and computes P&L.
//
// First strategy: "Follow the market favorite at hour H_enter, exit at
// H_exit." If the favorite at exit time is the same as at entry, hold;
// otherwise switch. Crude but a useful baseline.

import type { HistoricalEventDetail } from '../hooks/trader/useHistoricalData'

export interface BacktestTrade {
  ts_ms: number
  city: string
  forecast_date: string
  bracket_label: string
  side: 'YES' | 'NO'
  entry_price: number          // 0..1, what we paid per share
  qty: number                  // # shares (in the harness we assume $1 stake = 1/entry shares)
  notional_usd: number         // dollar stake
}

export interface BacktestSettlement extends BacktestTrade {
  payoff_per_share: number     // 1 if our side won this bracket, else 0
  pnl_usd: number              // (payoff − entry) × qty for YES, etc.
  won_event: boolean
}

export interface Strategy {
  key: string
  label: string
  description: string
  params: Record<string, number | string>
  run: (
    detail: HistoricalEventDetail,
    params: Record<string, number | string>,
    stakePerEventUsd: number,
  ) => BacktestTrade[]
}


// Snapshot of all brackets' YES prices at a given moment.
function snapshotAt(detail: HistoricalEventDetail, ms: number): Record<string, number> {
  const out: Record<string, number> = {}
  for (const b of detail.brackets) {
    // Find the last point at or before ms (linear scan is fine; bracket
    // series are at most ~3500 points).
    let lastP: number | null = null
    for (const p of b.points) {
      if (p.ms > ms) break
      lastP = p.yes_price
    }
    if (lastP != null) out[b.bracket_label] = lastP
  }
  return out
}


function pickFavorite(snapshot: Record<string, number>): { label: string; price: number } | null {
  let bestLabel: string | null = null
  let bestP = -1
  for (const [lbl, p] of Object.entries(snapshot)) {
    if (p > bestP) { bestP = p; bestLabel = lbl }
  }
  return bestLabel ? { label: bestLabel, price: bestP } : null
}


// "Buy favorite at hour H_enter" — entry only, holds to resolution.
// Useful baseline: does buying the market's predicted winner at noon
// beat random chance after spread + slippage?
export const STRATEGY_BUY_FAVORITE_AT_HOUR: Strategy = {
  key: 'buy_favorite_at_hour',
  label: 'Buy market favorite at a fixed hour (UTC)',
  description: 'At hour H UTC on the resolution date, buy 1 unit of the bracket with the highest YES price. Hold to settlement. Tests whether the market\'s implied winner has positive EV at that point in the day.',
  params: { hour_utc: 18, min_price: 0.30, max_price: 0.85 },
  run(detail, params, stakeUsd) {
    if (!detail.event) return []
    const targetMs = new Date(detail.event.forecast_date + 'T00:00:00Z').getTime()
                   + (Number(params.hour_utc) || 18) * 3600_000
    const snap = snapshotAt(detail, targetMs)
    const fav = pickFavorite(snap)
    if (!fav) return []
    if (fav.price < Number(params.min_price) || fav.price > Number(params.max_price)) return []
    return [{
      ts_ms: targetMs,
      city: detail.event.city,
      forecast_date: detail.event.forecast_date,
      bracket_label: fav.label,
      side: 'YES',
      entry_price: fav.price,
      qty: stakeUsd / fav.price,
      notional_usd: stakeUsd,
    }]
  },
}


// "Buy whichever bracket the day's running-max-temp suggests at hour H."
// Look at the WU obs that came in BEFORE hour H, then pick the bracket
// whose range contains that running max. Crude weather-aware baseline.
export const STRATEGY_RUNNING_MAX_BRACKET: Strategy = {
  key: 'running_max_bracket',
  label: 'Buy bracket containing running-max temp at hour H',
  description: 'At hour H UTC, look up the highest observed temperature so far that day. Buy YES on the bracket whose range contains that temp. Tests whether temperature itself beats market.',
  params: { hour_utc: 17, max_price: 0.85 },
  run(detail, params, stakeUsd) {
    if (!detail.event) return []
    const targetMs = new Date(detail.event.forecast_date + 'T00:00:00Z').getTime()
                   + (Number(params.hour_utc) || 17) * 3600_000
    // Running max temp from observations before targetMs (use °F to match
    // bracket bounds for US cities; for non-US cities the bracket_unit
    // tells us which scale we're in).
    const unit = detail.brackets[0]?.bracket_unit ?? 'F'
    let runningMax = -1e9
    let any = false
    for (const o of detail.observations) {
      if (o.ms > targetMs) break
      const t = unit === 'F' ? o.temp_f : o.temp_c
      if (t > runningMax) { runningMax = t; any = true }
    }
    if (!any) return []
    // Find bracket whose [low, high] contains runningMax. Use the
    // half-degree windowing the collector wrote (low_native already in
    // that form).
    const matched = detail.brackets.find((b) => {
      const lo = b.bracket_low_native
      const hi = b.bracket_high_native
      const loOk = lo == null || runningMax >= lo
      const hiOk = hi == null || runningMax < hi
      return loOk && hiOk
    })
    if (!matched) return []
    // Get the bracket's YES price at targetMs
    let entry: number | null = null
    for (const p of matched.points) {
      if (p.ms > targetMs) break
      entry = p.yes_price
    }
    if (entry == null) return []
    if (entry > Number(params.max_price)) return []
    return [{
      ts_ms: targetMs,
      city: detail.event.city,
      forecast_date: detail.event.forecast_date,
      bracket_label: matched.bracket_label,
      side: 'YES',
      entry_price: entry,
      qty: stakeUsd / entry,
      notional_usd: stakeUsd,
    }]
  },
}


// "Buy NO on every tail bracket whose YES price is below threshold."
//
// This is the variance-risk-premium thesis applied to weather markets.
// In equity options, deep out-of-the-money options have been systematically
// overpriced for decades because buyers pay a premium for tail insurance.
// Sellers earn ~3-5%/year. The behavioral analog in weather markets:
// retail buys "lottery ticket" extreme-temperature brackets, paying more
// than the brackets are statistically worth.
//
// Trade: at hour H UTC, find all brackets with yes_price < threshold (the
// "tails"), buy NO on each. NO wins if temp does NOT land in that bracket
// — i.e. wins MOST of the time when the bracket truly is a tail.
//
// Costs and edge:
//   • NO entry price = 1 - yes_price. For yes_price = 0.05 → pay 0.95
//   • Win pays $1 → net +$0.05 per share
//   • Lose pays $0 → net -$0.95 per share
//   • Under market-implied probabilities, EV = 0 (no edge)
//   • Under "tails are overpriced" hypothesis, win rate > (1 - yes_price)
//     → positive EV. That's what this test checks.
//
// Stake per bracket — each tail bracket gets the same fixed dollar amount
// (default $1). This means events with more tails get more total notional
// risked, which is acceptable for a "scan the market for cheap tails"
// strategy.
export const STRATEGY_TAIL_NO_BELOW_THRESHOLD: Strategy = {
  key: 'tail_no_below_threshold',
  label: 'Sell tails: buy NO on cheap brackets (variance risk premium)',
  description: 'At hour H UTC, buy NO on every bracket whose YES price is below the threshold. Tests whether tail brackets are systematically overpriced — the variance-risk-premium thesis from equity options. Positive ROI net of ~3% spread = the edge is real.',
  params: { hour_utc: 14, max_yes_price: 0.08, stake_per_bracket: 1 },
  run(detail, params, stakePerEventIgnored) {
    void stakePerEventIgnored          // this strategy uses per-bracket stake
    if (!detail.event) return []
    const targetMs = new Date(detail.event.forecast_date + 'T00:00:00Z').getTime()
                   + (Number(params.hour_utc) || 14) * 3600_000
    const snap = snapshotAt(detail, targetMs)
    const maxYes = Number(params.max_yes_price) || 0.08
    const stakePer = Number(params.stake_per_bracket) || 1
    const trades: BacktestTrade[] = []
    for (const [label, yesPrice] of Object.entries(snap)) {
      if (yesPrice >= maxYes) continue
      // Pay (1 - yes_price) per share to buy NO. Skip if price is degenerate.
      const noPrice = 1 - yesPrice
      if (noPrice <= 0 || noPrice >= 1) continue
      trades.push({
        ts_ms: targetMs,
        city: detail.event.city,
        forecast_date: detail.event.forecast_date,
        bracket_label: label,
        side: 'NO',
        entry_price: noPrice,
        qty: stakePer / noPrice,
        notional_usd: stakePer,
      })
    }
    return trades
  },
}


export const STRATEGIES: Strategy[] = [
  STRATEGY_BUY_FAVORITE_AT_HOUR,
  STRATEGY_RUNNING_MAX_BRACKET,
  STRATEGY_TAIL_NO_BELOW_THRESHOLD,
]


// Settle a trade against the event's resolution. For YES bets, payoff = 1
// if our bracket == winning_bracket, else 0. For NO bets, the reverse.
export function settleTrade(
  trade: BacktestTrade,
  winningBracket: string | null,
): BacktestSettlement {
  const wonBracket = winningBracket != null && trade.bracket_label === winningBracket
  const payoff = trade.side === 'YES' ? (wonBracket ? 1 : 0) : (wonBracket ? 0 : 1)
  const pnl = (payoff - trade.entry_price) * trade.qty
  return {
    ...trade,
    payoff_per_share: payoff,
    pnl_usd: pnl,
    won_event: wonBracket,
  }
}
