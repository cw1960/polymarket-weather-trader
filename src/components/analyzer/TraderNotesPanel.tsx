/**
 * TraderNotesPanel
 * ──────────────────
 * Long-form personal notes for any analyzed trader.  Stored in
 * `analyzer_annotations` (keyed by wallet) — independent of the
 * watchlist, so any wallet that's been analyzed can have notes.
 *
 * Save behavior: auto-saves on blur AND on Cmd/Ctrl+Enter so you
 * never have to click a save button.  A subtle ✓ pulses next to
 * the heading after each successful write.
 */
import { useEffect, useState } from 'react'
import { getAnnotations, setAnnotations } from './api'

interface Props {
  wallet: string
}

export default function TraderNotesPanel({ wallet }: Props) {
  const [loaded,  setLoaded]  = useState(false)
  const [draft,   setDraft]   = useState('')
  const [dirty,   setDirty]   = useState(false)
  const [saving,  setSaving]  = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const ann = await getAnnotations(wallet)
        if (cancelled) return
        setDraft(ann.notes || '')
      } catch {
        if (!cancelled) setDraft('')
      } finally {
        if (!cancelled) setLoaded(true); setDirty(false)
      }
    }
    setLoaded(false)
    load()
    return () => { cancelled = true }
  }, [wallet])

  async function save() {
    if (!dirty || saving) return
    setSaving(true)
    try {
      await setAnnotations(wallet, { notes: draft })
      setDirty(false)
      setSavedAt(Date.now())
    } catch {
      /* non-critical: keep dirty so user can retry */
    } finally {
      setSaving(false)
    }
  }

  if (!loaded) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <div className="text-sm font-semibold text-gray-300 mb-2">Notes</div>
        <div className="text-xs text-gray-500">Loading…</div>
      </div>
    )
  }

  const savedRecently = savedAt && Date.now() - savedAt < 3000

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          Notes
          {dirty && <span className="text-xs text-yellow-400">● unsaved</span>}
          {!dirty && savedRecently && (
            <span className="text-xs text-green-400 animate-pulse">✓ saved</span>
          )}
          {saving && <span className="text-xs text-gray-500">saving…</span>}
        </div>
        <div className="text-[10px] text-gray-500">⌘+Enter to save</div>
      </div>
      <textarea
        value={draft}
        onChange={(e) => { setDraft(e.target.value); setDirty(true) }}
        onBlur={save}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault()
            save()
          }
        }}
        placeholder={
          'Your personal notes on this trader…\n\nE.g. "Strong in Hong Kong but losing net cash. Watch for one more week."'
        }
        rows={6}
        className="w-full bg-gray-900 text-gray-200 text-sm px-3 py-2 rounded border border-gray-700 focus:outline-none focus:border-blue-500 resize-y"
      />
    </div>
  )
}
