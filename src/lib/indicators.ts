// Pure-function technical indicators. Operate on (timestamp, value) series.
// Returned arrays are 1:1 aligned with the input (with leading nulls until
// enough history accumulates), so they slot directly into a chart that
// already has the same x positions.

export type Series = (number | null)[]


/** Simple moving average over `period`. Returns nulls until N values. */
export function sma(values: Series, period: number): Series {
  const out: Series = new Array(values.length).fill(null)
  let sum = 0
  let cnt = 0
  const buf: number[] = []
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null) {
      // Skip nulls but don't reset state — we want a continuous indicator
      // across a single missing tick.
      out[i] = cnt >= period ? sum / period : null
      continue
    }
    buf.push(v)
    sum += v
    if (buf.length > period) {
      sum -= buf.shift()!
    }
    cnt = Math.min(cnt + 1, period)
    out[i] = cnt >= period ? sum / period : null
  }
  return out
}


/** Exponential moving average. α = 2 / (period + 1). */
export function ema(values: Series, period: number): Series {
  const alpha = 2 / (period + 1)
  const out: Series = new Array(values.length).fill(null)
  let cur: number | null = null
  let seeded = 0
  let seedSum = 0
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null) { out[i] = cur; continue }
    if (cur == null) {
      // Seed with SMA of first `period` values for stability
      seedSum += v
      seeded++
      if (seeded >= period) {
        cur = seedSum / period
        out[i] = cur
      } else {
        out[i] = null
      }
    } else {
      cur = alpha * v + (1 - alpha) * cur
      out[i] = cur
    }
  }
  return out
}


/** Bollinger Bands (mid, upper, lower) using SMA + σ over `period`. */
export function bollinger(values: Series, period = 20, sd = 2): {
  mid: Series; upper: Series; lower: Series
} {
  const mid = sma(values, period)
  const upper: Series = new Array(values.length).fill(null)
  const lower: Series = new Array(values.length).fill(null)
  // Compute rolling std around the SMA window.
  for (let i = 0; i < values.length; i++) {
    if (mid[i] == null) continue
    let sumSq = 0
    let cnt = 0
    for (let j = Math.max(0, i - period + 1); j <= i; j++) {
      const v = values[j]
      if (v == null) continue
      const d = v - (mid[i] as number)
      sumSq += d * d
      cnt++
    }
    if (cnt > 1) {
      const stdev = Math.sqrt(sumSq / cnt)
      upper[i] = (mid[i] as number) + sd * stdev
      lower[i] = (mid[i] as number) - sd * stdev
    }
  }
  return { mid, upper, lower }
}


/** Wilder RSI over `period`. Output is 0..100. */
export function rsi(values: Series, period = 14): Series {
  const out: Series = new Array(values.length).fill(null)
  let avgGain: number | null = null
  let avgLoss: number | null = null
  let lastValid: number | null = null
  let warmupCount = 0
  let warmupGain = 0
  let warmupLoss = 0
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null) { out[i] = out[i - 1] ?? null; continue }
    if (lastValid == null) { lastValid = v; continue }
    const change = v - lastValid
    const gain = change > 0 ? change : 0
    const loss = change < 0 ? -change : 0
    lastValid = v
    if (avgGain == null || avgLoss == null) {
      warmupGain += gain
      warmupLoss += loss
      warmupCount++
      if (warmupCount >= period) {
        avgGain = warmupGain / period
        avgLoss = warmupLoss / period
      } else {
        continue
      }
    } else {
      avgGain = (avgGain * (period - 1) + gain) / period
      avgLoss = (avgLoss * (period - 1) + loss) / period
    }
    if (avgLoss === 0) {
      out[i] = 100
    } else {
      const rs = avgGain / avgLoss
      out[i] = 100 - 100 / (1 + rs)
    }
  }
  return out
}
