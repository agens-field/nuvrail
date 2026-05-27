/**
 * ProviderPicker — reusable provider selection card grid.
 *
 * Used in both the initial SetupView wizard and the AgentsView add flow
 * so both paths look identical.
 *
 *  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 *  │  Gmail       │  │  iCloud      │  │  Outlook     │  │  Other IMAP  │
 *  │  OAuth2      │  │  App pwd     │  │  Coming soon │  │  Any server  │
 *  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
 */

export type Provider = 'gmail' | 'icloud' | 'outlook' | 'other'

interface ProviderOption {
  id: Provider
  label: string
  description: string
  icon: string
  available: boolean
  comingSoon?: boolean
}

const PROVIDERS: ProviderOption[] = [
  {
    id: 'gmail',
    label: 'Gmail',
    description: 'Sign in with Google',
    icon: '✉',
    available: true,
  },
  {
    id: 'icloud',
    label: 'iCloud Mail',
    description: 'App-specific password',
    icon: '☁',
    available: true,
  },
  {
    id: 'outlook',
    label: 'Outlook / 365',
    description: 'Microsoft OAuth2',
    icon: '📧',
    available: false,
    comingSoon: true,
  },
  {
    id: 'other',
    label: 'Other',
    description: 'Any IMAP / SMTP server',
    icon: '⚙',
    available: true,
  },
]

interface ProviderPickerProps {
  onSelect: (provider: Provider) => void
  heading?: string
  subheading?: string
}

export default function ProviderPicker({
  onSelect,
  heading = 'Connect your email',
  subheading = 'Choose your email provider to get started.',
}: ProviderPickerProps) {
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-display font-black text-fg">{heading}</h2>
        <p className="text-sm text-fg-3 mt-1">{subheading}</p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {PROVIDERS.map((p) => (
          <button
            key={p.id}
            onClick={() => p.available && onSelect(p.id)}
            disabled={!p.available}
            className={`
              relative flex flex-col items-center gap-2 p-4 rounded-xl border text-center
              transition-all duration-150
              ${p.available
                ? 'bg-surface border-edge hover:border-accent hover:bg-surface-hi cursor-pointer group'
                : 'bg-surface/50 border-edge/50 cursor-not-allowed opacity-50'
              }
            `}
          >
            {p.comingSoon && (
              <span className="absolute top-2 right-2 text-[9px] font-mono uppercase tracking-wide text-fg-3 bg-surface-hi px-1.5 py-0.5 rounded">
                Soon
              </span>
            )}
            <span
              className={`text-2xl transition-colors ${
                p.available ? 'group-hover:scale-110' : ''
              } inline-block transition-transform`}
            >
              {p.icon}
            </span>
            <div>
              <p className={`text-sm font-semibold ${p.available ? 'text-fg group-hover:text-accent' : 'text-fg-3'} transition-colors`}>
                {p.label}
              </p>
              <p className="text-xs text-fg-3 mt-0.5">{p.description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
