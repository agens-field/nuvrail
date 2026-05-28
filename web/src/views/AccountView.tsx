/**
 * AccountView — /#/account
 *
 * Three capabilities:
 *   1. Change password
 *   2. Forgot / reset password (link to /#/reset-request)
 *   3. Log out (clears localStorage token + optionally revokes server-side)
 */
import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { applyChangePassword, logoutUser, clearToken, setToken, fetchTokenInfo, rotateToken, downloadAccountData, deleteAccount } from '../api/client'

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

  // Account deletion state
  const [deleteStep, setDeleteStep] = useState<'idle' | 'confirm' | 'password'>('idle')
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  async function handleDeleteAccount(e: React.FormEvent) {
    e.preventDefault()
    setDeleteError(null)
    setDeleteLoading(true)
    try {
      await deleteAccount(deletePassword)
      clearToken()
      navigate('/login', { replace: true })
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Deletion failed. Check your password and try again.')
    } finally {
      setDeleteLoading(false)
    }
  }

  // Data export state
  const [exportLoading, setExportLoading] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  async function handleExport() {
    setExportLoading(true)
    setExportError(null)
    try {
      await downloadAccountData()
    } catch {
      setExportError('Export failed. Please try again.')
    } finally {
      setExportLoading(false)
    }
  }

  // Token rotate state
  const [rotateConfirm, setRotateConfirm] = useState(false)
  const [newToken, setNewToken] = useState<string | null>(null)
  const [tokenCopied, setTokenCopied] = useState(false)

  const tokenInfoQuery = useQuery({ queryKey: ['token-info'], queryFn: fetchTokenInfo })

  const rotateMutation = useMutation({
    mutationFn: rotateToken,
    onSuccess: (data) => {
      setToken(data.token)          // keep current session alive
      setNewToken(data.token)
      setRotateConfirm(false)
      tokenInfoQuery.refetch()
    },
  })

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

      {/* ── Session token ─────────────────────────────────────────────── */}
      <section className="bg-surface rounded-lg p-6 border border-edge space-y-4">
        <h2 className="text-lg font-semibold text-fg">Session token</h2>

        {tokenInfoQuery.data && (
          <div className="text-sm text-fg-3 space-y-1 font-mono">
            <div>
              <span className="text-fg-2">Issued: </span>
              {tokenInfoQuery.data.created_at
                ? new Date(tokenInfoQuery.data.created_at * 1000).toLocaleString()
                : 'unknown'}
            </div>
            <div>
              <span className="text-fg-2">Last used: </span>
              {tokenInfoQuery.data.last_used_at
                ? new Date(tokenInfoQuery.data.last_used_at * 1000).toLocaleString()
                : 'not recorded yet'}
            </div>
          </div>
        )}

        {/* New token display — shown once after rotation */}
        {newToken && (
          <div className="space-y-2">
            <p className="text-sm text-amber-400 font-medium">New token — copy it now, it won't be shown again.</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 bg-surface-hi border border-edge rounded px-3 py-2 text-xs text-fg font-mono break-all">
                {newToken}
              </code>
              <button
                onClick={() => { navigator.clipboard.writeText(newToken); setTokenCopied(true) }}
                className="shrink-0 text-xs bg-accent hover:bg-accent-hi text-white px-3 py-2 rounded transition-colors"
              >
                {tokenCopied ? 'Copied!' : 'Copy'}
              </button>
            </div>
            <button
              onClick={() => { setNewToken(null); setTokenCopied(false) }}
              className="text-xs text-fg-3 hover:text-fg-2 underline"
            >
              Done
            </button>
          </div>
        )}

        {!newToken && (
          rotateConfirm ? (
            <div className="space-y-3">
              <p className="text-sm text-amber-400">
                This will immediately invalidate your current token. You'll stay logged in on this device but any other active sessions will be logged out.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => rotateMutation.mutate()}
                  disabled={rotateMutation.isPending}
                  className="text-sm bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white px-4 py-2 rounded transition-colors"
                >
                  {rotateMutation.isPending ? 'Rotating…' : 'Yes, rotate token'}
                </button>
                <button
                  onClick={() => setRotateConfirm(false)}
                  className="text-sm bg-surface-hi hover:bg-edge text-fg-2 px-4 py-2 rounded border border-edge transition-colors"
                >
                  Cancel
                </button>
              </div>
              {rotateMutation.isError && (
                <p className="text-red-400 text-sm">Failed to rotate token. Try again.</p>
              )}
            </div>
          ) : (
            <button
              onClick={() => setRotateConfirm(true)}
              className="text-sm bg-surface-hi hover:bg-edge text-fg-2 px-4 py-2 rounded border border-edge transition-colors"
            >
              Rotate token
            </button>
          )
        )}
      </section>

      {/* ── Data export ─────────────────────────────────────────────── */}
      <section className="bg-surface rounded-lg p-6 border border-edge space-y-3">
        <h2 className="text-lg font-semibold text-fg">Download my data</h2>
        <p className="text-sm text-fg-3">
          Downloads a JSON file with your account info, agents, full operation
          history, audit log, and auto-approval rules. Credential values are
          never included.
        </p>
        {exportError && <p className="text-red-400 text-sm">{exportError}</p>}
        <button
          onClick={handleExport}
          disabled={exportLoading}
          className="bg-surface-hi hover:bg-edge disabled:opacity-50 text-fg font-medium py-2 px-4 rounded transition-colors border border-edge text-sm"
        >
          {exportLoading ? 'Preparing download…' : 'Download my data'}
        </button>
      </section>

      {/* ── Delete account ────────────────────────────────────────────── */}
      <section className="bg-surface rounded-lg p-6 border border-red-900/40 space-y-3">
        <h2 className="text-lg font-semibold text-red-400">Delete account</h2>

        {deleteStep === 'idle' && (
          <>
            <p className="text-sm text-fg-3">
              Permanently deletes your account, all agents, and all credentials.
              The audit log is retained per our privacy policy. This cannot be undone.
            </p>
            <button
              onClick={() => setDeleteStep('confirm')}
              className="text-sm border border-red-800 text-red-400 hover:bg-red-900/30 px-4 py-2 rounded transition-colors"
            >
              Delete my account…
            </button>
          </>
        )}

        {deleteStep === 'confirm' && (
          <>
            <p className="text-sm text-amber-400 font-medium">
              This will immediately revoke all your agents and delete your credentials. Are you sure?
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setDeleteStep('password')}
                className="text-sm bg-red-800 hover:bg-red-700 text-white px-4 py-2 rounded transition-colors"
              >
                Yes, continue
              </button>
              <button
                onClick={() => setDeleteStep('idle')}
                className="text-sm bg-surface-hi hover:bg-edge text-fg-2 px-4 py-2 rounded border border-edge transition-colors"
              >
                Cancel
              </button>
            </div>
          </>
        )}

        {deleteStep === 'password' && (
          <form onSubmit={handleDeleteAccount} className="space-y-3">
            <p className="text-sm text-fg-3">Enter your password to confirm deletion.</p>
            <input
              type="password"
              value={deletePassword}
              onChange={(e) => setDeletePassword(e.target.value)}
              required
              autoComplete="current-password"
              placeholder="Your current password"
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg focus:outline-none focus:ring-2 focus:ring-red-700 text-sm"
            />
            {deleteError && <p className="text-red-400 text-sm">{deleteError}</p>}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={deleteLoading}
                className="text-sm bg-red-800 hover:bg-red-700 disabled:opacity-50 text-white px-4 py-2 rounded transition-colors"
              >
                {deleteLoading ? 'Deleting…' : 'Delete account permanently'}
              </button>
              <button
                type="button"
                onClick={() => { setDeleteStep('idle'); setDeletePassword(''); setDeleteError(null) }}
                className="text-sm bg-surface-hi hover:bg-edge text-fg-2 px-4 py-2 rounded border border-edge transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        )}
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
