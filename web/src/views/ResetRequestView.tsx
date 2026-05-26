/**
 * ResetRequestView — /#/reset-request
 *
 * "Forgot password?" entry point. Submits the email and shows a confirmation.
 * The server logs the reset URL (Phase 2a); in production it emails it.
 */
import { useState } from 'react'
import { requestPasswordReset } from '../api/client'

export default function ResetRequestView() {
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await requestPasswordReset(email)
      setSubmitted(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed. Try again.')
    } finally {
      setLoading(false)
    }
  }

  if (submitted) {
    return (
      <div className="max-w-md mx-auto mt-16">
        <div className="bg-surface rounded-lg p-8 border border-edge text-center space-y-4">
          <p className="text-2xl">✉️</p>
          <h1 className="text-xl font-bold text-fg">Check your inbox</h1>
          <p className="text-fg-3 text-sm">
            If <span className="text-fg-2">{email}</span> is registered, a reset
            link has been sent. It expires in 1 hour.
          </p>
          <p className="text-fg-3 text-xs">
            (Phase 2a: the link is logged on the server. Check the container logs if
            email delivery is not configured.)
          </p>
          <a
            href="#/login"
            className="block text-accent hover:text-accent underline text-sm"
          >
            Back to sign in
          </a>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-md mx-auto mt-16">
      <div className="bg-surface rounded-lg p-8 border border-edge">
        <h1 className="text-2xl font-display font-black text-fg mb-2">Reset password</h1>
        <p className="text-fg-3 text-sm mb-6">
          Enter your email address and we'll send you a reset link.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="you@example.com"
            />
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-accent hover:bg-accent-hi disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
          >
            {loading ? 'Sending…' : 'Send reset link'}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-fg-3">
          <a href="#/login" className="text-accent hover:text-accent underline">
            Back to sign in
          </a>
        </p>
      </div>
    </div>
  )
}
