import { useQuery } from '@tanstack/react-query'
import { Inbox, RefreshCw } from 'lucide-react'
import { fetchOperations } from '../api/client'
import OperationCard from '../components/OperationCard'
import LoadingSkeleton from '../components/LoadingSkeleton'

export default function PendingView() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['operations', 'pending'],
    queryFn: () => fetchOperations('pending'),
    refetchInterval: 5000,
  })

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
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
            <OperationCard key={op.id} operation={op} />
          ))}
        </div>
      )}
    </div>
  )
}
