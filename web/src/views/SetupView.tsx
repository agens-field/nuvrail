/**
 * SetupView — two-step account + agent credential setup.
 *
 * Step 1 (if no token in localStorage):
 *   Create account → auto-login → store token
 *
 * Step 2 (after login):
 *   Register upstream email account → generate agent credentials
 *   Show agent_token ONE TIME in a copy box
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { registerUser, loginUser, createAgent, setToken, getToken } from '../api/client'
import type { AgentCreateResponse } from '../api/client'

// ---------------------------------------------------------------------------
// Step 1 — Account creation
// ---------------------------------------------------------------------------

function AccountStep({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await registerUser({ email, password, display_name: displayName || undefined })
      // Auto-login immediately after registration
      const login = await loginUser(email, password)
      setToken(login.token)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-200 mb-2">Step 1 — Create your Nuvrail account</h2>
      <p className="text-slate-400 text-sm mb-4">
        This is your <strong className="text-slate-200">Nuvrail login</strong> — not your email account credentials.
        You'll connect your actual email in the next step.
      </p>
      <form onSubmit={handleCreate} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Your Nuvrail email address</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="you@example.com"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Choose a Nuvrail password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            Display name <span className="text-slate-500">(optional)</span>
          </label>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
        >
          {loading ? 'Creating account…' : 'Create Account'}
        </button>
      </form>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 2 — Connect email account
// ---------------------------------------------------------------------------

function AgentStep() {
  const navigate = useNavigate()
  const [upstreamHost, setUpstreamHost] = useState('')
  const [imapPort, setImapPort] = useState(993)
  const [smtpPort, setSmtpPort] = useState(587)
  const [upstreamUser, setUpstreamUser] = useState('')
  const [upstreamPassword, setUpstreamPassword] = useState('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AgentCreateResponse | null>(null)
  const [copied, setCopied] = useState(false)

  function mapConnectionError(message: string): string {
    if (message.includes('imap_auth_failed')) {
      return 'Wrong password — for Gmail or iCloud, use an app-specific password.'
    }
    if (message.includes('imap_connection_failed')) {
      return 'Could not reach the IMAP server. Check the host and IMAP port.'
    }
    if (message.includes('imap_ssl_error')) {
      return 'SSL/TLS error. Check that you are using the correct IMAP SSL port.'
    }
    if (message.includes('imap_timeout')) {
      return 'Connection timed out while verifying. Please try again.'
    }
    return message
  }

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await createAgent({
        label: label || undefined,
        upstream_host: upstreamHost,
        upstream_imap_port: imapPort,
        upstream_smtp_port: smtpPort,
        upstream_user: upstreamUser,
        upstream_password: upstreamPassword,
      })
      setResult(data)
    } catch (err) {
      setError(
        err instanceof Error ? mapConnectionError(err.message) : 'Failed to generate credentials'
      )
    } finally {
      setLoading(false)
    }
  }

  async function copyToken() {
    if (!result) return
    await navigator.clipboard.writeText(result.agent_token)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (result) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-200">Agent credentials generated</h2>
        <div className="bg-slate-700 border border-amber-500 rounded-lg p-4 font-mono text-sm space-y-2">
          <p className="text-amber-400 font-semibold mb-3">⚠ Shown once — save these now</p>
          <div className="text-slate-300">
            <span className="text-slate-500">IMAP server: </span>{import.meta.env.VITE_PROXY_HOST ?? 'test.nuvrail.com'}
          </div>
          <div className="text-slate-300">
            <span className="text-slate-500">IMAP port:   </span>10143
          </div>
          <div className="text-slate-300">
            <span className="text-slate-500">SMTP port:   </span>10587
          </div>
          <div className="text-slate-300">
            <span className="text-slate-500">Username:    </span>
            <span className="text-indigo-300">{result.agent_username}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500">Password:    </span>
            <span className="text-indigo-300 break-all">{result.agent_token}</span>
            <button
              onClick={copyToken}
              className="ml-2 shrink-0 bg-slate-600 hover:bg-slate-500 text-slate-200 text-xs px-2 py-1 rounded"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => setResult(null)}
            className="bg-slate-600 hover:bg-slate-500 text-slate-200 font-medium py-2 px-4 rounded"
          >
            Add another account
          </button>
          <button
            onClick={() => navigate('/')}
            className="bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 px-4 rounded"
          >
            Done
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-200 mb-2">Step 2 — Connect your email account</h2>
      <p className="text-slate-400 text-sm mb-4">
        Now enter your <strong className="text-slate-200">actual email account credentials</strong> —
        the IMAP/SMTP server and login for the mailbox your AI agent will access.
        These are stored encrypted and used only to connect to your mail server.
      </p>
      <form onSubmit={handleGenerate} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            Label <span className="text-slate-500">(optional — e.g. Gmail, Work)</span>
          </label>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="Gmail"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Upstream IMAP host</label>
          <input
            type="text"
            value={upstreamHost}
            onChange={(e) => setUpstreamHost(e.target.value)}
            required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="imap.gmail.com"
          />
        </div>
        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-sm font-medium text-slate-300 mb-1">IMAP port</label>
            <input
              type="number"
              value={imapPort}
              onChange={(e) => setImapPort(Number(e.target.value))}
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div className="flex-1">
            <label className="block text-sm font-medium text-slate-300 mb-1">SMTP port</label>
            <input
              type="number"
              value={smtpPort}
              onChange={(e) => setSmtpPort(Number(e.target.value))}
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Email address</label>
          <input
            type="email"
            value={upstreamUser}
            onChange={(e) => setUpstreamUser(e.target.value)}
            required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="you@gmail.com"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            App password <span className="text-slate-500">(IMAP/SMTP)</span>
          </label>
          <input
            type="password"
            value={upstreamPassword}
            onChange={(e) => setUpstreamPassword(e.target.value)}
            required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors"
        >
          {loading ? 'Verifying connection…' : 'Generate Agent Credentials'}
        </button>
      </form>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SetupView — orchestrates both steps
// ---------------------------------------------------------------------------

export default function SetupView() {
  const hasToken = Boolean(getToken())
  const [step, setStep] = useState<'account' | 'agent'>(hasToken ? 'agent' : 'account')

  return (
    <div className="max-w-lg mx-auto mt-8">
      <div className="bg-slate-800 rounded-lg p-8 border border-slate-700">
        <h1 className="text-2xl font-bold text-indigo-400 mb-6">Setup</h1>

        {/* Step indicator */}
        <div className="flex gap-2 mb-6 text-sm">
          <span className={step === 'account' ? 'text-indigo-400 font-semibold' : 'text-slate-500'}>
            1. Account
          </span>
          <span className="text-slate-600">›</span>
          <span className={step === 'agent' ? 'text-indigo-400 font-semibold' : 'text-slate-500'}>
            2. Connect email
          </span>
        </div>

        {step === 'account' ? (
          <AccountStep onDone={() => setStep('agent')} />
        ) : (
          <AgentStep />
        )}
      </div>
    </div>
  )
}
