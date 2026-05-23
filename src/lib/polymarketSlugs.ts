// City → Polymarket event-slug fragment (same map the collector uses).
// Combine with formatDateSlug() to build the full event slug used by
// gamma-api.polymarket.com/events/slug/{slug}.

export const POLYMARKET_CITY_SLUG: Record<string, string> = {
  "NYC": "nyc", "Chicago": "chicago", "Miami": "miami",
  "Los Angeles": "los-angeles", "Dallas": "dallas", "Atlanta": "atlanta",
  "Houston": "houston", "Austin": "austin", "Seattle": "seattle",
  "San Francisco": "san-francisco", "Denver": "denver",
  "London": "london", "Paris": "paris", "Madrid": "madrid",
  "Munich": "munich", "Milan": "milan", "Amsterdam": "amsterdam",
  "Warsaw": "warsaw", "Helsinki": "helsinki",
  "Istanbul": "istanbul", "Ankara": "ankara", "Moscow": "moscow",
  "Tel Aviv": "tel-aviv", "Jeddah": "jeddah",
  "Hong Kong": "hong-kong", "Seoul": "seoul", "Tokyo": "tokyo",
  "Busan": "busan", "Taipei": "taipei",
  "Beijing": "beijing", "Shanghai": "shanghai", "Guangzhou": "guangzhou",
  "Shenzhen": "shenzhen", "Chengdu": "chengdu", "Chongqing": "chongqing",
  "Wuhan": "wuhan", "Singapore": "singapore",
  "Kuala Lumpur": "kuala-lumpur", "Manila": "manila", "Jakarta": "jakarta",
  "Lucknow": "lucknow", "Karachi": "karachi", "Wellington": "wellington",
  "Toronto": "toronto", "Mexico City": "mexico-city",
  "São Paulo": "sao-paulo", "Buenos Aires": "buenos-aires",
  "Panama City": "panama-city", "Cape Town": "cape-town", "Lagos": "lagos",
}

const MONTHS = [
  'january', 'february', 'march', 'april', 'may', 'june',
  'july', 'august', 'september', 'october', 'november', 'december',
]

// Build the date fragment Polymarket uses, e.g. "may-22-2026".
// dateYmd is "YYYY-MM-DD".
export function formatPolymarketDateSlug(dateYmd: string): string {
  const [y, m, d] = dateYmd.split('-').map((s) => parseInt(s, 10))
  if (!y || !m || !d) return ''
  return `${MONTHS[m - 1]}-${d}-${y}`
}

export function buildEventSlug(city: string, dateYmd: string): string | null {
  const cityFragment = POLYMARKET_CITY_SLUG[city]
  const dateFragment = formatPolymarketDateSlug(dateYmd)
  if (!cityFragment || !dateFragment) return null
  return `highest-temperature-in-${cityFragment}-on-${dateFragment}`
}


// --- Bracket-label parser ---------------------------------------------------
// Same logic as scripts/trader_price_collector.py:parse_bracket. Used to
// match each gamma-returned market to the bracket_label we already store
// in Supabase, so the live-tick price merges cleanly with the historical
// series for the focused bracket.

const RANGE_RE  = /between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°([fc])/i
const LE_RE     = /(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+below/i
const GE_RE     = /(-?\d+(?:\.\d+)?)\s*°([fc])\s+or\s+higher/i
const SINGLE_RE = /be\s+(-?\d+(?:\.\d+)?)\s*°([fc])\s+on/i

export function parseBracketLabel(question: string): string | null {
  const f = parseBracketFull(question)
  return f?.label ?? null
}


/** Same regex set as parseBracketLabel, but returns the bracket bounds too.
 * Bounds use the half-degree window that matches Wunderground's whole-degree
 * rounding (e.g. "86-87°F" → [85.5, 87.5)) — same convention as the original
 * Python parse_bracket in scripts/trader_price_collector.py. */
export interface BracketFull {
  label: string
  lowNative: number       // -Infinity for "≤X" brackets
  highNative: number      // +Infinity for "≥X" brackets
  unit: 'F' | 'C'
}
export function parseBracketFull(question: string): BracketFull | null {
  if (!question) return null
  let m = question.match(RANGE_RE)
  if (m) {
    const lo = parseFloat(m[1]), hi = parseFloat(m[2])
    const u = m[3].toUpperCase() as 'F' | 'C'
    return { label: `${parseInt(m[1], 10)}-${parseInt(m[2], 10)}°${u}`,
             lowNative: lo - 0.5, highNative: hi + 0.5, unit: u }
  }
  m = question.match(LE_RE)
  if (m) {
    const v = parseFloat(m[1]); const u = m[2].toUpperCase() as 'F' | 'C'
    return { label: `≤${parseInt(m[1], 10)}°${u}`,
             lowNative: -Infinity, highNative: v + 0.5, unit: u }
  }
  m = question.match(GE_RE)
  if (m) {
    const v = parseFloat(m[1]); const u = m[2].toUpperCase() as 'F' | 'C'
    return { label: `≥${parseInt(m[1], 10)}°${u}`,
             lowNative: v - 0.5, highNative: Infinity, unit: u }
  }
  m = question.match(SINGLE_RE)
  if (m) {
    const v = parseFloat(m[1]); const u = m[2].toUpperCase() as 'F' | 'C'
    return { label: `${parseInt(m[1], 10)}°${u}`,
             lowNative: v - 0.5, highNative: v + 0.5, unit: u }
  }
  return null
}
