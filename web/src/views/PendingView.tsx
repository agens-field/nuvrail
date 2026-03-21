import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Inbox, RefreshCw } from 'lucide-react'
import { fetchOperations, batchApproveOperations, batchRejectOperations } from '../api/client'
import OperationCard from '../components/OperationCard'
import LoadingSkeleton from '../components/LoadingSkeleton'

export default function PendingView() {
  const qc = useQueryClient()
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['operations', 'pending'],
    queryFn: () => fetchOperations('pending'),
    refetchInterval: 5000,
  })

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [isBatchApproving, setIsBatchApproving] = useState(false)
  const [isBatchRejecting, setIsBatchRejecting] = useState(false)

  // Sort urgent ops (is_urgent=1) to the top, then by created_at ascending
  const pendingOps = [...(data?.operations ?? [])].sort((a, b) => {
    const urgentA = (a.is_urgent ?? 0) === 1 ? 1 : 0
    const urgentB = (b.is_urgent ?? 0) === 1 ? 1 : 0
    if (urgentB !== urgentA) return urgentB - urgentA  // urgent first
    return a.created_at - b.created_at                 // then oldest first
  })
  const allPendingIds = pendingOps.map((op) => op.id)
  const allSelected =
    allPendingIds.length > 0 && allPendingIds.every((id) => selectedIds.has(id))
  const someSelected = !allSelected && allPendingIds.some((id) => selectedIds.has(id))

  const handleToggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const handleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(allPendingIds))
    }
  }

  const handleBatchApprove = async () => {
    const ids = [...selectedIds]
    if (ids.length === 0) return
    setIsBatchApproving(true)
    try {
      const result = await batchApproveOperations(ids)
      const approvedCount = result.approved.length
      const failedCount = result.failed.length
      const skippedCount = result.skipped.length
      if (failedCount > 0) {
        toast.error(
          `${approvedCount} approved, ${failedCount} failed, ${skippedCount} skipped`,
        )
      } else {
        toast.success(
          `${approvedCount} approved${skippedCount > 0 ? `, ${skippedCount} skipped` : ''}`,
        )
      }
      setSelectedIds(new Set())
      void qc.invalidateQueries({ queryKey: ['operations'] })
    } catch (err) {
      toast.error(`Batch approve failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setIsBatchApproving(false)
    }
  }

  const handleBatchReject = async () => {
    const ids = [...selectedIds]
    if (ids.length === 0) return
    setIsBatchRejecting(true)
    try {
      const result = await batchRejectOperations(ids)
      const rejectedCount = result.rejected.length
      const failedCount = result.failed.length
      const skippedCount = result.skipped.length
      if (failedCount > 0) {
        toast.error(
          `${rejectedCount} rejected, ${failedCount} failed, ${skippedCount} skipped`,
        )
      } else {
        toast.success(
          `${rejectedCount} rejected${skippedCount > 0 ? `, ${skippedCount} skipped` : ''}`,
        )
      }
      setSelectedIds(new Set())
      void qc.invalidateQueries({ queryKey: ['operations'] })
    } catch (err) {
      toast.error(`Batch reject failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setIsBatchRejecting(false)
    }
  }

  const isBatchInFlight = isBatchApproving || isBatchRejecting

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {pendingOps.length > 0 && (
            <input
              type="checkbox"
              checked={allSelected}
              ref={(el) => {
                if (el) el.indeterminate = someSelected
              }}
              onChange={handleSelectAll}
              className="w-4 h-4 rounded border-slate-600 bg-slate-700 accent-emerald-500 cursor-pointer"
              aria-label="Select all pending operations"
              title="Select all"
            />
          )}
          <h1 className="text-lg font-semibold text-slate-100">
            Pending Operations
          </h1>
          {data && (
            <span className="text-sm text-slate-400">
              ({data.total} op{data.total !== 1 ? 's' : ''})
            </span>
          )}
        </div>
        <button
          onClick={() => void refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600 transition-colors disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw
            className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`}
          />
          Refresh
        </button>
      </div>

      {/* Batch action bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 px-4 py-2.5 rounded-lg border border-slate-600 bg-slate-800/80">
          <span className="text-sm text-slate-300 flex-1">
            {selectedIds.size} selected
          </span>
          <button
            onClick={() => void handleBatchApprove()}
            disabled={isBatchInFlight}
            className="px-3 py-1.5 rounded-md text-sm font-medium bg-emerald-600 hover:bg-emerald-500 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isBatchApproving ? 'Approving…' : 'Approve selected'}
          </button>
          <button
            onClick={() => void handleBatchReject()}
            disabled={isBatchInFlight}
            className="px-3 py-1.5 rounded-md text-sm font-medium bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isBatchRejecting ? 'Rejecting…' : 'Reject selected'}
          </button>
        </div>
      )}

      {/* Content */}
      {isLoading && <LoadingSkeleton />}

      {isError && (
        <div className="rounded-lg border border-red-800/50 bg-red-900/20 p-4 text-sm text-red-400">
          Failed to load operations:{' '}
          {error instanceof Error ? error.message : 'Unknown error'}
        </div>
      )}

      {!isLoading && !isError && data && data.operations.length === 0 && (
        <div className="flex flex-col items-center justify-center py-24 text-slate-500 gap-4">
          <Inbox className="w-12 h-12 opacity-40" />
          <p className="text-sm">No pending operations</p>
        </div>
      )}

      {!isLoading && data && data.operations.length > 0 && (
        <div className="flex flex-col gap-4">
          {data.operations.map((op) => (
            <OperationCard
              key={op.id}
              operation={op}
              selected={selectedIds.has(op.id)}
              onToggleSelect={handleToggleSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}
