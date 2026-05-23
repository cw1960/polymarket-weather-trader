// Fallback only — used when the live_starting_bankroll row hasn't been written
// yet (i.e. system is still in pre-live paper-trading mode).  In live mode the
// percentage is computed against the user's actual live_starting_bankroll.
const PAPER_FALLBACK_STARTING_BANKROLL = 1000

interface Props {
  totalPnl:      number
  todayPnl:      number
  openPositions: number
  winRate:       number
  resolvedCount: number
  // When non-null, use the authoritative DB value (system_config.bankroll_usd)
  // instead of the computed estimate.
  liveBalance?:  number | null
  // The bankroll at the moment the user went live (system_config.live_starting_bankroll).
  // % return is computed against this, not the paper fallback.
  liveStartingBankroll?: number | null
}

function MetricCard({
  label,
  value,
  sub,
  valueColor,
}: {
  label: string
  value: string
  sub?: string
  valueColor?: string
}) {
  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-3">
      <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-xl font-bold ${valueColor ?? 'text-white'}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function BankrollPanel({
  totalPnl, todayPnl, openPositions, winRate, resolvedCount, liveBalance, liveStartingBankroll,
}: Props) {
  // Reference for the % gain calc: the live starting bankroll if we have one,
  // otherwise the paper-mode fallback so legacy displays still work.
  const startingRef = liveStartingBankroll ?? PAPER_FALLBACK_STARTING_BANKROLL
  // Use the live DB value when the reconciler has written it; otherwise fall back
  // to the computed estimate so the card always shows something sensible.
  const bankroll  = liveBalance ?? (startingRef + totalPnl)
  const pnlPct    = ((bankroll - startingRef) / startingRef) * 100
  const isUp      = pnlPct >= 0
  const isTodayUp = todayPnl >= 0
  // Show a small label so the user knows whether this is the authoritative value
  const bankrollSub = liveBalance != null
    ? `${isUp ? '+' : ''}${pnlPct.toFixed(1)}% · live balance`
    : `${isUp ? '+' : ''}${pnlPct.toFixed(1)}% · estimated`

  return (
    <div className="grid grid-cols-5 gap-3">
      <MetricCard
        label="Bankroll"
        value={`$${bankroll.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
        sub={bankrollSub}
        valueColor={isUp ? 'text-green-400' : 'text-red-400'}
      />
      <MetricCard
        label="Total P&L"
        value={`${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`}
        sub={`${resolvedCount} resolved trades`}
        valueColor={totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}
      />
      <MetricCard
        label="Today P&L"
        value={`${isTodayUp ? '+' : ''}$${todayPnl.toFixed(2)}`}
        sub={todayPnl === 0 ? 'no resolved trades today' : isTodayUp ? 'profitable today' : 'loss today'}
        valueColor={isTodayUp && todayPnl !== 0 ? 'text-green-400' : todayPnl < 0 ? 'text-red-400' : 'text-gray-400'}
      />
      <MetricCard
        label="Open Positions"
        value={String(openPositions)}
        sub="active trades"
      />
      <MetricCard
        label="Win Rate"
        value={`${winRate.toFixed(1)}%`}
        sub={winRate >= 65 ? '✓ above 65% target' : resolvedCount > 0 ? `${resolvedCount} trades` : 'no data'}
        valueColor={winRate >= 65 ? 'text-green-400' : winRate > 0 ? 'text-yellow-400' : 'text-gray-400'}
      />
    </div>
  )
}
