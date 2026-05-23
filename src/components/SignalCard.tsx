import { useState } from 'react'
import { TradeSignal } from '../types'

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

function formatDate(d: string | null): string {
  if (!d) return ''
  const dt = new Date(d + 'T12:00:00Z')
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}

function confidenceLabel(edge: number): { label: string; cls: string } {
  if (edge >= 0.2) return { label: 'HIGH', cls: 'bg-green-900 text-green-300' }
  if (edge >= 0.12) return { label: 'MEDIUM', cls: 'bg-yellow-900 text-yellow-300' }
  return { label: 'LOW', cls: 'bg-gray-700 text-gray-300' }
}

function polymarketUrl(slug: string | null): string | null {
  if (!slug) return null
  return `https://polymarket.com/event/${slug}`
}

interface Props {
  signal: TradeSignal
}

export default function SignalCard({ signal }: Props) {
  const [expanded, setExpanded] = useState(false)

  const isYes = signal.side === 'YES'
  const edgePct = Math.round(signal.edge * 100)
  const modelPct = Math.round(signal.model_probability * 100)
  const marketPct = Math.round(signal.market_price * 100)
  const noPricePct = Math.round((1 - signal.market_price) * 100)
  const conf = confidenceLabel(signal.edge)
  const suggestedSize = signal.recommended_position ?? 0
  const pmUrl = polymarketUrl(signal.event_slug)

  // How-to-act description
  const actionText = isYes
    ? `Buy YES shares at ~${marketPct}¢ each. Each share pays $1.00 if the market resolves YES.`
    : `Buy NO shares at ~${noPricePct}¢ each. Each share pays $1.00 if the market resolves NO.`

  // Model context.
  // mean_high / std_high are stored in Celsius for all cities (the trading
  // pipeline normalizes to C internally).  When the market's outcome label
  // is in °F, convert before display so the number matches the bracket.
  const isF = signal.outcome.includes('°F')
  const unit = isF ? '°F' : '°C'
  const meanDisplay = signal.mean_high != null
    ? (isF ? signal.mean_high * 9 / 5 + 32 : signal.mean_high)
    : null
  const stdDisplay = signal.std_high != null
    ? (isF ? signal.std_high * 9 / 5 : signal.std_high)
    : null
  const meanStr = meanDisplay != null ? `${meanDisplay.toFixed(1)}${unit}` : null
  const stdStr  = stdDisplay  != null ? `±${stdDisplay.toFixed(1)}${unit}` : null

  const whyText = meanStr
    ? `GFS 31-member ensemble forecasts a mean high of ${meanStr} ${stdStr ?? ''} for this date. The model assigns ${modelPct}% probability to this bucket vs the market's ${marketPct}% — a +${edgePct}pt discrepancy.`
    : `The model assigns ${modelPct}% probability to this bucket vs the market's ${marketPct}% — a +${edgePct}pt discrepancy.`

  const question = signal.market_question || `Will the highest temperature in ${signal.city} be ${signal.outcome}?`
  const dateLabel = formatDate(signal.forecast_date)

  return (
    <div className="relative bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      {/* Side stripe */}
      <div className={`absolute left-0 top-0 bottom-0 w-1 ${isYes ? 'bg-green-500' : 'bg-red-500'}`} />

      <div className="pl-4 pr-4 py-3">
        {/* Row 1: city / outcome / badges / time */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-white font-bold text-lg">{signal.city}</span>
            <span className="font-mono text-gray-200 font-semibold">{signal.outcome}</span>
            <span className={`text-xs font-bold px-2 py-0.5 rounded ${isYes ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}`}>
              BUY {signal.side}
            </span>
            {signal.edge >= 0.2 && (
              <span className="text-xs font-bold px-2 py-0.5 rounded bg-yellow-800 text-yellow-200">
                ⚡ HIGH EDGE
              </span>
            )}
          </div>
          <span className="text-xs text-gray-500 shrink-0 ml-2">{timeAgo(signal.signal_time)}</span>
        </div>

        {/* Row 2: market question + date */}
        <div className="flex items-center gap-2 mb-3">
          <p className="text-xs text-gray-400 italic flex-1 leading-snug">{question}</p>
          {dateLabel && (
            <span className="text-xs font-semibold text-blue-400 shrink-0 bg-blue-950 px-2 py-0.5 rounded">
              {dateLabel}
            </span>
          )}
        </div>

        {/* Row 3: key numbers */}
        <div className="flex items-center gap-6 mb-3">
          <div>
            <div className="text-xs text-gray-500 mb-0.5">Model</div>
            <div className={`text-xl font-bold ${isYes ? 'text-green-400' : 'text-red-400'}`}>{modelPct}%</div>
          </div>
          <div>
            <div className="text-xs text-gray-500 mb-0.5">Market</div>
            <div className="text-xl font-bold text-gray-400">{marketPct}%</div>
          </div>
          <div>
            <div className="text-xs text-gray-500 mb-0.5">Edge</div>
            <div className="text-xl font-bold text-yellow-400">+{edgePct}pts</div>
          </div>
          {meanStr && (
            <div>
              <div className="text-xs text-gray-500 mb-0.5">Model forecast</div>
              <div className="text-sm font-semibold text-gray-300">{meanStr} {stdStr}</div>
            </div>
          )}
        </div>

        {/* Row 4: confidence / size / links / expand */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded ${conf.cls}`}>{conf.label}</span>
            {suggestedSize > 0 && (
              <span className="text-sm text-gray-300 font-medium">${suggestedSize.toFixed(2)} suggested</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setExpanded((e) => !e)}
              className="text-xs text-gray-500 hover:text-gray-300 transition-colors underline underline-offset-2"
            >
              {expanded ? 'Hide explanation' : 'Why this trade?'}
            </button>
            {pmUrl && (
              <a
                href={pmUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs px-2 py-0.5 rounded bg-blue-900 hover:bg-blue-800 text-blue-300 font-semibold transition-colors"
              >
                View on Polymarket ↗
              </a>
            )}
          </div>
        </div>

        {/* Expandable explanation */}
        {expanded && (
          <div className="mt-3 pt-3 border-t border-gray-700 space-y-2">
            <div>
              <span className="text-xs font-bold text-gray-400 uppercase tracking-wider">Why</span>
              <p className="text-xs text-gray-300 mt-1 leading-relaxed">{whyText}</p>
            </div>
            <div>
              <span className="text-xs font-bold text-gray-400 uppercase tracking-wider">How to act</span>
              <p className="text-xs text-gray-300 mt-1 leading-relaxed">{actionText}</p>
              {isYes ? (
                <p className="text-xs text-gray-500 mt-1">
                  Risk: ${suggestedSize.toFixed(2)} · Max reward: ${(suggestedSize / signal.market_price).toFixed(2)} · Implied EV: +${(suggestedSize * signal.edge / signal.market_price).toFixed(2)}
                </p>
              ) : (
                <p className="text-xs text-gray-500 mt-1">
                  Risk: ${suggestedSize.toFixed(2)} · Max reward: ${(suggestedSize / (1 - signal.market_price)).toFixed(2)} · Implied EV: +${(suggestedSize * signal.edge / (1 - signal.market_price)).toFixed(2)}
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
