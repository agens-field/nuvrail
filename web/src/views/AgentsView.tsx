/**
 * AgentsView — /#/agents
 *
 * Displays active/revoked agent credentials and exposes an "Add agent"
 * wizard that replaced the old ad-hoc ConnectGmailButton + Setup redirect.
 *
 * Wizard flow:
 *
 *   [Add agent] button
 *        │
 *        ▼
 *   Step 1: Label
 *   ("What should I call this account?")
 *        │
 *        ▼
 *   Step 2: Provider selection
 *   ┌──────────┬──────────┬──────────┬───────────────┐
 *   │  Gmail   │  Apple   │ Outlook  │ Standard IMAP │
 *   └──────────┴──────────┴──────────┴───────────────┘
 *        │
 *        ├─ Gmail ─────► OAuth2 redirect (existing flow)
 *        ├─ Apple ─────► Phase 2b placeholder
 *        ├─ Outlook ───► Phase 3 placeholder
 *        └─ Standard ─► Manual IMAP form → credentials shown once
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronLeft, Mail, Plus, RefreshCw, Shield, Trash2 } from 'lucide-react'
import {
  createAgent,
  fetchAgents,
  revokeAgent,
  startGmailOAuth,
} from '../api/client'
import type { AgentCreateRequest, AgentCreateResponse, AgentResponse } from '../api/client'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

// ---------------------------------------------------------------------------
// AgentCard
// ---------------------------------------------------------------------------

function AgentCard({
  agent,
  onDelete,
  deleting,
}: {
  agent: AgentResponse
  onDelete: (id: number) => void
  deleting: boolean
}) {
  const [confirming, setConfirming] = useState(false)
  const isRevoked = agent.revoked_at != null

  return (
    <div
      className={`bg-slate-800 border rounded-lg p-4 flex flex-col gap-2 ${
        isRevoked ? 'border-slate-700 opacity-60' : 'border-slate-600'
      }`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Bot className="w-4 h-4 text-indigo-400 shrink-0" />
          <span className="font-medium text-slate-100 truncate">
            {agent.label || 'Unnamed agent'}
          </span>
          {isRevoked && (
            <span className="text-xs bg-red-900 text-red-300 px-1.5 py-0.5 rounded shrink-0">
              Revoked
            </span>
          )}
        </div>

        {!isRevoked && (
          confirming ? (
            <div className="flex gap-2 shrink-0">
              <button
                onClick={() => { onDelete(agent.id); setConfirming(false) }}
                disabled={deleting}
                className="text-xs bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white px-2 py-1 rounded"
              >
                {deleting ? 'Revoking…' : 'Confirm'}
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="text-xs bg-slate-600 hover:bg-slate-500 text-slate-200 px-2 py-1 rounded"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              className="text-slate-500 hover:text-red-400 shrink-0 transition-colors"
              title="Revoke agent"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )
        )}
      </div>

      {/* Details */}
      <div className="font-mono text-xs text-slate-400 space-y-1">
        <div>
          <span className="text-slate-500">Username: </span>
          <span className="text-indigo-300">{agent.agent_username}</span>
        </div>
        <div>
          <span className="text-slate-500">Upstream: </span>
          {agent.upstream_user}@{agent.upstream_host}
        </div>
        <div>
          <span className="text-slate-500">Created: </span>
          {formatDate(agent.created_at)}
        </div>
        {isRevoked && agent.revoked_at && (
          <div>
            <span className="text-slate-500">Revoked: </span>
            {formatDate(agent.revoked_at)}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CreatedCredentials — one-time display after agent creation
// ---------------------------------------------------------------------------

function CreatedCredentials({
  result,
  label,
  onDone,
}: {
  result: AgentCreateResponse
  label: string
  onDone: () => void
}) {
  const [copied, setCopied] = useState<string | null>(null)

  async function copyToClipboard(text: string, field: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(field)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      // ignore — clipboard access may be blocked
    }
  }

  return (
    <div className="space-y-4">
      <div className="bg-amber-900/40 border border-amber-700 rounded-lg p-4">
        <p className="text-amber-300 text-sm font-medium">
          Save these credentials now — the token is shown only once.
        </p>
      </div>

      <div className="bg-slate-900 rounded-lg border border-slate-700 divide-y divide-slate-700 font-mono text-sm">
        {[
          { label: 'Label', value: label || result.label },
          { label: 'IMAP Username', value: result.agent_username, copyKey: 'username' },
          { label: 'IMAP Password', value: result.agent_token, copyKey: 'token' },
          { label: 'IMAP Host', value: result.upstream_host },
          { label: 'IMAP Port', value: '10143 (proxy)' },
        ].map(({ label: l, value, copyKey }) => (
          <div key={l} className="flex items-center justify-between px-4 py-3 gap-4">
            <span className="text-slate-500 text-xs shrink-0">{l}</span>
            <span className="text-slate-100 truncate">{value}</span>
            {copyKey && (
              <button
                onClick={() => copyToClipboard(value, copyKey)}
                className="shrink-0 text-xs text-indigo-400 hover:text-indigo-300"
              >
                {copied === copyKey ? '✓ Copied' : 'Copy'}
              </button>
            )}
          </div>
        ))}
      </div>

      <button
        onClick={onDone}
        className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 px-4 rounded transition-colors"
      >
        Done
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Add-agent wizard steps
// ---------------------------------------------------------------------------

type WizardStep = 'label' | 'provider' | 'standard-imap' | 'created'
type Provider = 'gmail' | 'apple' | 'outlook' | 'standard'

interface ProviderOption {
  id: Provider
  name: string
  description: string
  phase: string | null  // null = available now; otherwise shows "Coming Phase N"
  icon: React.ReactNode
}

const PROVIDERS: ProviderOption[] = [
  {
    id: 'gmail',
    name: 'Gmail',
    description: 'Google Workspace or gmail.com',
    phase: null,
    icon: <Mail className="w-5 h-5 text-red-400" />,
  },
  {
    id: 'standard',
    name: 'Standard IMAP',
    description: 'Any IMAP/SMTP server',
    phase: null,
    icon: <Bot className="w-5 h-5 text-indigo-400" />,
  },
  {
    id: 'apple',
    name: 'Apple Mail',
    description: 'iCloud / me.com / mac.com',
    phase: 'Phase 2b',
    icon: <Shield className="w-5 h-5 text-slate-400" />,
  },
  {
    id: 'outlook',
    name: 'Outlook',
    description: 'Microsoft 365 / Outlook.com',
    phase: 'Phase 3',
    icon: <Mail className="w-5 h-5 text-slate-400" />,
  },
]

// ---------------------------------------------------------------------------
// AddAgentWizard
// ---------------------------------------------------------------------------

function AddAgentWizard({
  onCancel,
  onCreated,
}: {
  onCancel: () => void
  onCreated: () => void
}) {
  const [step, setStep] = useState<WizardStep>('label')
  const [label, setLabel] = useState('')
  const [provider, setProvider] = useState<Provider | null>(null)

  // Standard IMAP form state
  const [host, setHost] = useState('')
  const [port, setPort] = useState(993)
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [smtpHost, setSmtpHost] = useState('')
  const [smtpPort, setSmtpPort] = useState(587)
  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)
  const [createdResult, setCreatedResult] = useState<AgentCreateResponse | null>(null)

  // Gmail OAuth state
  const [gmailLoading, setGmailLoading] = useState(false)
  const [gmailError, setGmailError] = useState<string | null>(null)

  // ── Label step ────────────────────────────────────────────────────────────
  if (step === 'label') {
    return (
      <div className="bg-slate-800 border border-slate-600 rounded-lg p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-100">Add agent — Step 1 of 2</h2>
          <button
            onClick={onCancel}
            className="text-slate-500 hover:text-slate-300 text-sm"
          >
            Cancel
          </button>
        </div>

        <p className="text-sm text-slate-400">
          Give this email account a name. You'll see it in the agents list.
        </p>

        <input
          autoFocus
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && label.trim()) setStep('provider') }}
          placeholder="e.g. Work Gmail, AI Agent"
          className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />

        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="text-sm text-slate-400 hover:text-slate-200 px-3 py-1.5"
          >
            Cancel
          </button>
          <button
            onClick={() => setStep('provider')}
            disabled={!label.trim()}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded transition-colors"
          >
            Next →
          </button>
        </div>
      </div>
    )
  }

  // ── Provider selection step ───────────────────────────────────────────────
  if (step === 'provider') {
    return (
      <div className="bg-slate-800 border border-slate-600 rounded-lg p-5 space-y-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setStep('label')}
            className="text-slate-400 hover:text-slate-200"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <h2 className="font-semibold text-slate-100">
            Add agent — Step 2 of 2
          </h2>
          <button
            onClick={onCancel}
            className="ml-auto text-slate-500 hover:text-slate-300 text-sm"
          >
            Cancel
          </button>
        </div>

        <p className="text-sm text-slate-400">
          Choose your email provider for <span className="text-slate-200 font-medium">"{label}"</span>.
        </p>

        <div className="grid grid-cols-2 gap-3">
          {PROVIDERS.map((p) => {
            const available = p.phase === null
            return (
              <button
                key={p.id}
                disabled={!available}
                onClick={() => {
                  if (!available) return
                  setProvider(p.id)
                  if (p.id === 'gmail') {
                    setGmailError(null)
                    setGmailLoading(true)
                    startGmailOAuth(label.trim() || undefined)
                      .then((res) => { window.location.href = res.auth_url })
                      .catch((err) => {
                        setGmailLoading(false)
                        const msg = err instanceof Error ? err.message : String(err)
                        setGmailError(
                          msg.includes('oauth2_not_configured')
                            ? 'Gmail OAuth2 is not configured on this server.'
                            : msg
                        )
                      })
                  } else if (p.id === 'standard') {
                    setStep('standard-imap')
                  }
                }}
                className={`
                  flex flex-col gap-2 items-start p-4 rounded-lg border text-left transition-colors
                  ${available
                    ? 'border-slate-600 hover:border-indigo-500 hover:bg-slate-700 cursor-pointer'
                    : 'border-slate-700 opacity-50 cursor-not-allowed'
                  }
                  ${provider === p.id && gmailLoading ? 'border-indigo-500 bg-slate-700' : ''}
                `}
              >
                <div className="flex items-center gap-2">
                  {p.icon}
                  <span className="text-sm font-medium text-slate-100">{p.name}</span>
                </div>
                <span className="text-xs text-slate-400">{p.description}</span>
                {p.phase && (
                  <span className="text-xs bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded">
                    {p.phase}
                  </span>
                )}
                {provider === p.id && gmailLoading && (
                  <span className="text-xs text-indigo-300">Redirecting…</span>
                )}
              </button>
            )
          })}
        </div>

        {gmailError && (
          <p className="text-red-400 text-sm">{gmailError}</p>
        )}
      </div>
    )
  }

  // ── Standard IMAP form ────────────────────────────────────────────────────
  if (step === 'standard-imap') {
    async function handleStandardSubmit(e: React.FormEvent) {
      e.preventDefault()
      setFormError(null)
      setFormLoading(true)

      const body: AgentCreateRequest = {
        label: label.trim() || undefined,
        upstream_host: host.trim(),
        upstream_imap_port: port,
        upstream_smtp_port: smtpPort,
        upstream_user: user.trim(),
        upstream_password: password,
        ...(smtpHost.trim() ? { upstream_smtp_host: smtpHost.trim() } : {}),
      }

      try {
        const result = await createAgent(body)
        setCreatedResult(result)
        setStep('created')
      } catch (err) {
        setFormError(err instanceof Error ? err.message : 'Failed to create agent.')
      } finally {
        setFormLoading(false)
      }
    }

    return (
      <div className="bg-slate-800 border border-slate-600 rounded-lg p-5 space-y-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setStep('provider')}
            className="text-slate-400 hover:text-slate-200"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <h2 className="font-semibold text-slate-100">Standard IMAP — {label}</h2>
          <button
            onClick={onCancel}
            className="ml-auto text-slate-500 hover:text-slate-300 text-sm"
          >
            Cancel
          </button>
        </div>

        <form onSubmit={handleStandardSubmit} className="space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-slate-400 mb-1">
                IMAP host
              </label>
              <input
                type="text"
                value={host}
                onChange={(e) => setHost(e.target.value)}
                required
                placeholder="imap.example.com"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                IMAP port
              </label>
              <input
                type="number"
                value={port}
                onChange={(e) => setPort(Number(e.target.value))}
                required
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-slate-400 mb-1">
                SMTP host <span className="text-slate-600">(leave blank to use IMAP host)</span>
              </label>
              <input
                type="text"
                value={smtpHost}
                onChange={(e) => setSmtpHost(e.target.value)}
                placeholder="smtp.example.com"
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                SMTP port
              </label>
              <input
                type="number"
                value={smtpPort}
                onChange={(e) => setSmtpPort(Number(e.target.value))}
                required
                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              Email address (upstream username)
            </label>
            <input
              type="email"
              value={user}
              onChange={(e) => setUser(e.target.value)}
              required
              placeholder="you@example.com"
              autoComplete="username"
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">
              Password (or app-specific password)
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          {formError && <p className="text-red-400 text-sm">{formError}</p>}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => setStep('provider')}
              className="text-sm text-slate-400 hover:text-slate-200 px-3 py-1.5"
            >
              Back
            </button>
            <button
              type="submit"
              disabled={formLoading}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium px-4 py-1.5 rounded transition-colors"
            >
              {formLoading ? 'Connecting…' : 'Connect'}
            </button>
          </div>
        </form>
      </div>
    )
  }

  // ── Credentials shown once ────────────────────────────────────────────────
  if (step === 'created' && createdResult) {
    return (
      <div className="bg-slate-800 border border-slate-600 rounded-lg p-5 space-y-4">
        <h2 className="font-semibold text-slate-100">Agent created — save credentials</h2>
        <CreatedCredentials
          result={createdResult}
          label={label}
          onDone={() => {
            onCreated()
          }}
        />
      </div>
    )
  }

  return null
}

// ---------------------------------------------------------------------------
// AgentsView
// ---------------------------------------------------------------------------

export default function AgentsView() {
  const qc = useQueryClient()
  const [showWizard, setShowWizard] = useState(false)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
  })

  const deleteMutation = useMutation({
    mutationFn: revokeAgent,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  const agents = data ?? []
  const active = agents.filter((a) => a.revoked_at == null)
  const revoked = agents.filter((a) => a.revoked_at != null)

  function handleCreated() {
    setShowWizard(false)
    void qc.invalidateQueries({ queryKey: ['agents'] })
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Agents</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            className="text-slate-400 hover:text-slate-200 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          {!showWizard && (
            <button
              onClick={() => setShowWizard(true)}
              className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-3 py-1.5 rounded transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              Add agent
            </button>
          )}
        </div>
      </div>

      {/* Wizard (inline expansion) */}
      {showWizard && (
        <AddAgentWizard
          onCancel={() => setShowWizard(false)}
          onCreated={handleCreated}
        />
      )}

      {/* Loading */}
      {isLoading && <p className="text-slate-400 text-sm">Loading agents…</p>}

      {/* Error */}
      {isError && <p className="text-red-400 text-sm">Failed to load agents.</p>}

      {/* Empty state */}
      {!isLoading && !isError && agents.length === 0 && !showWizard && (
        <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
          <Bot className="w-10 h-10 opacity-40" />
          <p className="text-sm">No agents yet.</p>
          <button
            onClick={() => setShowWizard(true)}
            className="text-indigo-400 hover:text-indigo-300 text-sm underline"
          >
            Connect your first email account →
          </button>
        </div>
      )}

      {/* Active agents */}
      {active.length > 0 && (
        <div className="space-y-3">
          {active.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              onDelete={(id) => deleteMutation.mutate(id)}
              deleting={deleteMutation.isPending && deleteMutation.variables === agent.id}
            />
          ))}
        </div>
      )}

      {/* Revoked agents (collapsed section) */}
      {revoked.length > 0 && (
        <details className="mt-4">
          <summary className="text-slate-500 text-sm cursor-pointer hover:text-slate-300 select-none">
            {revoked.length} revoked agent{revoked.length !== 1 ? 's' : ''}
          </summary>
          <div className="mt-3 space-y-3">
            {revoked.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                onDelete={(id) => deleteMutation.mutate(id)}
                deleting={false}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
