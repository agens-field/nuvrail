import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { registerUser, setToken } from '../api/client'

export default function SetupView() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    if (password.length < 12) {
      setError('Password must be at least 12 characters.')
      return
    }

    setLoading(true)
    try {
      const data = await registerUser({ email, display_name: displayName, password })
      if (data.token) setToken(data.token)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-md mx-auto mt-16">
      <div className="bg-surface rounded-lg p-8 border border-edge">
        <h1 className="text-2xl font-display font-black text-fg mb-1">Create your account</h1>
        <p className="text-sm text-fg-3 mb-6">Set up Nuvrail for the first time.</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg placeholder-fg-3 focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="you@example.com"
              autoComplete="email"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">
              Display name <span className="text-fg-3 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg placeholder-fg-3 focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="Your name"
              autoComplete="name"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg placeholder-fg-3 focus:outline-none focus:ring-2 focus:ring-accent"
              autoComplete="new-password"
            />
            <p className="text-xs text-fg-3 mt-1">Minimum 12 characters.</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">Confirm password</label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg placeholder-fg-3 focus:outline-none focus:ring-2 focus:ring-accent"
              autoComplete="new-password"
            />
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-accent hover:bg-accent-hi disabled:opacity-50 text-white dark:text-[#111c27] font-semibold py-2 px-4 rounded transition-colors"
          >
            {loading ? 'Creating account…' : 'Create account'}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-fg-3">
          Already have an account?{' '}
          <a href="#/login" className="text-accent hover:underline">
            Sign in
          </a>
        </p>
      </div>
    </div>
  )
}
