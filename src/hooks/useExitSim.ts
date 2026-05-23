import { useState, useEffect } from 'react'
import supabase from '../lib/supabase'

export interface ExitSimRow {
  id:                       string
  city:                     string
  forecast_date:            string
  detection_type:           string    // 'bust' | 'late_decay'
  detected_at:              string
  bet_bracket:              string
  bet_lock_price:           number | null
  new_bracket:              string | null
  busted_yes_price:         number | null
  new_yes_price:            number | null
  actual_winning_bracket:   string | null
  bet_won:                  boolean | null
  new_won:                  boolean | null
  hold_pnl:                 number | null
  sell_only_pnl:            number | null
  switch_fresh_pnl:         number | null
  sell_switch_proceeds_pnl: number | null
  sell_switch_fresh_pnl:    number | null
}

export interface ExitSimSummary {
  rows:           ExitSimRow[]
  totalEvents:    number
  bustEvents:     number
  decayEvents:    number
  resolved:       number
  pending:        number
  // Aggregated P&L across all resolved sims
  totalHold:               number
  totalSellOnly:           number
  totalSwitchFresh:        number
  totalSellSwitchProceeds: number
  totalSellSwitchFresh:    number
}

export function useExitSim() {
  const [summary, setSummary] = useState<ExitSimSummary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let mounted = true
    async function fetchSim() {
      try {
        const { data } = await supabase
          .from('exit_simulation')
          .select('*')
          .order('detected_at', { ascending: false })
          .limit(500)

        if (!mounted) return
        const rows = (data ?? []) as ExitSimRow[]
        const resolved = rows.filter((r) => r.actual_winning_bracket != null)

        const sum = (key: keyof ExitSimRow) =>
          resolved.reduce((acc, r) => {
            const v = r[key]
            return acc + (typeof v === 'number' ? v : 0)
          }, 0)

        setSummary({
          rows,
          totalEvents:    rows.length,
          bustEvents:     rows.filter((r) => r.detection_type === 'bust').length,
          decayEvents:    rows.filter((r) => r.detection_type === 'late_decay').length,
          resolved:       resolved.length,
          pending:        rows.length - resolved.length,
          totalHold:               sum('hold_pnl'),
          totalSellOnly:           sum('sell_only_pnl'),
          totalSwitchFresh:        sum('switch_fresh_pnl'),
          totalSellSwitchProceeds: sum('sell_switch_proceeds_pnl'),
          totalSellSwitchFresh:    sum('sell_switch_fresh_pnl'),
        })
        setLoading(false)
      } catch (e) {
        console.error('useExitSim error', e)
        setLoading(false)
      }
    }
    fetchSim()
    const interval = setInterval(fetchSim, 60_000)
    return () => { mounted = false; clearInterval(interval) }
  }, [])

  return { summary, loading }
}
