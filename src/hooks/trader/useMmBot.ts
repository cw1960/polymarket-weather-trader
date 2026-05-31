// useMmBot — pulls live state of the 5-min BTC market-making bot from Supabase.
// Data is written by mm_bot_log_sync.py running every 30s on the VPS.

import { useCallback, useEffect, useState } from 'react'
import supabase from '../../lib/supabase'

export interface MmBotStatus {
  process_alive: boolean
  kill_switch_tripped: boolean
  last_heartbeat: string | null
  bot_started_at: string | null
  total_fills: number
  total_settlements: number
  realized_pnl_usd: number
  open_orders_count: number
  open_exposure_usd: number
  placements_today: number
  polymarket_portfolio_value: number | null
  starting_balance_usd: number | null
  rewards_today_usd: number | null
  rewards_7d_usd: number | null
  rewards_last_synced_at: string | null
  notes: string | null
}

export interface MmBotFill {
  id: number
  fill_time: string
  market_slug: string
  side: 'Up' | 'Down'
  price: number
  size: number
  cost_usd: number
  cumulative_up: number | null
  cumulative_down: number | null
  btc_binance: number | null
  btc_chainlink: number | null
}

export interface MmBotSettlement {
  id: number
  settlement_time: string
  market_slug: string
  outcome: 'Up' | 'Down' | null
  up_filled: number
  down_filled: number
  up_cost: number
  down_cost: number
  pnl_usd: number
  cumulative_pnl_usd: number | null
  notes: string | null
}

export interface MmBotData {
  status: MmBotStatus | null
  recentFills: MmBotFill[]
  recentSettlements: MmBotSettlement[]
  loading: boolean
  error: string | null
  refresh: () => void
}


export function useMmBot(): MmBotData {
  const [status, setStatus] = useState<MmBotStatus | null>(null)
  const [recentFills, setRecentFills] = useState<MmBotFill[]>([])
  const [recentSettlements, setRecentSettlements] = useState<MmBotSettlement[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch = useCallback(async () => {
    try {
      // 1) status row
      const { data: statusData, error: se } = await supabase
        .from('mm_bot_status')
        .select('*')
        .eq('id', 1)
        .maybeSingle()
      if (se) throw se
      if (statusData) {
        setStatus({
          process_alive: !!statusData.process_alive,
          kill_switch_tripped: !!statusData.kill_switch_tripped,
          last_heartbeat: statusData.last_heartbeat,
          bot_started_at: statusData.bot_started_at,
          total_fills: Number(statusData.total_fills || 0),
          total_settlements: Number(statusData.total_settlements || 0),
          realized_pnl_usd: Number(statusData.realized_pnl_usd || 0),
          open_orders_count: Number(statusData.open_orders_count || 0),
          open_exposure_usd: Number(statusData.open_exposure_usd || 0),
          placements_today: Number(statusData.placements_today || 0),
          polymarket_portfolio_value: statusData.polymarket_portfolio_value != null
            ? Number(statusData.polymarket_portfolio_value) : null,
          starting_balance_usd: statusData.starting_balance_usd != null
            ? Number(statusData.starting_balance_usd) : null,
          rewards_today_usd: statusData.rewards_today_usd != null
            ? Number(statusData.rewards_today_usd) : null,
          rewards_7d_usd: statusData.rewards_7d_usd != null
            ? Number(statusData.rewards_7d_usd) : null,
          rewards_last_synced_at: statusData.rewards_last_synced_at ?? null,
          notes: statusData.notes,
        })
      }

      // 2) Recent fills
      const { data: fillData, error: fe } = await supabase
        .from('mm_bot_fills')
        .select('*')
        .order('fill_time', { ascending: false })
        .limit(40)
      if (fe) throw fe
      setRecentFills((fillData ?? []) as MmBotFill[])

      // 3) Recent settlements
      const { data: settleData, error: setlErr } = await supabase
        .from('mm_bot_settlements')
        .select('*')
        .order('settlement_time', { ascending: false })
        .limit(40)
      if (setlErr) throw setlErr
      setRecentSettlements((settleData ?? []) as MmBotSettlement[])

      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, 2_000)   // 2s refresh (near-realtime)
    return () => clearInterval(id)
  }, [fetch])

  return { status, recentFills, recentSettlements, loading, error, refresh: fetch }
}
