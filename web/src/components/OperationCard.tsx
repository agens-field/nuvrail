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
  selected?: boolean
  onToggleSelect?: (id: string) => void
}

const WARN_OP_TYPES = new Set(['smtp_send', 'trash'])

function formatExpiry(expiresAt: number): string {
  const now = Date.now()
  const diffMs = expiresAt * 1000 - now
  if (diffMs <= 0) return 'expired'
  const duration = intervalToDuration({ start: 0, end: diffMs })
  return `in ${formatDuration(duration, { format: ['hours', 'minutes'] }) || 'seconds'}`
}

export default function OperationCard({ operation, selected, onToggleSelect }: OperationCardProps) {
  const qc = useQueryClient()
  const [showConfirm, setShowConfirm] = useState(false)

  const isSmtp = operation.protocol.toLowerCase() === 'smtp'
  const isWarn = WARN_OP_TYPES.has(operation.op_type)
  const isUrgent = (operation.is_urgent ?? 0) === 1

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
        className={`bg-surface rounded-lg border p-4 flex flex-col gap-3 transition-all ${
          isUrgent
            ? 'border-red-500/60 border-l-4 border-l-red-500 shadow-lg shadow-red-900/20'
            : isSmtp
            ? 'border-orange-500/40 border-l-2 border-l-orange-500'
            : 'border-edge'
        }`}
      >
        {/* Header */}
        <div className="flex items-start gap-2 flex-wrap">
          {onToggleSelect && (
            <span
              title={
                operation.op_type === 'smtp_send'
                  ? 'SMTP sends are approved individually'
                  : undefined
              }
            >
              <input
                type="checkbox"
                checked={selected ?? false}
                onChange={() => onToggleSelect(operation.id)}
                className="mt-0.5 w-4 h-4 rounded border-edge bg-surface-hi accent-emerald-500 cursor-pointer flex-shrink-0"
                aria-label={`Select operation ${operation.id}`}
              />
            </span>
          )}
          {isUrgent && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-900/60 border border-red-500/40 text-red-300 text-xs font-semibold uppercase tracking-wide flex-shrink-0">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse inline-block" />
              Urgent
            </span>
          )}
          <ProtocolBadge protocol={operation.protocol} />
          {isWarn && !isUrgent && (
            <AlertTriangle className="w-4 h-4 text-orange-400 flex-shrink-0 mt-0.5" />
          )}
          <span className="font-medium text-fg text-sm flex-1 min-w-0">
            {operation.description}
          </span>
        </div>

        {/* SMTP details */}
        {operation.smtp_envelope && (
          <div className="text-sm text-fg-3 space-y-0.5">
            <p>
              <span className="text-fg-3">To:</span>{' '}
              {operation.smtp_envelope.to.join(', ')}
            </p>
            <p>
              <span className="text-fg-3">Subject:</span>{' '}
              <span className="text-fg-2">
                &ldquo;{operation.smtp_envelope.subject}&rdquo;
              </span>
            </p>
          </div>
        )}

        {/* IMAP message previews — only shown for multi-message ops.
             Single-message ops: the description already carries sender + subject.
             Multi-message ops with mixed senders: description shows first message +
             overflow count; previews show the remaining messages' details. */}
        {!operation.smtp_envelope &&
          operation.message_previews &&
          operation.message_previews.length > 1 && (
          <div className="text-sm text-fg-3 space-y-1 border-t border-edge/50 pt-2 mt-1">
            {operation.message_previews.slice(1).map((msg) => (
              <div key={msg.uid} className="flex flex-col gap-0.5 pl-2 border-l border-edge">
                {msg.sender && (
                  <p className="truncate">
                    <span className="text-fg-3">From:</span>{' '}
                    <span className="text-fg-2">{msg.sender}</span>
                  </p>
                )}
                {msg.subject && (
                  <p className="truncate">
                    <span className="text-fg-3">Subject:</span>{' '}
                    <span className="text-fg-2">&ldquo;{msg.subject}&rdquo;</span>
                  </p>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Timing */}
        <div className="flex items-center gap-1 text-xs text-fg-3">
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
            className="px-4 py-2 rounded-md font-medium text-sm bg-surface hover:bg-surface-hi text-fg border border-edge transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
