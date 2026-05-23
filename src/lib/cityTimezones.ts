// IANA timezone per city. Ported from scripts/config.py CITY_TIMEZONES.
// Used by the live Watchlist to compute the CURRENT local hour at each
// city, which is the key tradeability filter (we only show markets where
// it's still daytime — 10:00 ≤ local hour < 17:00).
//
// Browsers ship the IANA tz database; `Intl.DateTimeFormat` evaluates these
// natively. No external library needed.

export const CITY_TIMEZONES: Record<string, string> = {
  "NYC":           "America/New_York",
  "Chicago":       "America/Chicago",
  "Miami":         "America/New_York",
  "Los Angeles":   "America/Los_Angeles",
  "Dallas":        "America/Chicago",
  "Atlanta":       "America/New_York",
  "Houston":       "America/Chicago",
  "Austin":        "America/Chicago",
  "Seattle":       "America/Los_Angeles",
  "San Francisco": "America/Los_Angeles",
  "Denver":        "America/Denver",
  "London":        "Europe/London",
  "Paris":         "Europe/Paris",
  "Madrid":        "Europe/Madrid",
  "Munich":        "Europe/Berlin",
  "Milan":         "Europe/Rome",
  "Amsterdam":     "Europe/Amsterdam",
  "Warsaw":        "Europe/Warsaw",
  "Helsinki":      "Europe/Helsinki",
  "Istanbul":      "Europe/Istanbul",
  "Ankara":        "Europe/Istanbul",
  "Moscow":        "Europe/Moscow",
  "Tel Aviv":      "Asia/Jerusalem",
  "Jeddah":        "Asia/Riyadh",
  "Hong Kong":     "Asia/Hong_Kong",
  "Seoul":         "Asia/Seoul",
  "Tokyo":         "Asia/Tokyo",
  "Busan":         "Asia/Seoul",
  "Taipei":        "Asia/Taipei",
  "Beijing":       "Asia/Shanghai",
  "Shanghai":      "Asia/Shanghai",
  "Guangzhou":     "Asia/Shanghai",
  "Shenzhen":      "Asia/Shanghai",
  "Chengdu":       "Asia/Shanghai",
  "Chongqing":     "Asia/Shanghai",
  "Wuhan":         "Asia/Shanghai",
  "Singapore":     "Asia/Singapore",
  "Kuala Lumpur":  "Asia/Kuala_Lumpur",
  "Manila":        "Asia/Manila",
  "Jakarta":       "Asia/Jakarta",
  "Lucknow":       "Asia/Kolkata",
  "Karachi":       "Asia/Karachi",
  "Wellington":    "Pacific/Auckland",
  "Toronto":       "America/Toronto",
  "Mexico City":   "America/Mexico_City",
  "São Paulo":     "America/Sao_Paulo",
  "Buenos Aires":  "America/Argentina/Buenos_Aires",
  "Panama City":   "America/Panama",
  "Cape Town":     "Africa/Johannesburg",
  "Lagos":         "Africa/Lagos",
}


/** Estimated UTC ms when a Polymarket weather market for (city, forecastDate)
 * will actually resolve.
 *
 * We CAN'T trust gamma's event.endDate: for weather markets it's set to
 * 12:00 UTC of the resolution calendar date, which is hours (sometimes a
 * full day) BEFORE the market actually resolves. The market resolves once
 * Wunderground publishes the daily-high for that city's local date — i.e.
 * after end-of-local-day, plus a small finalization buffer.
 *
 * Empirically (verified across multiple resolved markets in May 2026) this
 * is end_of_local_day + 2h. Same formula the Python collector used.
 *
 * forecastDate is "YYYY-MM-DD" in the city's local calendar.
 */
export function estimateResolutionUtcMs(
  cityTz: string,
  forecastDate: string,
  bufferHours: number = 2,
): number {
  // Anchor: noon UTC of the forecast date — far from any DST transitions.
  const [y, m, d] = forecastDate.split('-').map(Number)
  const noonUtcMs = Date.UTC(y, m - 1, d, 12, 0, 0)
  // Format that anchor in cityTz to learn the offset
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: cityTz, hour12: false,
    hour: '2-digit', minute: '2-digit',
  }).formatToParts(noonUtcMs)
  const get = (t: string) => parseInt(parts.find((p) => p.type === t)?.value ?? '0', 10)
  let cityHour = get('hour'); if (cityHour === 24) cityHour = 0
  const cityMin = get('minute')
  // offset minutes (city - UTC) at the anchor moment
  const offsetMin = (cityHour - 12) * 60 + cityMin
  // City-local midnight of the NEXT day in UTC ms = anchor + (24 - cityHour - cityMin/60)
  // Or equivalently: start_of_city_day(date) + 24h
  const startOfCityDay = Date.UTC(y, m - 1, d, 0, 0, 0) - offsetMin * 60_000
  const endOfCityDay = startOfCityDay + 24 * 3600_000
  return endOfCityDay + bufferHours * 3600_000
}


/** UTC ms corresponding to the start of "today" in the given timezone.
 * Computed by asking what the current city time-of-day is and subtracting
 * elapsed seconds from now. DST-safe within a single day. */
export function startOfCityDayUtcMs(cityTz: string, now: Date = new Date()): number {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: cityTz, hour12: false,
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  const parts = fmt.formatToParts(now)
  const get = (t: string) => parseInt(parts.find((p) => p.type === t)?.value ?? '0', 10)
  // Some Intl implementations emit "24" instead of "00" for midnight — normalize.
  let h = get('hour'); if (h === 24) h = 0
  const m = get('minute'), s = get('second')
  const elapsedSec = h * 3600 + m * 60 + s
  return now.getTime() - elapsedSec * 1000
}


/** Format a UTC ms in the given timezone as "HH:MM" (24-hour). */
export function formatCityTime(ms: number, cityTz: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: cityTz, hour12: false,
    hour: '2-digit', minute: '2-digit',
  }).format(new Date(ms))
}


/** Current hour-of-day at a city. Returns null if we don't know the tz. */
export function currentLocalHour(city: string, now: Date = new Date()): number | null {
  const tz = CITY_TIMEZONES[city]
  if (!tz) return null
  // Intl.DateTimeFormat with hour: 'numeric' + hour12: false gives "0"–"23"
  // in the target timezone.
  const hourStr = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hour: 'numeric', hour12: false,
  }).format(now)
  const h = parseInt(hourStr, 10)
  return Number.isFinite(h) ? h : null
}


/** Parse the forecast date out of a Polymarket slug like
 *   "highest-temperature-in-nyc-on-may-22-2026" → "2026-05-22"
 * Returns null on parse failure. */
const MONTHS = ['january','february','march','april','may','june','july','august','september','october','november','december']
export function parseForecastDateFromSlug(slug: string): string | null {
  // Match "...-on-{month}-{day}-{year}" at the end
  const m = slug.match(/-on-([a-z]+)-(\d{1,2})-(\d{4})\s*$/i)
  if (!m) return null
  const monthIdx = MONTHS.indexOf(m[1].toLowerCase())
  if (monthIdx < 0) return null
  const month = String(monthIdx + 1).padStart(2, '0')
  const day = m[2].padStart(2, '0')
  return `${m[3]}-${month}-${day}`
}


/** Extract the city from a slug like "highest-temperature-in-san-francisco-on-may-22-2026".
 * Returns the city in its display form ("San Francisco") if known, or the raw
 * slug fragment ("san-francisco") otherwise. */
export function parseCityFromSlug(slug: string, citySlugToDisplay: Record<string, string>): string | null {
  const m = slug.match(/^highest-temperature-in-(.+?)-on-[a-z]+-\d{1,2}-\d{4}\s*$/i)
  if (!m) return null
  const frag = m[1].toLowerCase()
  return citySlugToDisplay[frag] ?? frag
}
