// MmBotDashboard — live view of the 5-min BTC market-making bot.
//
// Source of truth is Supabase tables populated by mm_bot_log_sync.py
// (cron every 30s on the VPS). UI refreshes every 10s.

import { useState } from 'react'
import { useMmBot } from '../../hooks/trader/useMmBot'

// Kill switch: dashboard sets mm_bot_status.kill_switch_manual=true, the sync
// daemon on the VPS detects it on its next 3s cycle and sends SIGTERM to the
// trading bot. Bot's signal handler does the graceful shutdown.
async function requestKill(): Promise<{ ok: boolean; error?: string }> {
  const url = import.meta.env.VITE_SUPABASE_URL as string
  const key = import.meta.env.VITE_SUPABASE_ANON_KEY as string
  if (!url || !key) return { ok: false, error: 'Supabase config missing in env' }
  try {
    const r = await fetch(`${url}/rest/v1/mm_bot_status?id=eq.1`, {
      method: 'PATCH',
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
        Prefer: 'return=minimal',
      },
      body: JSON.stringify({ kill_switch_manual: true }),
    })
    if (!r.ok) return { ok: false, error: `HTTP ${r.status}: ${await r.text()}` }
    return { ok: true }
  } catch (e: any) {
    return { ok: false, error: String(e?.message || e) }
  }
}


function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (ms < 60_000) return `${Math.floor(ms/1000)}s ago`
  if (ms < 3600_000) return `${Math.floor(ms/60_000)}m ago`
  if (ms < 86400_000) return `${Math.floor(ms/3600_000)}h ago`
  return `${Math.floor(ms/86400_000)}d ago`
}


export default function MmBotDashboard() {
  const { status, recentFills, recentSettlements, loading, error } = useMmBot()

  // Kill button state
  const [killState, setKillState] = useState<'idle'|'sending'|'sent'|'failed'>('idle')
  const [killMsg, setKillMsg] = useState<string>('')
  async function handleKillClick() {
    if (!window.confirm(
      'STOP THE BOT NOW?\n\nThis sends SIGTERM to the trading process within 3-5s.\n' +
      'Any open orders are cancelled. Already-filled positions stay on Polymarket ' +
      'and will resolve normally.'
    )) return
    setKillState('sending'); setKillMsg('')
    const r = await requestKill()
    if (r.ok) {
      setKillState('sent')
      setKillMsg('Kill signal sent. Sync daemon will detect within 3s and shut down the bot.')
    } else {
      setKillState('failed')
      setKillMsg(r.error || 'unknown error')
    }
  }

  const heartbeatStale = status?.last_heartbeat
    ? (Date.now() - new Date(status.last_heartbeat).getTime()) > 120_000   // 2 min
    : true

  const isHealthy = status?.process_alive && !status?.kill_switch_tripped && !heartbeatStale
  // pnl here is now the TRUE LIFETIME P&L (total realized + unrealized), published
  // by the sync as (real_cash + position_value) - net_deposit. It is NOT a
  // log-derived estimate anymore. See mm_bot_log_sync.get_cash_balance.
  const pnl = status?.realized_pnl_usd ?? 0
  const pnlClr = pnl > 0.005 ? 'text-emerald-300' : pnl < -0.005 ? 'text-red-300' : 'text-gray-300'

  // Account math — GROUND TRUTH (2026-05-31 rewrite). The sync reads real USDC
  // cash from the wallet, so we no longer DERIVE the balance from a log P&L
  // estimate (which had been overstating the account by ~$259, masking real
  // losses). Because `pnl` is already the full lifetime P&L (it includes the
  // live position value via real-cash + positions − deposit), Portfolio is
  // simply starting_balance + pnl — NO separate unrealized term (adding one
  // double-counts the open position value).
  //   Portfolio = net_deposit + lifetime_pnl   (= real cash + position value)
  //   Cash      = Portfolio − position_value   (= real USDC cash)
  const positionValue = status?.polymarket_portfolio_value ?? 0
  const startingBalance = status?.starting_balance_usd ?? null
  const portfolio = startingBalance != null ? startingBalance + pnl : null
  const cash = portfolio != null ? portfolio - positionValue : null

  // Group settlements by date for daily P&L
  const byDay: Record<string, { pnl: number; n: number; wins: number; losses: number }> = {}
  for (const s of recentSettlements) {
    const day = s.settlement_time.slice(0, 10)
    if (!byDay[day]) byDay[day] = { pnl: 0, n: 0, wins: 0, losses: 0 }
    byDay[day].pnl += s.pnl_usd
    byDay[day].n += 1
    if (s.pnl_usd > 0.005) byDay[day].wins++
    else if (s.pnl_usd < -0.005) byDay[day].losses++
  }
  const dayKeys = Object.keys(byDay).sort().reverse()

  // Bot is considered "live" if process_alive AND not killed AND heartbeat fresh
  const botLive = !!(status?.process_alive && !status?.kill_switch_tripped && !heartbeatStale)

  return (
    <div className="p-6 space-y-4 text-white">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-xl font-semibold text-gray-100">🤖 5-min BTC Market-Maker</div>
          <div className="text-xs text-gray-500">
            Read-only view of the live bot. Updates every 10s. Bot writes via cron sync.
          </div>
        </div>
        {/* Emergency kill button — always visible at the top */}
        <div className="flex flex-col items-end gap-1">
          <button
            onClick={handleKillClick}
            disabled={!botLive || killState === 'sending' || killState === 'sent'}
            className={
              'px-5 py-3 rounded-lg font-bold text-base shadow-lg transition ' +
              (botLive && killState === 'idle'
                ? 'bg-red-700 hover:bg-red-600 text-white border-2 border-red-500 cursor-pointer'
                : killState === 'sending'
                ? 'bg-amber-700 text-white border-2 border-amber-500'
                : killState === 'sent'
                ? 'bg-gray-700 text-gray-300 border-2 border-gray-600'
                : 'bg-gray-800 text-gray-500 border-2 border-gray-700 cursor-not-allowed')
            }
            title={botLive ? 'Send SIGTERM to bot now' : 'Bot is not running'}
          >
            {killState === 'sending' ? '⏳ SENDING…'
              : killState === 'sent'  ? '✓ KILL SENT'
              : killState === 'failed' ? '⚠ RETRY KILL'
              : '🛑 STOP BOT'}
          </button>
          {killMsg && (
            <div className={
              'text-xs max-w-xs text-right ' +
              (killState === 'failed' ? 'text-red-400' : 'text-gray-400')
            }>
              {killMsg}
            </div>
          )}
        </div>
      </div>

      {error && <div className="text-sm text-red-400 border border-red-900 bg-red-950/30 rounded p-2">{error}</div>}

      {/* Rewards earned strip */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <StatusCard
          label="Rewards today"
          value={status?.rewards_today_usd != null ? `$${status.rewards_today_usd.toFixed(2)}` : '—'}
          color="text-purple-300"
          sub={status?.rewards_last_synced_at
            ? `last synced ${timeAgo(status.rewards_last_synced_at)}`
            : 'not synced yet'}
          big
        />
        <StatusCard
          label="Rewards 7d"
          value={status?.rewards_7d_usd != null ? `$${status.rewards_7d_usd.toFixed(2)}` : '—'}
          color="text-purple-300"
          sub="Polymarket distributes daily at 00:00 UTC"
          big
        />
        <StatusCard
          label="Lifetime P&L"
          value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`}
          color={pnlClr}
          sub={startingBalance != null ? `vs $${startingBalance.toFixed(0)} deposited (incl. rewards & losses)` : 'net of deposits'}
          big
        />
      </div>

      {/* Account balance strip — matches Polymarket UI labels */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <StatusCard
          label="Portfolio"
          value={portfolio != null ? `$${portfolio.toFixed(2)}` : '—'}
          color="text-emerald-300"
          sub={startingBalance == null ? 'starting balance not set' : `start $${startingBalance.toFixed(2)} + P&L ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`}
          big
        />
        <StatusCard
          label="Cash"
          value={cash != null ? `$${cash.toFixed(2)}` : '—'}
          color="text-cyan-300"
          sub="real USDC balance (live from wallet)"
          big
        />
        <StatusCard
          label="Position value"
          value={`$${positionValue.toFixed(2)}`}
          color="text-amber-300"
          sub="live from polymarket /value"
          big
        />
      </div>

      {/* Status strip */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatusCard
          label="Bot status"
          value={status?.kill_switch_tripped ? 'KILLED'
                : !status?.process_alive ? 'STOPPED'
                : heartbeatStale ? 'STALE'
                : 'RUNNING'}
          color={status?.kill_switch_tripped ? 'text-red-300'
                : isHealthy ? 'text-emerald-300'
                : 'text-amber-300'}
          sub={`heartbeat ${timeAgo(status?.last_heartbeat ?? null)}`}
          big
        />
        <StatusCard
          label="Lifetime P&L"
          value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`}
          color={pnlClr}
          sub={`${status?.total_settlements ?? 0} settled · net of $${startingBalance != null ? startingBalance.toFixed(0) : '—'} in`}
          big
        />
        <StatusCard
          label="Total fills"
          value={`${status?.total_fills ?? 0}`}
          color="text-cyan-300"
          sub={`since ${status?.bot_started_at ? new Date(status.bot_started_at).toLocaleString() : '—'}`}
        />
        <StatusCard
          label="Placements today"
          value={`${status?.placements_today ?? 0}`}
          color="text-gray-300"
          sub="vs 2000 daily cap"
        />
        <StatusCard
          label="Markets settled"
          value={`${status?.total_settlements ?? 0}`}
          color="text-gray-300"
          sub={status?.kill_switch_tripped ? '🚨 KILL SWITCH' : 'limits ok'}
        />
      </div>

      {/* Daily P&L breakdown */}
      <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div className="text-base font-semibold text-gray-100 mb-2">📅 Daily P&L</div>
        {dayKeys.length === 0 ? (
          <div className="text-sm text-gray-500 p-2">No settlements yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-gray-500 text-xs border-b border-gray-800">
              <tr>
                <th className="text-left  py-1.5 px-2 font-normal">Date</th>
                <th className="text-right py-1.5 px-2 font-normal">P&L</th>
                <th className="text-right py-1.5 px-2 font-normal">Markets</th>
                <th className="text-right py-1.5 px-2 font-normal">Wins</th>
                <th className="text-right py-1.5 px-2 font-normal">Losses</th>
              </tr>
            </thead>
            <tbody>
              {dayKeys.map((d) => {
                const x = byDay[d]
                const c = x.pnl >= 0 ? 'text-emerald-300' : 'text-red-300'
                return (
                  <tr key={d} className="border-t border-gray-900">
                    <td className="py-1.5 px-2 text-gray-200">{d}</td>
                    <td className={`py-1.5 px-2 text-right font-mono ${c}`}>
                      {x.pnl >= 0 ? '+' : ''}${x.pnl.toFixed(2)}
                    </td>
                    <td className="py-1.5 px-2 text-right text-gray-300">{x.n}</td>
                    <td className="py-1.5 px-2 text-right text-emerald-300">{x.wins}</td>
                    <td className="py-1.5 px-2 text-right text-red-300">{x.losses}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Detailed session report — matches the CLI mm_report.py format */}
      <SettlementReport
        settlements={recentSettlements}
        botStartedAt={status?.bot_started_at}
      />

      {/* Recent settlements (compact view) */}
      <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div className="text-base font-semibold text-gray-100 mb-2">📜 Recent market settlements (last {recentSettlements.length})</div>
        {recentSettlements.length === 0 ? (
          <div className="text-sm text-gray-500 p-2">{loading ? 'Loading…' : 'No settlements yet.'}</div>
        ) : (
          <div className="max-h-[320px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="text-gray-500 text-xs border-b border-gray-800 sticky top-0 bg-gray-950">
                <tr>
                  <th className="text-left  py-1.5 px-2 font-normal">Time</th>
                  <th className="text-left  py-1.5 px-2 font-normal">Market</th>
                  <th className="text-right py-1.5 px-2 font-normal">Outcome</th>
                  <th className="text-right py-1.5 px-2 font-normal">Up filled</th>
                  <th className="text-right py-1.5 px-2 font-normal">Down filled</th>
                  <th className="text-right py-1.5 px-2 font-normal">P&L</th>
                  <th className="text-left  py-1.5 px-2 font-normal">Notes</th>
                </tr>
              </thead>
              <tbody>
                {recentSettlements.map((s) => {
                  const c = s.pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'
                  return (
                    <tr key={s.id} className="border-t border-gray-900">
                      <td className="py-1.5 px-2 text-xs text-gray-500">{new Date(s.settlement_time).toLocaleTimeString()}</td>
                      <td className="py-1.5 px-2 text-xs text-gray-400">{s.market_slug.slice(-10)}</td>
                      <td className="py-1.5 px-2 text-right">
                        {s.outcome ? (
                          <span className={`px-1.5 py-0.5 rounded text-[10px] ${s.outcome === 'Up' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`}>{s.outcome}</span>
                        ) : '—'}
                      </td>
                      <td className="py-1.5 px-2 text-right font-mono text-gray-300">{s.up_filled.toFixed(0)}</td>
                      <td className="py-1.5 px-2 text-right font-mono text-gray-300">{s.down_filled.toFixed(0)}</td>
                      <td className={`py-1.5 px-2 text-right font-mono ${c}`}>{s.pnl_usd >= 0 ? '+' : ''}${s.pnl_usd.toFixed(2)}</td>
                      <td className="py-1.5 px-2 text-[10px] text-gray-500">{s.notes}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent fills */}
      <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div className="text-base font-semibold text-gray-100 mb-2">💸 Live trades — running position (last {recentFills.length})</div>
        {recentFills.length === 0 ? (
          <div className="text-sm text-gray-500 p-2">{loading ? 'Loading…' : 'No fills yet.'}</div>
        ) : (
          <div className="max-h-[320px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="text-gray-500 text-xs border-b border-gray-800 sticky top-0 bg-gray-950">
                <tr>
                  <th className="text-left  py-1.5 px-2 font-normal">Time</th>
                  <th className="text-left  py-1.5 px-2 font-normal">Market</th>
                  <th className="text-center py-1.5 px-2 font-normal">Side</th>
                  <th className="text-right py-1.5 px-2 font-normal">Size</th>
                  <th className="text-right py-1.5 px-2 font-normal">Price</th>
                  <th className="text-right py-1.5 px-2 font-normal">→ Cum Up</th>
                  <th className="text-right py-1.5 px-2 font-normal">→ Cum Dn</th>
                  <th className="text-right py-1.5 px-2 font-normal">Cost</th>
                  <th className="text-right py-1.5 px-2 font-normal">BTC (binance)</th>
                </tr>
              </thead>
              <tbody>
                {recentFills.map((f) => (
                  <tr key={f.id} className="border-t border-gray-900">
                    <td className="py-1.5 px-2 text-xs text-gray-500">{new Date(f.fill_time).toLocaleTimeString()}</td>
                    <td className="py-1.5 px-2 text-xs text-gray-400">{f.market_slug.slice(-10)}</td>
                    <td className="py-1.5 px-2 text-center">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] ${f.side === 'Up' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`}>{f.side}</span>
                    </td>
                    <td className="py-1.5 px-2 text-right font-mono text-gray-300">{f.size}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-cyan-300">{(f.price * 100).toFixed(1)}¢</td>
                    <td className="py-1.5 px-2 text-right font-mono text-emerald-300">{f.cumulative_up != null ? f.cumulative_up.toFixed(1) : '—'}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-red-300">{f.cumulative_down != null ? f.cumulative_down.toFixed(1) : '—'}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-gray-300">${f.cost_usd.toFixed(2)}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-gray-500">{f.btc_binance ? `$${f.btc_binance.toFixed(0)}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}


function SettlementReport({ settlements, botStartedAt }: {
  settlements: import('../../hooks/trader/useMmBot').MmBotSettlement[]
  botStartedAt: string | null | undefined
}) {
  // Filter to settlements since bot start
  const startMs = botStartedAt ? new Date(botStartedAt).getTime() : 0
  const filtered = settlements.filter(s => new Date(s.settlement_time).getTime() >= startMs)

  // Dedupe by slug (keep latest)
  const bySlug = new Map<string, typeof filtered[0]>()
  for (const s of filtered) {
    const prev = bySlug.get(s.market_slug)
    if (!prev || new Date(s.settlement_time) > new Date(prev.settlement_time)) {
      bySlug.set(s.market_slug, s)
    }
  }
  const deduped = Array.from(bySlug.values())
    .sort((a, b) => new Date(a.settlement_time).getTime() - new Date(b.settlement_time).getTime())

  // Compute cumulative P&L (oldest to newest)
  let cum = 0
  const withCum = deduped.map(s => {
    cum += s.pnl_usd
    return { ...s, cumPnl: cum }
  })

  // Display newest first
  const display = [...withCum].reverse()
  const sessionPnl = withCum.length > 0 ? withCum[withCum.length - 1].cumPnl : 0

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <div className="text-lg font-semibold text-gray-100">📊 Per-market settlement report</div>
          <div className="text-xs text-gray-400 mt-1">
            Most recent at top · auto-refreshes every 2 seconds
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-gray-500">Session P&L · {deduped.length} markets</div>
          <div className={`text-2xl font-mono font-bold ${sessionPnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
            {sessionPnl >= 0 ? '+' : ''}${sessionPnl.toFixed(2)}
          </div>
        </div>
      </div>

      {display.length === 0 ? (
        <div className="text-sm text-gray-500 p-4 text-center">
          No settlements yet since bot start. Markets settle every 5 minutes — first one should appear shortly.
        </div>
      ) : (
        <div className="max-h-[600px] overflow-y-auto pr-2 space-y-2 font-mono text-sm">
          {display.map((s) => {
            const u_avg = s.up_filled > 0 ? s.up_cost / s.up_filled : 0
            const d_avg = s.down_filled > 0 ? s.down_cost / s.down_filled : 0
            const c = s.pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'
            const cumC = s.cumPnl >= 0 ? 'text-emerald-300' : 'text-red-300'
            const bg = s.pnl_usd >= 0 ? 'border-emerald-700 bg-emerald-950/20' : 'border-red-700 bg-red-950/20'
            const t = new Date(s.settlement_time).toLocaleTimeString('en-US', {hour12: false})
            // Right-align share counts and prices for monospace alignment
            const pad = (s: string, n: number) => s.padStart(n, ' ')
            const upLine = `Up:   ${pad(s.up_filled.toFixed(1), 6)} sh @ $${u_avg.toFixed(3)}  (cost $${s.up_cost.toFixed(2)})`
            const dnLine = `Down: ${pad(s.down_filled.toFixed(1), 6)} sh @ $${d_avg.toFixed(3)}  (cost $${s.down_cost.toFixed(2)})`
            const pnlStr = `${s.pnl_usd >= 0 ? '+' : ''}$${s.pnl_usd.toFixed(2)}`
            const cumStr = `${s.cumPnl >= 0 ? '+' : ''}$${s.cumPnl.toFixed(2)}`
            return (
              <div key={s.id} className={`rounded-lg border ${bg} p-3`}>
                <div className="text-sm text-gray-200 mb-1.5">
                  <span className="text-cyan-300">{t}</span>
                  <span className="text-gray-500"> │ </span>
                  <span className="text-gray-400">{s.market_slug.slice(-10)}</span>
                  <span className="text-gray-500"> │ </span>
                  <span className={s.outcome === 'Up' ? 'text-emerald-300 font-bold' : 'text-red-300 font-bold'}>
                    outcome={s.outcome || '?'}
                  </span>
                </div>
                <pre className="text-sm text-gray-200 whitespace-pre leading-snug mb-1.5 pl-4">{upLine}{'\n'}{dnLine}</pre>
                <div className="text-sm pl-4">
                  <span className="text-gray-400">P&L: </span>
                  <span className={`${c} font-bold`}>{pnlStr}</span>
                  <span className="text-gray-500">  cum: </span>
                  <span className={`${cumC} font-bold`}>{cumStr}</span>
                </div>
                {s.notes && (
                  <div className="text-xs text-gray-400 mt-1 pl-4">{s.notes}</div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}


function StatusCard({ label, value, sub, color, big }: { label: string; value: string; sub?: string; color: string; big?: boolean }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/60 p-3">
      <div className="text-xs uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`${big ? 'text-2xl' : 'text-xl'} font-mono ${color}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500">{sub}</div>}
    </div>
  )
}
