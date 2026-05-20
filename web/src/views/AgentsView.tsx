import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, ClipboardList, Mail, RefreshCw, Server, Trash2 } from 'lucide-react'
import { fetchAgents, revokeAgent, startGmailOAuth, createAgent, fetchAgentAuditLog } from '../api/client'
import type { AgentResponse, AgentCreateResponse } from '../api/client'
import type { AuditEntry } from '../types'

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

// ---------------------------------------------------------------------------
// Per-agent audit panel
// ---------------------------------------------------------------------------

const AUDIT_EVENT_COLORS: Record<string, string> = {
  staged:           'text-blue-400',
  approved:         'text-emerald-400',
  executed:         'text-emerald-300',
  rejected:         'text-red-400',
  execution_failed: 'text-orange-400',
  expired:          'text-slate-400',
  auto_rule:        'text-violet-400',
}

function AgentAuditPanel({ agentId }: { agentId: number }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-audit', agentId],
    queryFn: () => fetchAgentAuditLog(agentId, { limit: 10 }),
  })

  if (isLoading) {
    return <p className="text-slate-400 text-xs py-2">Loading audit…</p>
  }
  if (isError) {
    return <p className="text-red-400 text-xs py-2">Failed to load audit log.</p>
  }
  const entries: AuditEntry[] = data?.entries ?? []
  if (entries.length === 0) {
    return <p className="text-slate-500 text-xs py-2 italic">No audit entries yet.</p>
  }

  return (
    <div className="mt-2 space-y-1">
      {entries.map((entry) => (
        <div key={entry.id} className="flex items-center gap-2 text-xs">
          <span className="text-slate-500 shrink-0">
            {new Date(entry.timestamp * 1000).toLocaleString(undefined, {
              month: 'short', day: 'numeric',
              hour: '2-digit', minute: '2-digit',
            })}
          </span>
          <span className={`font-medium shrink-0 ${AUDIT_EVENT_COLORS[entry.event] ?? 'text-slate-300'}`}>
            {entry.event.replace('_', ' ')}
          </span>
          {entry.op_type && (
            <span className="text-slate-500 shrink-0">{entry.op_type}</span>
          )}
          {entry.op_description && (
            <span className="text-slate-400 truncate">{entry.op_description}</span>
          )}
        </div>
      ))}
      {(data?.total ?? 0) > 10 && (
        <p className="text-slate-500 text-xs pt-1">
          Showing 10 of {data!.total} entries — use the Audit view for the full log.
        </p>
      )}
    </div>
  )
}

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
  const [showAudit, setShowAudit] = useState(false)
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
        <div className="flex items-center gap-1.5">
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${
            agent.last_activity_at ? 'bg-emerald-400' : 'bg-slate-600'
          }`} />
          <span className={agent.last_activity_at ? 'text-emerald-400' : 'text-slate-500'}>
            {agent.last_activity_at
              ? `Last active ${formatDate(agent.last_activity_at)}`
              : 'Never connected'}
          </span>
        </div>
      </div>

      {/* Audit tab */}
      <div className="border-t border-slate-700 pt-2">
        <button
          onClick={() => setShowAudit((v) => !v)}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          <ClipboardList className="w-3.5 h-3.5" />
          {showAudit ? 'Hide audit' : 'Recent audit'}
        </button>
        {showAudit && <AgentAuditPanel agentId={agent.id} />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Connect Gmail inline widget
// ---------------------------------------------------------------------------

function ConnectGmailButton() {
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleConnect() {
    setError(null)
    setLoading(true)
    try {
      const res = await startGmailOAuth(label.trim() || undefined)
      window.location.href = res.auth_url
    } catch (err) {
      setLoading(false)
      const msg = err instanceof Error ? err.message : String(err)
      if (msg.includes('oauth2_not_configured')) {
        setError('Gmail OAuth2 is not configured on this server.')
      } else {
        setError(msg)
      }
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-3 py-1.5 rounded transition-colors"
      >
        <Mail className="w-3.5 h-3.5" />
        Connect Gmail
      </button>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <input
        autoFocus
        type="text"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void handleConnect() }}
        placeholder="Label (optional)"
        disabled={loading}
        className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100 w-36 focus:outline-none focus:ring-1 focus:ring-red-500 disabled:opacity-50"
      />
      <button
        onClick={() => void handleConnect()}
        disabled={loading}
        className="flex items-center gap-1 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white text-sm font-medium px-3 py-1.5 rounded transition-colors"
      >
        <Mail className="w-3.5 h-3.5" />
        {loading ? 'Redirecting…' : 'Connect'}
      </button>
      <button
        onClick={() => { setOpen(false); setLabel(''); setError(null) }}
        disabled={loading}
        className="text-slate-400 hover:text-slate-200 text-sm disabled:opacity-50"
      >
        Cancel
      </button>
      {error && <p className="text-red-400 text-xs">{error}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Add IMAP / App Password inline widget
// ---------------------------------------------------------------------------

function AddImapAgentWidget({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('')
  const [upstreamHost, setUpstreamHost] = useState('')
  const [imapPort, setImapPort] = useState(993)
  const [smtpPort, setSmtpPort] = useState(587)
  const [upstreamUser, setUpstreamUser] = useState('')
  const [upstreamPassword, setUpstreamPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AgentCreateResponse | null>(null)
  const [copied, setCopied] = useState(false)

  function mapConnectionError(message: string): string {
    if (message.includes('imap_auth_failed')) return 'Wrong password — for Gmail or iCloud, use an app-specific password.'
    if (message.includes('imap_connection_failed')) return 'Could not reach the IMAP server. Check the host and IMAP port.'
    if (message.includes('imap_ssl_error')) return 'SSL/TLS error. Check that you are using the correct IMAP SSL port.'
    if (message.includes('imap_timeout')) return 'Connection timed out while verifying. Please try again.'
    return message
  }

  function reset() {
    setOpen(false)
    setLabel('')
    setUpstreamHost('')
    setImapPort(993)
    setSmtpPort(587)
    setUpstreamUser('')
    setUpstreamPassword('')
    setError(null)
    setResult(null)
    setCopied(false)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await createAgent({
        label: label.trim() || undefined,
        upstream_host: upstreamHost,
        upstream_imap_port: imapPort,
        upstream_smtp_port: smtpPort,
        upstream_user: upstreamUser,
        upstream_password: upstreamPassword,
      })
      setResult(data)
      onCreated()
    } catch (err) {
      setError(err instanceof Error ? mapConnectionError(err.message) : 'Failed to create agent')
    } finally {
      setLoading(false)
    }
  }

  const proxyHost = import.meta.env.VITE_PROXY_HOST ?? 'test.nuvrail.com'

  function buildConfigSnippet(): string {
    if (!result) return ''

    const host = result.upstream_host?.toLowerCase() ?? ''
    const isGmail = host.includes('gmail')
    const isOutlook = host.includes('outlook') || host.includes('office365') || host.includes('hotmail')
    const isICloud = host.includes('icloud') || host.includes('me.com') || host.includes('mac.com')

    const providerHints: string[] = []
    if (isGmail) {
      providerHints.push(
        ``,
        `# GMAIL-SPECIFIC OPERATIONS`,
        `# Archive a message : UID MOVE <uid> [Gmail]/All Mail`,
        `# Trash a message   : UID MOVE <uid> [Gmail]/Trash`,
        `# Sent folder       : [Gmail]/Sent Mail`,
        `# Drafts folder     : [Gmail]/Drafts`,
        `# Do NOT use COPY + STORE \\Deleted to move messages.`,
        `# Use UID MOVE directly — it stages a single clean operation.`,
      )
    } else if (isOutlook) {
      providerHints.push(
        ``,
        `# OUTLOOK / OFFICE 365 OPERATIONS`,
        `# Archive a message : UID MOVE <uid> Archive`,
        `# Trash a message   : UID MOVE <uid> Deleted Items`,
        `# Sent folder       : Sent Items`,
        `# Junk folder       : Junk Email`,
        `# Use UID MOVE directly; do not use COPY + STORE \\Deleted.`,
      )
    } else if (isICloud) {
      providerHints.push(
        ``,
        `# ICLOUD MAIL OPERATIONS`,
        `# Archive a message : UID MOVE <uid> Archive`,
        `# Trash a message   : UID MOVE <uid> Deleted Messages`,
        `# Sent folder       : Sent Messages`,
        `# Use UID MOVE directly; do not use COPY + STORE \\Deleted.`,
      )
    }

    return [
      `# Nuvrail proxy — connection and operating instructions`,
      `#`,
      `# CONNECTION`,
      `IMAP  ${proxyHost}:993  (SSL/TLS)`,
      `SMTP  ${proxyHost}:465  (SSL/TLS)`,
      `User  ${result.agent_username}`,
      `Pass  ${result.agent_token}`,
      ``,
      `# HOW THIS PROXY WORKS`,
      `# Every write operation (flag changes, moves, copies, email sends) is`,
      `# staged for human approval before it executes on the real mail server.`,
      `# When you receive OK [STAGED], the operation is queued — not yet done.`,
      `# Wait for explicit confirmation before treating it as complete.`,
      `#`,
      `# REQUIRED — always use UID-prefixed commands for all write operations:`,
      `#   UID STORE, UID MOVE, UID COPY`,
      `# Non-UID write commands are rejected with NO [CLIENTBUG].`,
      `#`,
      `# EXPUNGE is permanently blocked by the proxy.`,
      `# To delete a message, use UID MOVE <uid> <Trash folder> instead.`,
      ...providerHints,
    ].join('\n')
  }

  async function copyToken() {
    if (!result) return
    await navigator.clipboard.writeText(result.agent_token)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  async function copySnippet() {
    await navigator.clipboard.writeText(buildConfigSnippet())
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-3 py-1.5 rounded transition-colors"
      >
        <Server className="w-3.5 h-3.5" />
        IMAP / App Password
      </button>
    )
  }

  if (result) {
    return (
      <div className="bg-slate-800 border border-slate-600 rounded-lg p-5 space-y-4">
        <h2 className="text-base font-semibold text-slate-200">Agent credentials generated</h2>
        <div className="bg-slate-700 border border-amber-500 rounded-lg p-4 font-mono text-sm space-y-2">
          <p className="text-amber-400 font-semibold mb-3">⚠ Shown once — save these now</p>
          <div className="text-slate-300"><span className="text-slate-500">IMAP server: </span>{proxyHost}</div>
          <div className="text-slate-300"><span className="text-slate-500">IMAP port:   </span>993 <span className="text-slate-500 text-xs">(SSL/TLS)</span></div>
          <div className="text-slate-300"><span className="text-slate-500">SMTP server: </span>{proxyHost}</div>
          <div className="text-slate-300"><span className="text-slate-500">SMTP port:   </span>465 <span className="text-slate-500 text-xs">(SSL/TLS)</span></div>
          <div className="text-slate-300"><span className="text-slate-500">Username:    </span><span className="text-indigo-300">{result.agent_username}</span></div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500">Password:    </span>
            <span className="text-indigo-300 break-all">{result.agent_token}</span>
            <button
              onClick={() => void copyToken()}
              className="ml-2 shrink-0 bg-slate-600 hover:bg-slate-500 text-slate-200 text-xs px-2 py-1 rounded"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
        <div className="rounded-md bg-slate-900/60 border border-slate-600 p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-slate-400">Config snippet — paste into your IMAP/SMTP client</span>
            <button
              onClick={() => void copySnippet()}
              className="text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 px-2 py-1 rounded"
            >
              {copied ? 'Copied!' : 'Copy all'}
            </button>
          </div>
          <pre className="text-xs text-slate-300 whitespace-pre overflow-x-auto">{buildConfigSnippet()}</pre>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => { setResult(null); setLabel(''); setUpstreamHost(''); setUpstreamUser(''); setUpstreamPassword('') }}
            className="bg-slate-600 hover:bg-slate-500 text-slate-200 text-sm font-medium py-2 px-4 rounded"
          >
            Add another
          </button>
          <button onClick={reset} className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium py-2 px-4 rounded">
            Done
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-slate-800 border border-slate-600 rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-slate-200">Connect via IMAP / App Password</h2>
        <button onClick={reset} className="text-slate-400 hover:text-slate-200 text-sm">Cancel</button>
      </div>
      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            Label <span className="text-slate-500">(optional — e.g. Work, iCloud)</span>
          </label>
          <input type="text" value={label} onChange={(e) => setLabel(e.target.value)}
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="Work" />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Upstream IMAP host</label>
          <input type="text" value={upstreamHost} onChange={(e) => setUpstreamHost(e.target.value)} required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="imap.mail.me.com" />
        </div>
        <div className="flex gap-4">
          <div className="flex-1">
            <label className="block text-sm font-medium text-slate-300 mb-1">IMAP port</label>
            <input type="number" value={imapPort} onChange={(e) => setImapPort(Number(e.target.value))}
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          </div>
          <div className="flex-1">
            <label className="block text-sm font-medium text-slate-300 mb-1">SMTP port</label>
            <input type="number" value={smtpPort} onChange={(e) => setSmtpPort(Number(e.target.value))}
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">Email address</label>
          <input type="email" value={upstreamUser} onChange={(e) => setUpstreamUser(e.target.value)} required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="you@example.com" />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1">
            App password <span className="text-slate-500">(IMAP/SMTP)</span>
          </label>
          <input type="password" value={upstreamPassword} onChange={(e) => setUpstreamPassword(e.target.value)} required
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500" />
        </div>
        {error && <p className="text-red-400 text-sm">{error}</p>}
        <button type="submit" disabled={loading}
          className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-medium py-2 px-4 rounded transition-colors">
          {loading ? 'Verifying connection…' : 'Connect'}
        </button>
      </form>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AgentsView
// ---------------------------------------------------------------------------

export default function AgentsView() {
  const qc = useQueryClient()

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
          <ConnectGmailButton />
          <AddImapAgentWidget onCreated={() => qc.invalidateQueries({ queryKey: ['agents'] })} />
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <p className="text-slate-400 text-sm">Loading agents…</p>
      )}

      {/* Error */}
      {isError && (
        <p className="text-red-400 text-sm">Failed to load agents.</p>
      )}

      {/* Empty state */}
      {!isLoading && !isError && agents.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
          <Bot className="w-10 h-10 opacity-40" />
          <p className="text-sm">No agents yet — use the buttons above to connect your first email account.</p>
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
