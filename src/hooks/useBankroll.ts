import { useState, useEffect, useCallback } from 'react'
import supabase from '../lib/supabase'
import { BankrollSnapshot } from '../types'

export function useBankroll() {
  const [currentBankroll, setCurrentBankroll] = useState<BankrollSnapshot | null>(null)
  const [snapshots,       setSnapshots]       = useState<BankrollSnapshot[]>([])
  // liveBalance: the actual bankroll_usd from system_config (updated by reconciler).
  // null means the row hasn't been written yet; fall back to $1,000 + computed P&L.
  const [liveBalance,            setLiveBalance]            = useState<number | null>(null)
  // The reference value used for "% gain since going live". Written once when the
  // user transitioned to live trading and never moved by P&L (deposits/withdrawals
  // are handled separately if needed). null means we're still in paper mode.
  const [liveStartingBankroll,   setLiveStartingBankroll]   = useState<number | null>(null)
  const [liveStartDate,          setLiveStartDate]          = useState<string | null>(null)
  const [loading,                setLoading]                = useState(true)
  const [lastRefreshed,          setLastRefreshed]          = useState<Date | null>(null)

  const fetch = useCallback(async () => {
    try {
      const ninetyDaysAgo = new Date()
      ninetyDaysAgo.setDate(ninetyDaysAgo.getDate() - 90)

      const [latestRes, historyRes, configRes, startBankrollRes, startDateRes] = await Promise.all([
        supabase
          .from('bankroll_snapshots')
          .select('*')
          .order('snapshot_date', { ascending: false })
          .limit(1),
        supabase
          .from('bankroll_snapshots')
          .select('*')
          .gte('snapshot_date', ninetyDaysAgo.toISOString().split('T')[0])
          .order('snapshot_date', { ascending: true }),
        // Read the authoritative bankroll from system_config (set by reconciler)
        supabase
          .from('system_config')
          .select('value')
          .eq('key', 'bankroll_usd')
          .maybeSingle(),
        supabase
          .from('system_config')
          .select('value')
          .eq('key', 'live_starting_bankroll')
          .maybeSingle(),
        supabase
          .from('system_config')
          .select('value')
          .eq('key', 'live_start_date')
          .maybeSingle(),
      ])

      setCurrentBankroll(latestRes.data?.[0] ?? null)
      setSnapshots(historyRes.data ?? [])

      const raw = configRes.data?.value
      setLiveBalance(raw != null ? parseFloat(raw) : null)

      const startRaw = startBankrollRes.data?.value
      setLiveStartingBankroll(startRaw != null ? parseFloat(startRaw) : null)

      setLiveStartDate(startDateRes.data?.value ?? null)

      setLastRefreshed(new Date())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const interval = setInterval(fetch, 60_000)
    return () => clearInterval(interval)
  }, [fetch])

  return {
    currentBankroll,
    snapshots,
    liveBalance,
    liveStartingBankroll,
    liveStartDate,
    loading,
    lastRefreshed,
  }
}
