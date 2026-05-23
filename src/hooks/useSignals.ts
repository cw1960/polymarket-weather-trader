import { useState, useEffect, useCallback, useRef } from 'react'
import supabase from '../lib/supabase'
import { TradeSignal } from '../types'

export function useSignals() {
  const [signals, setSignals] = useState<TradeSignal[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const channelRef = useRef<ReturnType<typeof supabase.channel> | null>(null)

  const fetch = useCallback(async () => {
    try {
      const today = new Date().toISOString().split('T')[0]
      const { data, error: err } = await supabase
        .from('trade_signals')
        .select('*')
        .gte('signal_time', today)
        .order('edge', { ascending: false })

      if (err) throw err
      setSignals(data ?? [])
      setError(null)
      setLastRefreshed(new Date())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()

    // Poll every 60s as fallback
    const interval = setInterval(fetch, 60_000)

    // Real-time: insert new signals immediately as they arrive
    channelRef.current = supabase
      .channel('trade_signals_inserts')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'trade_signals' },
        (payload) => {
          const incoming = payload.new as TradeSignal
          setSignals((prev) => {
            // Deduplicate by id, then re-sort by edge descending
            const merged = [incoming, ...prev.filter((s) => s.id !== incoming.id)]
            return merged.sort((a, b) => b.edge - a.edge)
          })
          setLastRefreshed(new Date())
        }
      )
      .subscribe()

    return () => {
      clearInterval(interval)
      if (channelRef.current) supabase.removeChannel(channelRef.current)
    }
  }, [fetch])

  return { signals, loading, error, refresh: fetch, lastRefreshed }
}
