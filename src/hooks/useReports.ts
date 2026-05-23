import { useState, useEffect, useCallback } from 'react'
import supabase from '../lib/supabase'
import { RunReport } from '../types'

export function useReports() {
  const [latestReport,  setLatestReport]  = useState<RunReport | null>(null)
  const [todayReports,  setTodayReports]  = useState<RunReport[]>([])
  const [recentReports, setRecentReports] = useState<RunReport[]>([])
  const [loading,       setLoading]       = useState(true)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  const fetch = useCallback(async () => {
    try {
      // Use LOCAL midnight as the cutoff so the timeline doesn't reset
      // at UTC midnight while the user is still mid-evening.
      const localMidnight = new Date()
      localMidnight.setHours(0, 0, 0, 0)
      const localMidnightISO = localMidnight.toISOString()

      const thirtyDaysAgo = new Date()
      thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30)
      const thirtyDaysAgoStr = thirtyDaysAgo.toISOString()

      const [latestRes, todayRes, recentRes] = await Promise.all([
        // Single most recent report (for go-live criteria + city health grid)
        supabase
          .from('run_reports')
          .select('*')
          .order('run_time', { ascending: false })
          .limit(1),

        // Today's runs — from local midnight so Monterrey evenings aren't cut off
        supabase
          .from('run_reports')
          .select('*')
          .gte('run_time', localMidnightISO)
          .order('run_time', { ascending: true }),

        // Last 30 days for the history log
        supabase
          .from('run_reports')
          .select(
            'id, run_time, run_slot, health_score, summary, criteria_met, signals_generated, orders_placed, win_rate_7d, roi_7d'
          )
          .gte('run_time', thirtyDaysAgoStr)
          .order('run_time', { ascending: false })
          .limit(200),
      ])

      setLatestReport((latestRes.data?.[0] as RunReport) ?? null)
      setTodayReports((todayRes.data ?? []) as RunReport[])
      setRecentReports((recentRes.data ?? []) as RunReport[])
      setLastRefreshed(new Date())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    // Refresh every 5 minutes — reports are written 4x/day, no need to hammer
    const interval = setInterval(fetch, 5 * 60_000)
    return () => clearInterval(interval)
  }, [fetch])

  return { latestReport, todayReports, recentReports, loading, lastRefreshed, refresh: fetch }
}
