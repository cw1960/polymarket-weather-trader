import { useState, useEffect, useRef } from 'react'
import supabase from '../lib/supabase'
import { Ladder } from '../types'

export function useLadders() {
  const [ladders, setLadders] = useState<Ladder[]>([])
  const [loading, setLoading] = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const channelRef = useRef<ReturnType<typeof supabase.channel> | null>(null)

  async function fetch() {
    const today = new Date().toISOString().slice(0, 10)
    const sevenDaysAgo = new Date()
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7)
    const cutoff = sevenDaysAgo.toISOString().slice(0, 10)

    // Fetch open ladders (forecast >= today) + recently closed (last 7 days)
    // as two separate queries so open ladders are never mixed with old closed noise
    const [openRes, closedRes] = await Promise.all([
      supabase
        .from('ladders')
        .select('*')
        .eq('status', 'open')
        .gte('forecast_date', today)
        .order('forecast_date', { ascending: true })
        .order('city', { ascending: true }),
      supabase
        .from('ladders')
        .select('*')
        .neq('status', 'open')
        .neq('status', 'void')
        .gte('forecast_date', cutoff)
        .order('forecast_date', { ascending: false })
        .order('city', { ascending: true })
        .limit(30),  // cap recently-closed at 30 rows — enough to review, not enough to flood
    ])

    const combined = [
      ...((openRes.data as Ladder[]) ?? []),
      ...((closedRes.data as Ladder[]) ?? []),
    ]
    setLadders(combined)
    setLoading(false)
    setLastRefreshed(new Date())
  }

  useEffect(() => {
    fetch()

    const channel = supabase
      .channel('ladders-changes')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'ladders' }, () => fetch())
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'ladders' }, () => fetch())
      .subscribe()

    channelRef.current = channel
    const poll = setInterval(fetch, 60_000)

    return () => {
      clearInterval(poll)
      if (channelRef.current) supabase.removeChannel(channelRef.current)
    }
  }, [])

  return { ladders, loading, lastRefreshed, refresh: fetch }
}
