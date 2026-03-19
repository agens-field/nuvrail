import { useState } from 'react'
import { useQueryClient, useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Clock } from 'lucide-react'
import { formatDistanceToNow, formatDuration, intervalToDuration } from 'date-fns'
import { approveOperation, rejectOperation } from '../api/client'
import type { Operation } from '../types'
import ProtocolBadge from './ProtocolBadge'
import ConfirmDialog from './ConfirmDialog'

interface OperationCardProps {
  operation: Operation
}

const WARN_OP_TYPES = new Set(['smtp_send', 'trash'])

function formatExpiry(expiresAt: number): string {
  const now = Date.now()
  const diffMs = expiresAt * 1000 - now
  if (diffMs <= 0) return 'expired'
  const duration = intervalToDuration({ start: 0, end: diffMs })
  return `in ${formatDuration(duration, { format: ['hours', 'minutes'] }) || 'seconds'}`
}

export default function OperationCard({ operation }: OperationCardProps) {
  const qc = useQueryClient()
  const [showConfirm, setShowConfirm] = useState(false)

  const isSmtp = operation.protocol.toLowerCase() === 'smtp'
  const isWarn = WARN_OP_TYPES.has(operation.op_type)

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['operations'] })
  }

  const approveMut = useMutation({
    mutationFn: () => approveOperation(operation.id),
    onSuccess: () => {
      toast.success(`Approved: ${operation.description}`)
      invalidate()
    },
    onError: (err: Error) => {
      toast.error(`Approve failed: ${err.message}`)
    },
  })

  const rejectMut = useMutation({
    mutationFn: () => rejectOperation(operation.id),
    onSuccess: () => {
      toast.success(`Rejected: ${operation.description}`)
      invalidate()
    },
    onError: (err: Error) => {
      toast.error(`Reject failed: ${err.message}`)
    },
  })

  const handleApprove = () => {
    if (isSmtp) {
      setShowConfirm(true)
    } else {
      approveMut.mutate()
    }
  }

  const handleConfirmApprove = () => {
    setShowConfirm(false)
    approveMut.mutate()
  }

  const isPending = approveMut.isPending || rejectMut.isPending

  return (
    <>
      <div
        className={`bg-slate-800 rounded-lg border p-4 flex flex-col gap-3 ${
          isSmtp
            ? 'border-orange-500/40 border-l-2 border-l-orange-500'
            : 'border-slate-700'
        }`}
      >
        {/* Header */}
        <div className="flex items-start gap-2 flex-wrap">
          <ProtocolBadge protocol={operation.protocol} />
          {isWarn && (
            <AlertTriangle className="w-4 h-4 text-orange-400 flex-shrink-0 mt-0.5" />
          )}
          <span className="font-medium text-slate-100 text-sm flex-1 min-w-0">
            {operation.description}
          </span>
        </div>

        {/* SMTP details */}
        {operation.smtp_envelope && (
          <div className="text-sm text-slate-400 space-y-0.5">
            <p>
              <span className="text-slate-500">To:</span>{' '}
              {operation.smtp_envelope.to.join(', ')}
            </p>
            <p>
              <span className="text-slate-500">Subject:</span>{' '}
              <span className="text-slate-300">
                &ldquo;{operation.smtp_envelope.subject}&rdquo;
              </span>
            </p>
          </div>
        )}

        {/* Timing */}
        <div className="flex items-center gap-1 text-xs text-slate-500">
          <Clock className="w-3 h-3" />
          <span>
            Queued{' '}
            {formatDistanceToNow(new Date(operation.created_at * 1000), {
              addSuffix: true,
            })}
          </span>
          <span className="mx-1">·</span>
          <span>Expires {formatExpiry(operation.expires_at)}</span>
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <button
            onClick={() => rejectMut.mutate()}
            disabled={isPending}
            className="px-4 py-2 rounded-md font-medium text-sm bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Reject
          </button>
          <button
            onClick={handleApprove}
            disabled={isPending}
            className="px-4 py-2 rounded-md font-medium text-sm bg-emerald-600 hover:bg-emerald-500 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {approveMut.isPending ? 'Approving…' : 'Approve'}
          </button>
        </div>
      </div>

      {showConfirm && (
        <ConfirmDialog
          title="Send this email?"
          message="This will actually deliver the email to the recipient(s). This cannot be undone."
          confirmLabel="Yes, send it"
          onConfirm={handleConfirmApprove}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </>
  )
}
