import { useState, useEffect } from 'react'

/**
 * Shows when the next batch of Polymarket weather markets is expected to
 * resolve, in Monterrey time (CST = UTC-6, no DST year-round).
 *
 * Polymarket settles daily weather markets at end-of-local-day for each
 * city.  The four main resolution batches, expressed in Monterrey time:
 *
 *   9:00 AM  — Asia      (Tokyo, Seoul, HK, Singapore, Beijing, etc.)
 *   4:00 PM  — Europe    (London, Paris, Madrid, Munich, Istanbul, Moscow…)
 *  10:00 PM  — US East   (NYC, Miami, Atlanta — EDT = UTC-4 in summer)
 *  11:00 PM  — US Central (Chicago, Dallas, Houston — CDT = UTC-5 in summer)
 *
 * Our resolver runs at the top of every hour, so resolved trades appear
 * in the dashboard within ≤60 min after each window.
 */

interface Window {
  label:  string
  detail: string
  hour:   number   // Monterrey hour (0–23)
  minute: number
}

const WINDOWS: Window[] = [
  { label: 'Asia',       detail: 'Tokyo · Seoul · Singapore',  hour: 9,  minute: 0 },
  { label: 'Europe',     detail: 'London · Paris · Istanbul',  hour: 16, minute: 0 },
  { label: 'US East',    detail: 'NYC · Miami · Atlanta',      hour: 22, minute: 0 },
  { label: 'US Central', detail: 'Chicago · Dallas · Houston', hour: 23, minute: 0 },
]

/** Current Monterrey time components (UTC-6, no DST). */
function mtyNow() {
  const now  = new Date()
  const h    = (now.getUTCHours()   + 18) % 24   // UTC-6 ≡ UTC+18 mod 24
  const m    = now.getUTCMinutes()
  const s    = now.getUTCSeconds()
  const total = h * 3600 + m * 60 + s
  return { h, m, s, total }
}

/** Format an hour (0-23) as "9:00 AM" / "10:00 PM". */
function fmt12(hour: number, minute = 0): string {
  const period = hour >= 12 ? 'PM' : 'AM'
  const h12    = hour % 12 || 12
  const mm     = minute.toString().padStart(2, '0')
  return `${h12}:${mm} ${period}`
}

/** Human-readable countdown from totalSeconds. */
function fmtCountdown(secs: number): string {
  if (secs >= 3600) {
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    return m === 0 ? `${h}h` : `${h}h ${m}m`
  }
  if (secs >= 60) {
    const m = Math.floor(secs / 60)
    const s = secs % 60
    return s === 0 ? `${m}m` : `${m}m ${s}s`
  }
  return `${secs}s`
}

export default function NextResolutionBadge() {
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const { total } = mtyNow()

  // Find the next window that hasn't started yet today
  const windowsSec = WINDOWS.map(w => ({ ...w, sec: w.hour * 3600 + w.minute * 60 }))
  const next       = windowsSec.find(w => w.sec > total) ?? windowsSec[0]

  let secsUntil = next.sec - total
  if (secsUntil <= 0) secsUntil += 86400   // wraps to tomorrow

  const isImminent = secsUntil < 30 * 60   // within 30 minutes → yellow alert

  // Also show which window just passed (i.e. what may have just resolved)
  const prevIdx    = (windowsSec.indexOf(next) - 1 + WINDOWS.length) % WINDOWS.length
  const prev       = windowsSec[prevIdx]
  const secsSince  = total - prev.sec
  const justFired  = secsSince >= 0 && secsSince < 3600   // resolver picks up within 1h

  return (
    <div className="flex items-center gap-2 text-xs select-none">
      {/* "Just resolved" pill — shown for up to 1h after a window */}
      {justFired && (
        <span className="flex items-center gap-1 px-2 py-0.5 rounded bg-blue-900/50 text-blue-300">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
          {prev.label} resolving now
        </span>
      )}

      {/* Next window */}
      <span className="text-gray-600">Next resolution:</span>
      <span className={`font-semibold ${isImminent ? 'text-yellow-400' : 'text-gray-300'}`}>
        {fmt12(next.hour, next.minute)}
      </span>
      <span className="text-gray-600">·</span>
      <span className={isImminent ? 'text-yellow-500' : 'text-gray-500'}>{next.label}</span>
      <span
        className={`tabular-nums font-mono ${isImminent ? 'text-yellow-400 font-semibold' : 'text-gray-600'}`}
        title={next.detail}
      >
        ({fmtCountdown(secsUntil)})
      </span>
    </div>
  )
}
