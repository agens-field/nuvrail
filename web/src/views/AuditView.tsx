import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { formatDistanceToNow, format, fromUnixTime } from 'date-fns'
import { Download, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { fetchAuditLog, exportAuditLog } from '../api/client'
import type { AuditEntry } from '../types'

const PAGE_SIZE = 20

const EVENT_COLORS: Record<string, string> = {
  staged:            'bg-blue-900 text-blue-200',
  approved:          'bg-emerald-900 text-emerald-200',
  executed:          'bg-emerald-700 text-emerald-100',
  rejected:          'bg-red-900 text-red-200',
  execution_failed:  'bg-orange-900 text-orange-200',
  expired:           'bg-slate-700 text-slate-300',
}

const ACTOR_LABELS: Record<string, string> = {
  ai_agent: 'AI',
  human:    'Human',
  system:   'System',
}

const EVENT_FILTERS = [
  { label: 'All',      value: undefined },
  { label: 'Staged',   value: 'staged' },
  { label: 'Approved', value: 'approved' },
  { label: 'Executed', value: 'executed' },
  { label: 'Rejected', value: 'rejected' },
  { label: 'Failed',   value: 'execution_failed' },
]

function EventPill({ event }: { event: string }) {
  const cls = EVENT_COLORS[event] ?? 'bg-slate-700 text-slate-300'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {event.replace('_', ' ')}
    </span>
  )
}

function ProtocolBadge({ protocol }: { protocol?: string }) {
  if (!protocol) return null
  const isSmtp = protocol === 'smtp'
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded text-xs font-bold uppercase tracking-wide ${
        isSmtp ? 'bg-orange-900 text-orange-200' : 'bg-blue-900 text-blue-200'
      }`}
    >
      {protocol}
    </span>
  )
}

function RelativeTime({ ts }: { ts: number }) {
  const date = fromUnixTime(ts)
  return (
    <span title={format(date, 'yyyy-MM-dd HH:mm:ss')} className="cursor-help">
      {formatDistanceToNow(date, { addSuffix: true })}
    </span>
  )
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const [expanded, setExpanded] = useState(false)

  const hasDetail = !!(entry.detail || entry.op_description || entry.op_type)

  return (
    <>
      <tr
        className={`border-b border-slate-700/50 hover:bg-slate-800/50 transition-colors ${
          hasDetail ? 'cursor-pointer' : ''
        }`}
        onClick={() => hasDetail && setExpanded(e => !e)}
      >
        {/* Expand toggle */}
        <td className="px-3 py-3 w-6 text-slate-500">
          {hasDetail && (
            expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />
          )}
        </td>

        {/* Timestamp */}
        <td className="px-3 py-3 text-sm text-slate-400 whitespace-nowrap">
          <RelativeTime ts={entry.timestamp} />
        </td>

        {/* Protocol + event */}
        <td className="px-3 py-3">
          <div className="flex items-center gap-2 flex-wrap">
            <ProtocolBadge protocol={entry.op_protocol ?? undefined} />
            <EventPill event={entry.event} />
          </div>
        </td>

        {/* Description */}
        <td className="px-3 py-3 text-sm text-slate-200">
          {entry.op_description ?? (
            <span className="text-slate-500 italic">System event</span>
          )}
        </td>

        {/* Actor */}
        <td className="px-3 py-3 text-sm text-slate-400 whitespace-nowrap">
          {ACTOR_LABELS[entry.actor ?? ''] ?? entry.actor ?? '—'}
        </td>
      </tr>

      {/* Expanded detail row */}
      {expanded && (
        <tr className="bg-slate-800/70 border-b border-slate-700/50">
          <td colSpan={5} className="px-6 py-4">
            <div className="space-y-2 text-sm">
              {entry.operation_id && (
                <div>
                  <span className="text-slate-400">Operation ID: </span>
                  <code className="text-slate-300 font-mono text-xs">{entry.operation_id}</code>
                  {entry.op_status && (
                    <span className="ml-2 text-slate-400">
                      · Status: <span className="text-slate-200">{entry.op_status}</span>
                    </span>
                  )}
                </div>
              )}
              {entry.op_type && (
                <div>
                  <span className="text-slate-400">Op type: </span>
                  <span className="text-slate-200">{entry.op_type}</span>
                </div>
              )}
              {entry.detail && (
                <div>
                  <span className="text-slate-400">Detail: </span>
                  <pre className="mt-1 p-2 bg-slate-900 rounded text-xs text-slate-300 overflow-auto max-h-32">
                    {JSON.stringify(entry.detail, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function AuditView() {
  const [page, setPage] = useState(0)
  const [eventFilter, setEventFilter] = useState<string | undefined>(undefined)
  const [exporting, setExporting] = useState(false)

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['audit', page, eventFilter],
    queryFn: () => fetchAuditLog({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, event: eventFilter }),
    placeholderData: prev => prev,
  })

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  async function handleExport() {
    setExporting(true)
    try {
      await exportAuditLog()
    } catch (err) {
      console.error('Export failed:', err)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="max-w-5xl mx-auto p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Audit</h1>
          {data && (
            <p className="text-sm text-slate-400 mt-0.5">
              {data.total.toLocaleString()} total entries
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-slate-700 hover:bg-slate-600 text-sm text-slate-200 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
            Refresh
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="flex items-center gap-1.5 px-3 py-2 rounded-md bg-slate-700 hover:bg-slate-600 text-sm text-slate-200 disabled:opacity-50 transition-colors"
          >
            <Download size={14} />
            {exporting ? 'Exporting…' : 'Export JSON'}
          </button>
        </div>
      </div>

      {/* Event filter pills */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {EVENT_FILTERS.map(f => (
          <button
            key={f.label}
            onClick={() => { setEventFilter(f.value); setPage(0) }}
            className={`px-3 py-1 rounded-full text-sm font-medium transition-colors ${
              eventFilter === f.value
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Table */}
      {isError ? (
        <div className="text-center py-12 text-red-400">
          Failed to load audit log. Is the API running?
        </div>
      ) : isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-12 bg-slate-800 rounded animate-pulse" />
          ))}
        </div>
      ) : !data || data.entries.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <p className="text-lg">No audit entries yet</p>
          <p className="text-sm mt-1">Entries appear when operations are staged, approved, or rejected.</p>
        </div>
      ) : (
        <div className="bg-slate-800/50 rounded-lg border border-slate-700 overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-slate-700 text-xs text-slate-400 uppercase tracking-wide">
                <th className="px-3 py-2 w-6" />
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Event</th>
                <th className="px-3 py-2">Description</th>
                <th className="px-3 py-2">Actor</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map(entry => (
                <AuditRow key={entry.id} entry={entry} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-4 py-2 rounded-md bg-slate-700 hover:bg-slate-600 text-sm text-slate-200 disabled:opacity-40 transition-colors"
          >
            ← Previous
          </button>
          <span className="text-sm text-slate-400">
            Page {page + 1} of {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="px-4 py-2 rounded-md bg-slate-700 hover:bg-slate-600 text-sm text-slate-200 disabled:opacity-40 transition-colors"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
