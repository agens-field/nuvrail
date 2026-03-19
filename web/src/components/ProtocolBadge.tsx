interface ProtocolBadgeProps {
  protocol: string
}

export default function ProtocolBadge({ protocol }: ProtocolBadgeProps) {
  const isSmtp = protocol.toLowerCase() === 'smtp'
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold tracking-wide ${
        isSmtp
          ? 'bg-orange-500/20 text-orange-400'
          : 'bg-blue-500/20 text-blue-400'
      }`}
    >
      {protocol.toUpperCase()}
    </span>
  )
}
