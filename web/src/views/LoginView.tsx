import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { loginUser, setToken } from '../api/client'

export default function LoginView() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await loginUser(email, password)
      setToken(data.token)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-md mx-auto mt-16">
      <div className="bg-surface rounded-lg p-8 border border-edge">
        <h1 className="text-2xl font-display font-black text-fg mb-6">Sign in to Nuvrail</h1>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="you@example.com"
              autoComplete="email"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-fg-2 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full bg-surface-hi border border-edge rounded px-3 py-2 text-fg focus:outline-none focus:ring-2 focus:ring-accent"
              autoComplete="current-password"
            />
          </div>

          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-accent hover:bg-accent-hi disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-fg-3">
          No account?{' '}
          <a
            href="#/setup"
            className="text-accent hover:text-accent underline"
          >
            Create one
          </a>
        </p>
      </div>
    </div>
  )
}
