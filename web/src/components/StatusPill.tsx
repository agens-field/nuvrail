const STATUS_STYLES: Record<string, string> = {
  pending: 'bg-yellow-500/20 text-yellow-400',
  approved: 'bg-emerald-500/20 text-emerald-400',
  rejected: 'bg-red-500/20 text-red-400',
  executed: 'bg-emerald-600/20 text-emerald-300',
  failed: 'bg-red-600/20 text-red-300',
  expired: 'bg-slate-600/40 text-fg-3',
}

interface StatusPillProps {
  status: string
}

export default function StatusPill({ status }: StatusPillProps) {
  const style = STATUS_STYLES[status] ?? 'bg-slate-600/40 text-fg-3'
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium capitalize ${style}`}
    >
      {status}
    </span>
  )
}
