import type { AnalyzeStartResponse, AnalyzerResponse, CommentaryResponse, JobStatus, RunSummary, WatchlistEntry } from './types'

const BASE_URL = (import.meta.env.VITE_ANALYZER_URL || 'http://127.0.0.1:8001').replace(/\/$/, '')
const TOKEN_KEY = 'analyzer_token'

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

async function apiCall<T>(
  path: string,
  body: unknown,
  method: 'GET' | 'POST' | 'DELETE' | 'PATCH' = 'POST',
): Promise<T> {
  const token = getToken()
  if (!token) throw new Error('Not authenticated')

  const init: RequestInit = {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  }
  if (body !== undefined && body !== null && method !== 'GET') {
    init.body = JSON.stringify(body)
  }

  const r = await fetch(`${BASE_URL}${path}`, init)
  if (r.status === 401) {
    clearToken()
    throw new Error('Invalid token')
  }
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`${r.status}: ${text}`)
  }
  return r.json()
}

export async function checkHealth(): Promise<boolean> {
  try {
    const r = await fetch(`${BASE_URL}/health`)
    return r.ok
  } catch {
    return false
  }
}

export async function startAnalyze(
  walletOrUsername: string,
  opts: { forceRefresh?: boolean } = {},
): Promise<AnalyzeStartResponse> {
  const isAddress = walletOrUsername.startsWith('0x')
  return apiCall<AnalyzeStartResponse>('/analyze', {
    [isAddress ? 'wallet' : 'username']: walletOrUsername,
    force_refresh: !!opts.forceRefresh,
  })
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  return apiCall<JobStatus>(`/jobs/${jobId}`, null, 'GET')
}

/**
 * High-level helper: kick off an analysis and poll until done. Calls
 * `onProgress` on every status update so the UI can show stage / pct.
 * Resolves with the final AnalyzerResponse, or rejects with an Error.
 */
export async function analyze(
  walletOrUsername: string,
  opts: { forceRefresh?: boolean; onProgress?: (s: JobStatus) => void } = {},
): Promise<AnalyzerResponse> {
  const start = await startAnalyze(walletOrUsername, { forceRefresh: opts.forceRefresh })

  // Inline cache-hit case: server already returned the full result
  if (start.from_cache) {
    return start as unknown as AnalyzerResponse
  }

  if (!start.job_id) {
    throw new Error('server did not return a job_id and result is not cached')
  }

  // Poll
  const POLL_INTERVAL_MS = 2000
  const MAX_DURATION_MS = 30 * 60 * 1000 // 30 minutes
  const startedAt = Date.now()

  while (true) {
    const s = await getJobStatus(start.job_id)
    opts.onProgress?.(s)
    if (s.status === 'done' && s.result) return s.result
    if (s.status === 'error') throw new Error(s.error || 'analysis failed')
    if (Date.now() - startedAt > MAX_DURATION_MS) {
      throw new Error('analysis exceeded 30-minute deadline')
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
  }
}

export async function commentary(
  runId: number,
  mode: 'standard' | 'deep' = 'standard',
): Promise<CommentaryResponse> {
  return apiCall<CommentaryResponse>('/commentary', { run_id: runId, mode })
}

export async function fetchHistory(limit = 50): Promise<{ runs: RunSummary[] }> {
  return apiCall<{ runs: RunSummary[] }>(`/history?limit=${limit}`, null, 'GET')
}

export async function fetchWatchlist(): Promise<{ entries: WatchlistEntry[] }> {
  return apiCall<{ entries: WatchlistEntry[] }>(`/watchlist`, null, 'GET')
}

export async function followWallet(wallet: string, label = ''): Promise<void> {
  await apiCall<unknown>(`/watchlist`, { wallet, label }, 'POST')
}

export async function unfollowWallet(wallet: string): Promise<void> {
  await apiCall<unknown>(`/watchlist/${wallet}`, null, 'DELETE')
}

export async function setWatchlistLabel(wallet: string, label: string): Promise<void> {
  await apiCall<unknown>(`/watchlist/${wallet}`, { label }, 'PATCH')
}

/**
 * Personal annotations for any analyzed trader.  Stored in
 * `analyzer_annotations` keyed by wallet — does NOT require the trader
 * to be on the watchlist.
 */
export async function getAnnotations(
  wallet: string,
): Promise<{ headline: string; notes: string }> {
  return apiCall<{ headline: string; notes: string }>(
    `/annotations/${wallet}`, null, 'GET',
  )
}

export async function setAnnotations(
  wallet: string,
  patch: { headline?: string; notes?: string },
): Promise<void> {
  await apiCall<unknown>(`/annotations/${wallet}`, patch, 'PATCH')
}

export async function deleteTrader(wallet: string): Promise<void> {
  await apiCall<unknown>(`/trader/${wallet}`, null, 'DELETE')
}
