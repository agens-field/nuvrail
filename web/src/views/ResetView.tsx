/**
 * ResetView — /#/reset?token=...
 *
 * Consumes the reset token from the URL query string and lets the user set a
 * new password. Token is validated server-side; this view handles the UX.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { resetPassword } from '../api/client'

export default function ResetView() {
  const navigate = useNavigate()
  const token = new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('token') ?? ''

  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  if (!token) {
    return (
      <div className="max-w-md mx-auto mt-16">
        <div className="bg-surface rounded-lg p-8 border border-edge text-center">
          <p className="text-red-400">Missing or invalid reset token.</p>
          <a href="#/reset-request" className="mt-4 block text-accent underline text-sm">
            Request a new reset link
          </a>
        </div>
      </div>
    )
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (newPw !== confirmPw) {
      setError('Passwords do not match.')
      return
    }
    if (newPw.length < 12) {
      setError('Password must be at least 12 characters.')
      return
    }

    setLoading(true)
    try {
      await resetPassword(token, newPw)
      setDone(true)
      setTimeout(() => navigate('/login', { replace: true }), 2500)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Reset failed. The link may have expired.'
      )
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <div className="max-w-md mx-auto mt-16">
        <div className="bg-surface rounded-lg p-8 border border-edge text-center space-y-3">
          <p className="text-2xl">✅</p>
          <h1 className="text-xl font-bold text-fg">Password updated</h1>
          <p className="text-fg-3 text-sm">Redirecting to sign in…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-md mx-auto mt-16">
      <div className="bg-surface rounded-lg p-8 border border-edge">
        <h1 className="text-2xl font-display font-black text-fg mb-6">Set new password</h1>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">
              New password
            </label>
            <input
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              required
              minLength={12}
              autoComplete="new-password"
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg focus:outline-none focus:ring-2 focus:ring-accent"
            />
            <p className="mt-1 text-xs text-fg-3">Minimum 12 characters</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">
              Confirm new password
            </label>
            <input
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              required
              autoComplete="new-password"
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-accent hover:bg-accent-hi disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
          >
            {loading ? 'Updating…' : 'Set new password'}
          </button>
        </form>
      </div>
    </div>
  )
}
