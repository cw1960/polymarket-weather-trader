import { useEffect, useState } from 'react'
import { fetchWatchlist, followWallet, unfollowWallet } from './api'

export default function FollowButton({
  wallet,
  onChange,
}: {
  wallet: string
  onChange?: () => void
}) {
  const [followed, setFollowed] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchWatchlist()
      .then((r) => {
        if (cancelled) return
        setFollowed(r.entries.some((e) => e.wallet.toLowerCase() === wallet.toLowerCase()))
      })
      .catch(() => { if (!cancelled) setFollowed(false) })
    return () => { cancelled = true }
  }, [wallet])

  async function toggle() {
    if (followed === null || busy) return
    setBusy(true)
    try {
      if (followed) {
        await unfollowWallet(wallet)
        setFollowed(false)
      } else {
        await followWallet(wallet)
        setFollowed(true)
      }
      onChange?.()
    } catch (_e) {
      /* silent */
    } finally {
      setBusy(false)
    }
  }

  if (followed === null) return null

  return (
    <button
      onClick={toggle}
      disabled={busy}
      className={`text-xs px-3 py-1.5 rounded font-semibold transition-colors disabled:opacity-40 ${
        followed
          ? 'bg-yellow-700 hover:bg-yellow-600 text-yellow-100'
          : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
      }`}
      title={followed ? 'Click to unfollow' : 'Add to watchlist'}
    >
      {followed ? '★ Following' : '☆ Follow'}
    </button>
  )
}
