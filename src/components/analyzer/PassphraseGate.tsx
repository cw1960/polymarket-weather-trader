import { useState } from 'react'
import { checkHealth, setToken } from './api'

export default function PassphraseGate({ onAuthed }: { onAuthed: () => void }) {
  const [pw, setPw] = useState('')
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)

  async function submit() {
    setError('')
    setChecking(true)
    setToken(pw)
    const ok = await checkHealth()
    setChecking(false)
    if (!ok) {
      setError('Analyzer worker unreachable. Check VITE_ANALYZER_URL and that the worker is running.')
      return
    }
    onAuthed()
  }

  return (
    <div className="max-w-md mx-auto mt-12 bg-gray-800 border border-gray-700 rounded-lg p-6">
      <h2 className="text-lg font-semibold text-white mb-1">Trader Analyzer</h2>
      <p className="text-sm text-gray-400 mb-4">Enter the analyzer auth token to continue.</p>
      <input
        type="password"
        value={pw}
        onChange={(e) => setPw(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
        placeholder="Auth token"
        className="w-full bg-gray-700 text-white text-sm px-3 py-2 rounded border border-gray-600 focus:outline-none focus:border-blue-500 mb-3"
        autoFocus
      />
      <button
        onClick={submit}
        disabled={!pw || checking}
        className="w-full bg-blue-700 hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold py-2 rounded transition-colors"
      >
        {checking ? 'Checking…' : 'Unlock'}
      </button>
      {error && <div className="mt-3 text-xs text-red-400">{error}</div>}
    </div>
  )
}
