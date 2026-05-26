/**
 * AccountView — /#/account
 *
 * Three capabilities:
 *   1. Change password
 *   2. Forgot / reset password (link to /#/reset-request)
 *   3. Log out (clears localStorage token + optionally revokes server-side)
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { applyChangePassword, logoutUser, clearToken } from '../api/client'

export default function AccountView() {
  const navigate = useNavigate()

  // Change-password form state
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [pwError, setPwError] = useState<string | null>(null)
  const [pwSuccess, setPwSuccess] = useState(false)
  const [pwLoading, setPwLoading] = useState(false)

  // Logout state
  const [logoutLoading, setLogoutLoading] = useState(false)

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault()
    setPwError(null)
    setPwSuccess(false)

    if (newPw !== confirmPw) {
      setPwError('New passwords do not match.')
      return
    }
    if (newPw.length < 12) {
      setPwError('New password must be at least 12 characters.')
      return
    }

    setPwLoading(true)
    try {
      await applyChangePassword(currentPw, newPw)
      setPwSuccess(true)
      setCurrentPw('')
      setNewPw('')
      setConfirmPw('')
    } catch (err) {
      setPwError(err instanceof Error ? err.message : 'Failed to change password.')
    } finally {
      setPwLoading(false)
    }
  }

  async function handleLogout() {
    setLogoutLoading(true)
    try {
      // Best-effort server-side revocation — ignore errors (e.g. token already gone)
      await logoutUser().catch(() => undefined)
    } finally {
      clearToken()
      navigate('/login', { replace: true })
    }
  }

  return (
    <div className="max-w-md mx-auto space-y-8">
      <h1 className="text-2xl font-bold text-fg">Account</h1>

      {/* ── Change password ───────────────────────────────────────────── */}
      <section className="bg-surface rounded-lg p-6 border border-edge space-y-4">
        <h2 className="text-lg font-semibold text-fg">Change password</h2>

        <form onSubmit={handleChangePassword} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">
              Current password
            </label>
            <input
              type="password"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              required
              autoComplete="current-password"
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>

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

          {pwError && <p className="text-red-400 text-sm">{pwError}</p>}
          {pwSuccess && (
            <p className="text-green-400 text-sm">Password changed successfully.</p>
          )}

          <button
            type="submit"
            disabled={pwLoading}
            className="w-full bg-accent hover:bg-accent-hi disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
          >
            {pwLoading ? 'Updating…' : 'Update password'}
          </button>
        </form>

        <p className="text-sm text-fg-3">
          Forgot your current password?{' '}
          <a
            href="#/reset-request"
            className="text-accent hover:text-accent underline"
          >
            Reset it
          </a>
        </p>
      </section>

      {/* ── Log out ───────────────────────────────────────────────────── */}
      <section className="bg-surface rounded-lg p-6 border border-edge">
        <h2 className="text-lg font-semibold text-fg mb-3">Log out</h2>
        <p className="text-sm text-fg-3 mb-4">
          Clears your session and revokes the server-side token.
        </p>
        <button
          onClick={handleLogout}
          disabled={logoutLoading}
          className="bg-surface-hi hover:bg-edge disabled:opacity-50 text-fg font-medium py-2 px-4 rounded transition-colors border border-edge"
        >
          {logoutLoading ? 'Logging out…' : 'Log out'}
        </button>
      </section>
    </div>
  )
}
