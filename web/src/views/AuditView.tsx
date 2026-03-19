import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { formatDistanceToNow, format } from 'date-fns'
import { fetchOperations } from '../api/client'
import type { Operation } from '../types'
import ProtocolBadge from '../components/ProtocolBadge'
import StatusPill from '../components/StatusPill'

type FilterStatus = 'all' | 'pending' | 'executed' | 'rejected' | 'failed'

const FILTER_BUTTONS: { label: string; value: FilterStatus }[] = [
  { label: 'All', value: 'all' },
  { label: 'Pending', value: 'pending' },
  { label: 'Executed', value: 'executed' },
  { label: 'Rejected', value: 'rejected' },
  { label: 'Failed', value: 'failed' },
]

const PAGE_SIZE = 20

function OperationDetail({ op }: { op: Operation }) {
  if (op.smtp_envelope) {
    return (
      <div className="px-4 pb-4 pt-2 text-sm text-slate-400 space-y-1 border-t border-slate-700/50">
        <p>
          <span className="text-slate-500">From:</span> {op.smtp_envelope.from}
        </p>
        <p>
          <span className="text-slate-500">To:</span>{' '}
          {op.smtp_envelope.to.join(', ')}
        </p>
        <p>
          <span className="text-slate-500">Subject:</span>{' '}
          <span className="text-slate-300">{op.smtp_envelope.subject}</span>
        </p>
        <p className="text-slate-500 mt-2 font-medium">Preview:</p>
        <p className="text-slate-400 whitespace-pre-wrap leading-relaxed">
          {op.smtp_envelope.body_preview}
        </p>
      </div>
    )
  }

  if (op.imap_command) {
    return (
      <div className="px-4 pb-4 pt-2 border-t border-slate-700/50">
        <p className="text-xs text-slate-500 mb-1">IMAP command</p>
        <pre className="text-xs text-slate-300 bg-slate-900 rounded p-3 overflow-x-auto font-mono">
          {op.imap_command}
        </pre>
      </div>
    )
  }

  return (
    <div className="px-4 pb-4 pt-2 border-t border-slate-700/50">
      <p className="text-xs text-slate-500">No additional details</p>
    </div>
  )
}

interface AuditRowProps {
  op: Operation
  expanded: boolean
  onToggle: () => void
}

function AuditRow({ op, expanded, onToggle }: AuditRowProps) {
  const createdDate = new Date(op.created_at * 1000)
  const relative = formatDistanceToNow(createdDate, { addSuffix: true })
  const absolute = format(createdDate, 'MMM d, yyyy HH:mm:ss')

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-slate-750 transition-colors"
      >
        {/* Expand icon */}
        <span className="text-slate-600 flex-shrink-0">
          {expanded ? (
            <ChevronDown className="w-4 h-4" />
          ) : (
            <ChevronRight className="w-4 h-4" />
          )}
        </span>

        {/* Timestamp */}
        <span
          className="text-xs text-slate-400 w-28 flex-shrink-0"
          title={absolute}
        >
          {relative}
        </span>

        {/* Badge */}
        <ProtocolBadge protocol={op.protocol} />

        {/* Description */}
        <span className="text-sm text-slate-200 flex-1 min-w-0 truncate">
          {op.description}
        </span>

        {/* Status pill */}
        <StatusPill status={op.status} />
      </button>

      {expanded && <OperationDetail op={op} />}
    </div>
  )
}

export default function AuditView() {
  const [filter, setFilter] = useState<FilterStatus>('all')
  const [page, setPage] = useState(0)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['operations', 'all'],
    queryFn: () => fetchOperations(),
    staleTime: 10000,
  })

  const filtered =
    data?.operations.filter(
      (op) => filter === 'all' || op.status === filter,
    ) ?? []

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const pageItems = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const handleFilterChange = (f: FilterStatus) => {
    setFilter(f)
    setPage(0)
    setExpandedId(null)
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <h1 className="text-lg font-semibold text-slate-100">Audit Log</h1>

      {/* Filter buttons */}
      <div className="flex gap-2 flex-wrap">
        {FILTER_BUTTONS.map(({ label, value }) => (
          <button
            key={value}
            onClick={() => handleFilterChange(value)}
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
              filter === value
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-700 hover:bg-slate-600 text-slate-300 border border-slate-600'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {isLoading && (
        <div className="flex flex-col gap-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="h-12 bg-slate-800 rounded-lg border border-slate-700 animate-pulse"
            />
          ))}
        </div>
      )}

      {isError && (
        <div className="rounded-lg border border-red-800/50 bg-red-900/20 p-4 text-sm text-red-400">
          Failed to load audit log:{' '}
          {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      )}

      {!isLoading && !isError && filtered.length === 0 && (
        <p className="text-sm text-slate-500 py-8 text-center">
          No operations found
        </p>
      )}

      {!isLoading && pageItems.length > 0 && (
        <>
          <div className="flex flex-col gap-2">
            {pageItems.map((op) => (
              <AuditRow
                key={op.id}
                op={op}
                expanded={expandedId === op.id}
                onToggle={() =>
                  setExpandedId(expandedId === op.id ? null : op.id)
                }
              />
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs text-slate-500">
                Page {page + 1} of {totalPages} · {filtered.length} total
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="px-3 py-1.5 rounded-md text-sm font-medium bg-slate-700 hover:bg-slate-600 text-slate-300 border border-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Previous
                </button>
                <button
                  onClick={() =>
                    setPage((p) => Math.min(totalPages - 1, p + 1))
                  }
                  disabled={page >= totalPages - 1}
                  className="px-3 py-1.5 rounded-md text-sm font-medium bg-slate-700 hover:bg-slate-600 text-slate-300 border border-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
