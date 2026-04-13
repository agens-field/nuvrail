import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, Trash2, Plus, RefreshCw } from 'lucide-react'
import { fetchAgents, revokeAgent } from '../api/client'
import type { AgentResponse } from '../api/client'
import { useNavigate } from 'react-router-dom'

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
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

export default function AgentsView() {
  const navigate = useNavigate()
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
        <div className="flex gap-2">
          <button
            onClick={() => refetch()}
            className="text-slate-400 hover:text-slate-200 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => navigate('/setup')}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-3 py-1.5 rounded transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            Add agent
          </button>
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
          <p className="text-sm">No agents yet.</p>
          <button
            onClick={() => navigate('/setup')}
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
