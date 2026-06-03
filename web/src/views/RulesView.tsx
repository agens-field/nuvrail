import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ArrowDown, ArrowUp, FlaskConical, Inbox, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { createRule, deleteRule, fetchAgents, fetchRules, testRule, updateRule } from '../api/client'
import type { AutoApprovalAction, AutoApprovalRule, RuleTestResponse } from '../types'

const OP_TYPE_OPTIONS = [
  { value: '', label: 'Any operation type' },
  { value: 'mark_read', label: 'mark_read' },
  { value: 'mark_unread', label: 'mark_unread' },
  { value: 'move', label: 'move' },
  { value: 'copy', label: 'copy' },
  { value: 'trash', label: 'trash' },
  { value: 'star', label: 'star' },
  { value: 'unstar', label: 'unstar' },
  { value: 'smtp_send', label: 'smtp_send' },
] as const

const ACTION_OPTIONS: { value: AutoApprovalAction; label: string }[] = [
  { value: 'approve', label: 'Approve immediately' },
  { value: 'approve_after', label: 'Approve after a delay (cool-down)' },
  { value: 'reject', label: 'Reject' },
  { value: 'hold', label: 'Hold for review' },
]

type RuleFormState = {
  op_type: string
  sender_pattern: string
  folder_from: string
  folder_to: string
  recipient_pattern: string
  subject_pattern: string
  flag_pattern: string
  min_message_count: string
  max_message_count: string
  agent_id: string // '' = any agent
  action: AutoApprovalAction
  delay_minutes: string       // cool-down for approve_after (entered in minutes)
  rate_limit_per_hour: string // optional cap for approving actions
  set_urgent: boolean         // urgency override for hold/approve_after
  // Tier 3 guardrails
  is_guardrail: boolean
  shadow: boolean
  active_start: string        // "HH:MM" active-window start (blank = always)
  active_end: string          // "HH:MM" active-window end
  active_tz: string           // IANA tz (blank = UTC)
  description: string
}

const initialFormState: RuleFormState = {
  op_type: '',
  sender_pattern: '',
  folder_from: '',
  folder_to: '',
  recipient_pattern: '',
  subject_pattern: '',
  flag_pattern: '',
  min_message_count: '',
  max_message_count: '',
  agent_id: '',
  action: 'approve',
  delay_minutes: '',
  rate_limit_per_hour: '',
  set_urgent: false,
  is_guardrail: false,
  shadow: false,
  active_start: '',
  active_end: '',
  active_tz: '',
  description: '',
}

type TestFormState = {
  op_type: string
  sender: string
  folder_from: string
  folder_to: string
  recipient: string
  subject: string
  flag: string
  message_count: string
  agent_id: string
}

const initialTestForm: TestFormState = {
  op_type: '',
  sender: '',
  folder_from: '',
  folder_to: '',
  recipient: '',
  subject: '',
  flag: '',
  message_count: '',
  agent_id: '',
}

function describeSender(pattern: string): string {
  const trimmed = pattern.trim()
  if (trimmed.startsWith('*@')) {
    return `from addresses ending in ${trimmed.slice(1)}`
  }
  return `from senders matching "${trimmed}"`
}

function normalizeOptionalInput(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

/** Parse a non-negative integer count input, or null when blank/invalid. */
function normalizeCount(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const n = Number(trimmed)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null
}

/** "HH:MM" -> minutes since midnight, or null if blank/invalid. */
function hhmmToMinutes(value: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(value.trim())
  if (!m) return null
  const h = Number(m[1])
  const min = Number(m[2])
  if (h > 23 || min > 59) return null
  return h * 60 + min
}

/** minutes since midnight -> "HH:MM" (for display / time inputs). */
function minutesToHHMM(total: number): string {
  const h = Math.floor(total / 60)
  const m = total % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/** Humanise a duration in minutes, e.g. 90 -> "1h 30m", 10 -> "10m". */
function humanizeMinutes(totalMinutes: number): string {
  if (totalMinutes <= 0) return '0m'
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  if (h && m) return `${h}h ${m}m`
  if (h) return `${h}h`
  return `${m}m`
}

function actionPhrase(form: RuleFormState): string {
  const urgent = form.set_urgent ? ' (mark urgent)' : ''
  switch (form.action) {
    case 'approve_after': {
      const mins = normalizeCount(form.delay_minutes) ?? 0
      return `auto-approve after ${humanizeMinutes(mins)} (cancelable until then)${urgent}`
    }
    case 'reject':
      return 'auto-reject'
    case 'hold':
      return `hold for manual review${urgent}`
    default:
      return 'auto-approve'
  }
}

function buildRulePreview(form: RuleFormState, agentLabel: (id: number) => string): string {
  const parts: string[] = [form.op_type ? `${form.op_type} ops` : 'any ops']
  if (form.sender_pattern.trim()) parts.push(describeSender(form.sender_pattern))
  if (form.folder_from.trim()) parts.push(`from ${form.folder_from.trim()}`)
  if (form.folder_to.trim()) parts.push(`into ${form.folder_to.trim()}`)
  if (form.recipient_pattern.trim()) parts.push(`to recipients matching "${form.recipient_pattern.trim()}"`)
  if (form.subject_pattern.trim()) parts.push(`with subject matching "${form.subject_pattern.trim()}"`)
  if (form.flag_pattern.trim()) parts.push(`touching flag "${form.flag_pattern.trim()}"`)
  const min = normalizeCount(form.min_message_count)
  const max = normalizeCount(form.max_message_count)
  if (min !== null && max !== null) parts.push(`affecting ${min}–${max} messages`)
  else if (min !== null) parts.push(`affecting ≥ ${min} messages`)
  else if (max !== null) parts.push(`affecting ≤ ${max} messages`)
  if (form.agent_id) parts.push(`staged by ${agentLabel(Number(form.agent_id))}`)
  const start = hhmmToMinutes(form.active_start)
  const end = hhmmToMinutes(form.active_end)
  if (start !== null && end !== null) {
    parts.push(`only ${form.active_start}–${form.active_end} ${form.active_tz.trim() || 'UTC'}`)
  }
  const cap = normalizeCount(form.rate_limit_per_hour)
  const capClause =
    cap !== null && (form.action === 'approve' || form.action === 'approve_after')
      ? `, up to ${cap}/hour then fall back to manual review`
      : ''
  if (form.shadow) {
    return `Shadow mode: would ${actionPhrase(form)} ${parts.join(', ')} — logged only, never acts.`
  }
  const lead = form.is_guardrail ? 'Guardrail — wins over normal rules. Will' : 'This rule will'
  return `${lead} ${actionPhrase(form)}: ${parts.join(', ')}${capClause}.`
}

const ACTION_BADGE: Record<AutoApprovalAction, string> = {
  approve: 'bg-emerald-900/70 text-emerald-200',
  approve_after: 'bg-sky-900/70 text-sky-200',
  reject: 'bg-red-900/70 text-red-200',
  hold: 'bg-amber-900/70 text-amber-200',
}

/** Compact action label for the rules table, including Tier 2 modifiers. */
function actionLabel(rule: AutoApprovalRule): string {
  const base =
    rule.action === 'approve_after'
      ? `approve after ${humanizeMinutes(Math.round((rule.delay_seconds ?? 0) / 60))}`
      : rule.action
  const cap = rule.rate_limit_per_hour != null ? ` ≤${rule.rate_limit_per_hour}/h` : ''
  const urgent = rule.set_urgent ? ' ⚡' : ''
  return `${base}${cap}${urgent}`
}

const TEST_ACTION_VERB: Record<string, string> = {
  approve: 'auto-approved',
  approve_after: 'auto-approved after a cool-down',
  reject: 'auto-rejected',
  hold: 'held for manual review',
}

function testHeadline(r: RuleTestResponse): string {
  if (!r.matched) return '⚪ No rule matched — this op would be queued for review.'
  const verb = TEST_ACTION_VERB[r.action ?? ''] ?? `decided (${r.action})`
  return `Would be ${verb} by: "${r.rule_description}"`
}

/** Short labelled chips summarising every match condition set on a rule. */
function ruleConditions(rule: AutoApprovalRule, agentLabel: (id: number) => string): string[] {
  const chips: string[] = []
  if (rule.sender_pattern) chips.push(`sender: ${rule.sender_pattern}`)
  if (rule.folder_from) chips.push(`from: ${rule.folder_from}`)
  if (rule.folder_to) chips.push(`to: ${rule.folder_to}`)
  if (rule.recipient_pattern) chips.push(`recipient: ${rule.recipient_pattern}`)
  if (rule.subject_pattern) chips.push(`subject: ${rule.subject_pattern}`)
  if (rule.flag_pattern) chips.push(`flag: ${rule.flag_pattern}`)
  const min = rule.min_message_count
  const max = rule.max_message_count
  if (min != null || max != null) {
    chips.push(`count: ${min ?? 0}–${max ?? '∞'}`)
  }
  if (rule.agent_id != null) chips.push(`agent: ${agentLabel(rule.agent_id)}`)
  if (rule.active_start_min != null && rule.active_end_min != null) {
    chips.push(
      `${minutesToHHMM(rule.active_start_min)}–${minutesToHHMM(rule.active_end_min)} ${rule.active_tz || 'UTC'}`
    )
  }
  return chips
}

export default function RulesView() {
  const qc = useQueryClient()
  const [form, setForm] = useState<RuleFormState>(initialFormState)
  const [deletingRuleId, setDeletingRuleId] = useState<number | null>(null)
  const [updatingRuleId, setUpdatingRuleId] = useState<number | null>(null)

  // Rule test panel
  const [testForm, setTestForm] = useState<TestFormState>(initialTestForm)
  const [testResult, setTestResult] = useState<RuleTestResponse | null>(null)
  const [testPending, setTestPending] = useState(false)

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['rules'],
    queryFn: fetchRules,
  })

  // Agents power the friendly agent picker + label lookup in the rules table.
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: fetchAgents })
  const agentLabel = useMemo(() => {
    const byId = new Map((agents ?? []).map((a) => [a.id, a.label || a.agent_username]))
    return (id: number) => byId.get(id) ?? `#${id}`
  }, [agents])

  const createMutation = useMutation({
    mutationFn: createRule,
    onSuccess: () => {
      toast.success('Rule created')
      setForm(initialFormState)
      void qc.invalidateQueries({ queryKey: ['rules'] })
    },
    onError: (err) => {
      toast.error(`Create rule failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    },
  })

  const previewText = useMemo(() => buildRulePreview(form, agentLabel), [form, agentLabel])
  const rules = useMemo(
    () =>
      [...(data ?? [])].sort((a, b) => {
        if (a.priority !== b.priority) {
          return b.priority - a.priority
        }
        return a.id - b.id
      }),
    [data]
  )

  function handleCreateRule(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!form.description.trim()) {
      toast.error('Description is required')
      return
    }

    const min = normalizeCount(form.min_message_count)
    const max = normalizeCount(form.max_message_count)
    if (min !== null && max !== null && min > max) {
      toast.error('Min messages cannot exceed max messages')
      return
    }

    const delayMinutes = normalizeCount(form.delay_minutes)
    if (form.action === 'approve_after' && !delayMinutes) {
      toast.error('A cool-down rule needs a delay greater than 0 minutes')
      return
    }

    const isApproving = form.action === 'approve' || form.action === 'approve_after'
    const canSetUrgent = form.action === 'hold' || form.action === 'approve_after'
    const rateLimit = normalizeCount(form.rate_limit_per_hour)

    const startMin = hhmmToMinutes(form.active_start)
    const endMin = hhmmToMinutes(form.active_end)
    if ((form.active_start.trim() === '') !== (form.active_end.trim() === '')) {
      toast.error('An active window needs both a start and end time')
      return
    }
    if (form.active_start.trim() && startMin === null) {
      toast.error('Active window times must be valid (HH:MM)')
      return
    }
    if (form.active_end.trim() && endMin === null) {
      toast.error('Active window times must be valid (HH:MM)')
      return
    }

    const maxPriority = rules.reduce((currentMax, rule) => Math.max(currentMax, rule.priority), 0)

    createMutation.mutate({
      action: form.action,
      description: form.description.trim(),
      priority: maxPriority + 1,
      op_type: normalizeOptionalInput(form.op_type),
      sender_pattern: normalizeOptionalInput(form.sender_pattern),
      folder_from: normalizeOptionalInput(form.folder_from),
      folder_to: normalizeOptionalInput(form.folder_to),
      recipient_pattern: normalizeOptionalInput(form.recipient_pattern),
      subject_pattern: normalizeOptionalInput(form.subject_pattern),
      flag_pattern: normalizeOptionalInput(form.flag_pattern),
      min_message_count: min,
      max_message_count: max,
      agent_id: form.agent_id ? Number(form.agent_id) : null,
      delay_seconds: form.action === 'approve_after' && delayMinutes ? delayMinutes * 60 : null,
      rate_limit_per_hour: isApproving ? rateLimit : null,
      set_urgent: canSetUrgent && form.set_urgent ? 1 : null,
      is_guardrail: form.is_guardrail,
      shadow: form.shadow,
      active_start_min: startMin,
      active_end_min: endMin,
      active_tz: normalizeOptionalInput(form.active_tz),
    })
  }

  async function handleToggleRule(rule: AutoApprovalRule) {
    setUpdatingRuleId(rule.id)
    try {
      await updateRule(rule.id, { enabled: !rule.enabled })
      toast.success(`Rule ${!rule.enabled ? 'enabled' : 'disabled'}`)
      void qc.invalidateQueries({ queryKey: ['rules'] })
    } catch (err) {
      toast.error(`Update rule failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setUpdatingRuleId(null)
    }
  }

  async function handleDeleteRule(ruleId: number) {
    setDeletingRuleId(ruleId)
    try {
      await deleteRule(ruleId)
      toast.success('Rule deleted')
      void qc.invalidateQueries({ queryKey: ['rules'] })
    } catch (err) {
      toast.error(`Delete rule failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setDeletingRuleId(null)
    }
  }

  async function handleTestRule(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!testForm.op_type.trim()) {
      toast.error('Op type is required for test')
      return
    }
    setTestPending(true)
    setTestResult(null)
    const count = normalizeCount(testForm.message_count)
    const flag = testForm.flag.trim()
    try {
      const result = await testRule({
        op_type: testForm.op_type.trim(),
        sender: testForm.sender.trim() || null,
        folder_from: testForm.folder_from.trim() || null,
        folder_to: testForm.folder_to.trim() || null,
        recipient: testForm.recipient.trim() || null,
        subject: testForm.subject.trim() || null,
        flags_add: flag ? [flag] : null,
        message_ids:
          count && count > 0 ? Array.from({ length: count }, (_, i) => String(i + 1)) : null,
        agent_id: testForm.agent_id ? Number(testForm.agent_id) : null,
      })
      setTestResult(result)
    } catch (err) {
      toast.error(`Test failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setTestPending(false)
    }
  }

  async function handleMoveRule(index: number, direction: 'up' | 'down') {
    if (!rules.length) return
    const swapIndex = direction === 'up' ? index - 1 : index + 1
    if (swapIndex < 0 || swapIndex >= rules.length) return

    const reordered = [...rules]
    const [moved] = reordered.splice(index, 1)
    reordered.splice(swapIndex, 0, moved)

    const desiredPriorities = reordered.map((rule, orderIndex) => ({
      id: rule.id,
      priority: reordered.length - orderIndex,
    }))

    const updates = desiredPriorities.filter(({ id, priority }) => {
      const current = rules.find((rule) => rule.id === id)
      return current != null && current.priority !== priority
    })

    if (!updates.length) return

    setUpdatingRuleId(moved.id)

    try {
      await Promise.all(updates.map(({ id, priority }) => updateRule(id, { priority })))
      toast.success('Rule priority updated')
      void qc.invalidateQueries({ queryKey: ['rules'] })
    } catch (err) {
      toast.error(`Reorder failed: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setUpdatingRuleId(null)
    }
  }

  const inputClass =
    'w-full rounded-md bg-surface-hi border border-edge px-3 py-2 text-sm text-fg-2 placeholder:text-fg-3'

  const agentOptions = (agents ?? []).filter((a) => a.revoked_at == null)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-fg">Auto-approval Rules</h1>
          <p className="text-sm text-fg-3 mt-1">
            Create filters to auto-approve or auto-reject predictable operations.
          </p>
        </div>
        <button
          onClick={() => void refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium bg-surface-hi hover:bg-edge text-fg-2 border border-edge transition-colors disabled:opacity-50"
          title="Refresh rules"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <section className="rounded-lg border border-edge bg-surface/40 p-4">
        <div className="flex items-center gap-2 mb-4">
          <Plus className="w-4 h-4 text-accent" />
          <h2 className="text-sm font-semibold text-fg">Add rule</h2>
        </div>
        <form onSubmit={(e) => void handleCreateRule(e)} className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Op type</label>
              <select
                value={form.op_type}
                onChange={(e) => setForm((prev) => ({ ...prev, op_type: e.target.value }))}
                className={inputClass}
              >
                {OP_TYPE_OPTIONS.map((option) => (
                  <option key={option.value || 'any'} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Action</label>
              <select
                value={form.action}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, action: e.target.value as AutoApprovalAction }))
                }
                className={inputClass}
              >
                {ACTION_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Action settings — only those relevant to the chosen action. */}
          {form.action !== 'reject' && (
            <fieldset className="rounded-md border border-edge/70 p-3">
              <legend className="px-1 text-xs font-medium text-fg-3">Action settings</legend>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
                {form.action === 'approve_after' && (
                  <div>
                    <label className="block text-xs font-medium text-fg-3 mb-1">
                      Delay (minutes) <span className="text-red-400">*</span>
                    </label>
                    <input
                      type="number"
                      min={1}
                      value={form.delay_minutes}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, delay_minutes: e.target.value }))
                      }
                      placeholder="10"
                      className={inputClass}
                    />
                    <p className="text-xs text-fg-3/70 mt-1">
                      Auto-runs after this long unless you cancel first — your window to catch a
                      bad send.
                    </p>
                  </div>
                )}
                {(form.action === 'approve' || form.action === 'approve_after') && (
                  <div>
                    <label className="block text-xs font-medium text-fg-3 mb-1">
                      Rate limit (per hour)
                    </label>
                    <input
                      type="number"
                      min={1}
                      value={form.rate_limit_per_hour}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, rate_limit_per_hour: e.target.value }))
                      }
                      placeholder="No limit"
                      className={inputClass}
                    />
                    <p className="text-xs text-fg-3/70 mt-1">
                      Beyond this many auto-approvals per hour, fall back to manual review.
                    </p>
                  </div>
                )}
                {(form.action === 'hold' || form.action === 'approve_after') && (
                  <div>
                    <label className="block text-xs font-medium text-fg-3 mb-1">Urgency</label>
                    <label className="inline-flex items-center gap-2 rounded-md bg-surface-hi border border-edge px-3 py-2 text-sm text-fg-2 w-full">
                      <input
                        type="checkbox"
                        checked={form.set_urgent}
                        onChange={(e) =>
                          setForm((prev) => ({ ...prev, set_urgent: e.target.checked }))
                        }
                        className="accent-amber-500"
                      />
                      Mark matching ops urgent
                    </label>
                  </div>
                )}
              </div>
            </fieldset>
          )}

          {/* Match conditions — all optional; blank means "don't care". */}
          <fieldset className="rounded-md border border-edge/70 p-3">
            <legend className="px-1 text-xs font-medium text-fg-3">
              Match conditions <span className="text-fg-3/70">— leave blank to ignore</span>
            </legend>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">Sender pattern</label>
                <input
                  value={form.sender_pattern}
                  onChange={(e) => setForm((prev) => ({ ...prev, sender_pattern: e.target.value }))}
                  placeholder="*@newsletter.com"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">Source folder</label>
                <input
                  value={form.folder_from}
                  onChange={(e) => setForm((prev) => ({ ...prev, folder_from: e.target.value }))}
                  placeholder="INBOX"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">Destination folder</label>
                <input
                  value={form.folder_to}
                  onChange={(e) => setForm((prev) => ({ ...prev, folder_to: e.target.value }))}
                  placeholder="Archive"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">
                  Recipient pattern <span className="text-fg-3/70">(sends)</span>
                </label>
                <input
                  value={form.recipient_pattern}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, recipient_pattern: e.target.value }))
                  }
                  placeholder="*@mycompany.com"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">
                  Subject pattern <span className="text-fg-3/70">(sends)</span>
                </label>
                <input
                  value={form.subject_pattern}
                  onChange={(e) => setForm((prev) => ({ ...prev, subject_pattern: e.target.value }))}
                  placeholder="*invoice*"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">
                  Flag pattern <span className="text-fg-3/70">(flag changes)</span>
                </label>
                <input
                  value={form.flag_pattern}
                  onChange={(e) => setForm((prev) => ({ ...prev, flag_pattern: e.target.value }))}
                  placeholder="\Deleted"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">Min messages</label>
                <input
                  type="number"
                  min={0}
                  value={form.min_message_count}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, min_message_count: e.target.value }))
                  }
                  placeholder="0"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">Max messages</label>
                <input
                  type="number"
                  min={0}
                  value={form.max_message_count}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, max_message_count: e.target.value }))
                  }
                  placeholder="25"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-fg-3 mb-1">Agent</label>
                <select
                  value={form.agent_id}
                  onChange={(e) => setForm((prev) => ({ ...prev, agent_id: e.target.value }))}
                  className={inputClass}
                >
                  <option value="">Any agent</option>
                  {agentOptions.map((a) => (
                    <option key={a.id} value={String(a.id)}>
                      {a.label || a.agent_username}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </fieldset>

          {/* Guardrails — safety controls (Tier 3). */}
          <fieldset className="rounded-md border border-edge/70 p-3">
            <legend className="px-1 text-xs font-medium text-fg-3">Guardrails</legend>
            <div className="space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="inline-flex items-start gap-2 rounded-md bg-surface-hi border border-edge px-3 py-2 text-sm text-fg-2">
                  <input
                    type="checkbox"
                    checked={form.is_guardrail}
                    onChange={(e) => setForm((prev) => ({ ...prev, is_guardrail: e.target.checked }))}
                    className="mt-0.5 accent-rose-500"
                  />
                  <span>
                    Guardrail
                    <span className="block text-xs text-fg-3/70">
                      Evaluated before all normal rules; wins regardless of priority. Use to make a
                      safety rule unbeatable.
                    </span>
                  </span>
                </label>
                <label className="inline-flex items-start gap-2 rounded-md bg-surface-hi border border-edge px-3 py-2 text-sm text-fg-2">
                  <input
                    type="checkbox"
                    checked={form.shadow}
                    onChange={(e) => setForm((prev) => ({ ...prev, shadow: e.target.checked }))}
                    className="mt-0.5 accent-violet-500"
                  />
                  <span>
                    Shadow mode
                    <span className="block text-xs text-fg-3/70">
                      Logs what it would do without acting — measure a rule's impact before
                      enabling it for real.
                    </span>
                  </span>
                </label>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs font-medium text-fg-3 mb-1">Active from</label>
                  <input
                    type="time"
                    value={form.active_start}
                    onChange={(e) => setForm((prev) => ({ ...prev, active_start: e.target.value }))}
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-fg-3 mb-1">Active until</label>
                  <input
                    type="time"
                    value={form.active_end}
                    onChange={(e) => setForm((prev) => ({ ...prev, active_end: e.target.value }))}
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-fg-3 mb-1">Timezone</label>
                  <input
                    value={form.active_tz}
                    onChange={(e) => setForm((prev) => ({ ...prev, active_tz: e.target.value }))}
                    placeholder="UTC"
                    className={inputClass}
                  />
                </div>
              </div>
              <p className="text-xs text-fg-3/70">
                Leave the times blank for always-on. A window only auto-decides during those hours
                (e.g. block 3am anomalies); outside it, ops fall to manual review.
              </p>
            </div>
          </fieldset>

          <div>
            <label className="block text-xs font-medium text-fg-3 mb-1">Description</label>
            <input
              value={form.description}
              onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
              placeholder="Auto-approve mark_read from newsletters"
              required
              className={inputClass}
            />
          </div>
          <div className="rounded-md border border-indigo-800/60 bg-indigo-900/20 px-3 py-2 text-sm text-indigo-100">
            {previewText}
          </div>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium bg-accent hover:bg-accent-hi text-white transition-colors disabled:opacity-50"
          >
            <Plus className="w-3.5 h-3.5" />
            {createMutation.isPending ? 'Adding…' : 'Add rule'}
          </button>
        </form>
      </section>

      {/* Rule test panel */}
      <section className="rounded-lg border border-edge bg-surface/40 p-4">
        <div className="flex items-center gap-2 mb-4">
          <FlaskConical className="w-4 h-4 text-amber-400" />
          <h2 className="text-sm font-semibold text-fg">Test rules</h2>
          <span className="text-xs text-fg-3 ml-1">
            — dry-run a sample operation against your ruleset
          </span>
        </div>
        <form onSubmit={(e) => void handleTestRule(e)} className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">
                Op type <span className="text-red-400">*</span>
              </label>
              <select
                value={testForm.op_type}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, op_type: e.target.value }))
                  setTestResult(null)
                }}
                className={inputClass}
              >
                <option value="">— select —</option>
                {OP_TYPE_OPTIONS.filter((o) => o.value).map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Sender</label>
              <input
                value={testForm.sender}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, sender: e.target.value }))
                  setTestResult(null)
                }}
                placeholder="digest@example.com"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Source folder</label>
              <input
                value={testForm.folder_from}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, folder_from: e.target.value }))
                  setTestResult(null)
                }}
                placeholder="INBOX"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Destination folder</label>
              <input
                value={testForm.folder_to}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, folder_to: e.target.value }))
                  setTestResult(null)
                }}
                placeholder="Archive"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Recipient</label>
              <input
                value={testForm.recipient}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, recipient: e.target.value }))
                  setTestResult(null)
                }}
                placeholder="bob@mycompany.com"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Subject</label>
              <input
                value={testForm.subject}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, subject: e.target.value }))
                  setTestResult(null)
                }}
                placeholder="Your invoice for May"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Flag</label>
              <input
                value={testForm.flag}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, flag: e.target.value }))
                  setTestResult(null)
                }}
                placeholder="\Deleted"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Message count</label>
              <input
                type="number"
                min={0}
                value={testForm.message_count}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, message_count: e.target.value }))
                  setTestResult(null)
                }}
                placeholder="3"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-fg-3 mb-1">Agent</label>
              <select
                value={testForm.agent_id}
                onChange={(e) => {
                  setTestForm((p) => ({ ...p, agent_id: e.target.value }))
                  setTestResult(null)
                }}
                className={inputClass}
              >
                <option value="">Any agent</option>
                {agentOptions.map((a) => (
                  <option key={a.id} value={String(a.id)}>
                    {a.label || a.agent_username}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={testPending || !testForm.op_type}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium bg-amber-700 hover:bg-amber-600 text-white transition-colors disabled:opacity-50"
            >
              <FlaskConical className="w-3.5 h-3.5" />
              {testPending ? 'Testing…' : 'Test'}
            </button>
          </div>
          {testResult !== null && (
            <div className="space-y-2">
              <div
                className={`px-3 py-2 rounded-md text-sm font-medium border ${
                  !testResult.matched
                    ? 'border-edge bg-surface-hi/60 text-fg-2'
                    : testResult.action === 'reject'
                      ? 'border-red-700 bg-red-900/30 text-red-200'
                      : 'border-emerald-700 bg-emerald-900/30 text-emerald-200'
                }`}
              >
                {testHeadline(testResult)}
              </div>
              {testResult.matches && testResult.matches.length > 0 && (
                <div className="rounded-md border border-edge bg-surface-hi/30 divide-y divide-edge/60">
                  <div className="px-3 py-1.5 text-xs text-fg-3">
                    {testResult.matches.length} rule
                    {testResult.matches.length === 1 ? '' : 's'} match in evaluation order
                  </div>
                  {testResult.matches.map((m) => (
                    <div
                      key={m.rule_id}
                      className={`flex items-center gap-2 px-3 py-1.5 text-xs ${
                        m.selected ? 'bg-emerald-900/15' : ''
                      }`}
                    >
                      <span className={m.selected ? 'text-emerald-300' : 'text-fg-3'}>
                        {m.selected ? '▶ wins' : '—'}
                      </span>
                      <span className="font-medium text-fg-2 whitespace-nowrap">{m.action}</span>
                      <span className="text-fg-3 truncate flex-1">{m.description}</span>
                      {m.is_guardrail && (
                        <span className="px-1.5 py-0.5 rounded bg-rose-900/60 text-rose-200">
                          guardrail
                        </span>
                      )}
                      {m.shadow && (
                        <span className="px-1.5 py-0.5 rounded bg-violet-900/60 text-violet-200">
                          shadow
                        </span>
                      )}
                      {m.active_now === false && (
                        <span className="px-1.5 py-0.5 rounded bg-surface-hi text-fg-3 border border-edge/60">
                          inactive now
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </form>
      </section>

      <section className="rounded-lg border border-edge bg-surface/30 overflow-hidden">
        <div className="px-4 py-3 border-b border-edge flex items-center justify-between">
          <h2 className="text-sm font-semibold text-fg">Rules</h2>
          <span className="text-xs text-fg-3">{rules.length} total</span>
        </div>

        {isLoading && (
          <div className="p-4 space-y-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-10 rounded bg-surface animate-pulse" />
            ))}
          </div>
        )}

        {isError && (
          <div className="p-4 text-sm text-red-400">
            Failed to load rules: {error instanceof Error ? error.message : 'Unknown error'}
          </div>
        )}

        {!isLoading && !isError && rules.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-fg-3 gap-4">
            <Inbox className="w-10 h-10 opacity-40" />
            <p className="text-sm">No auto-approval rules yet. Add one to reduce approval noise.</p>
          </div>
        )}

        {!isLoading && !isError && rules.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-edge text-xs text-fg-3 uppercase tracking-wide">
                  <th className="px-3 py-2">Priority</th>
                  <th className="px-3 py-2">Description</th>
                  <th className="px-3 py-2">Op type</th>
                  <th className="px-3 py-2">Conditions</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Hits</th>
                  <th className="px-3 py-2">Enabled</th>
                  <th className="px-3 py-2 text-right">Controls</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule, index) => {
                  const isUpdating = updatingRuleId === rule.id
                  const isDeleting = deletingRuleId === rule.id
                  const conditions = ruleConditions(rule, agentLabel)
                  return (
                    <tr
                      key={rule.id}
                      className="border-b border-edge/60 last:border-b-0 hover:bg-surface/50"
                    >
                      <td className="px-3 py-2 text-fg-2">{rule.priority}</td>
                      <td className="px-3 py-2 text-fg">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          {rule.is_guardrail && (
                            <span
                              className="inline-flex px-1.5 py-0.5 rounded text-xs font-medium bg-rose-900/70 text-rose-200"
                              title="Guardrail — evaluated first, wins over normal rules"
                            >
                              guardrail
                            </span>
                          )}
                          {rule.shadow && (
                            <span
                              className="inline-flex px-1.5 py-0.5 rounded text-xs font-medium bg-violet-900/70 text-violet-200"
                              title="Shadow — logged only, never acts"
                            >
                              shadow
                            </span>
                          )}
                          <span>{rule.description}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-fg-2">{rule.op_type ?? 'any'}</td>
                      <td className="px-3 py-2">
                        {conditions.length === 0 ? (
                          <span className="text-fg-3">any</span>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {conditions.map((c) => (
                              <span
                                key={c}
                                className="inline-flex px-1.5 py-0.5 rounded text-xs bg-surface-hi text-fg-2 border border-edge/60 whitespace-nowrap"
                              >
                                {c}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-flex px-2 py-1 rounded text-xs font-medium whitespace-nowrap ${
                            ACTION_BADGE[rule.action] ?? 'bg-surface-hi text-fg-2'
                          }`}
                        >
                          {actionLabel(rule)}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-col gap-0.5">
                          {rule.shadow ? (
                            <span
                              className="inline-flex w-fit px-2 py-1 rounded text-xs font-medium bg-violet-900/50 text-violet-200"
                              title="Operations this shadow rule would have acted on"
                            >
                              {rule.shadow_hits ?? 0} shadow
                            </span>
                          ) : (
                            <span
                              className="inline-flex w-fit px-2 py-1 rounded text-xs font-medium bg-surface-hi text-fg-2"
                              title="Operations auto-decided by this rule"
                            >
                              {rule.hits}
                            </span>
                          )}
                          {rule.last_fired_at != null && (
                            <span
                              className="text-xs text-fg-3 whitespace-nowrap"
                              title={`Last fired ${new Date(rule.last_fired_at * 1000).toLocaleString()}`}
                            >
                              {formatDistanceToNow(new Date(rule.last_fired_at * 1000), {
                                addSuffix: true,
                              })}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <button
                          onClick={() => void handleToggleRule(rule)}
                          disabled={isUpdating}
                          className={`inline-flex items-center px-2 py-1 rounded text-xs font-medium transition-colors ${
                            rule.enabled
                              ? 'bg-emerald-900/70 text-emerald-200 hover:bg-emerald-800/80'
                              : 'bg-surface-hi text-fg-2 hover:bg-edge'
                          } disabled:opacity-50`}
                        >
                          {rule.enabled ? 'On' : 'Off'}
                        </button>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => void handleMoveRule(index, 'up')}
                            disabled={index === 0 || isUpdating}
                            className="p-1 rounded border border-edge text-fg-2 hover:bg-surface-hi disabled:opacity-30"
                            title="Move rule up"
                          >
                            <ArrowUp className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => void handleMoveRule(index, 'down')}
                            disabled={index === rules.length - 1 || isUpdating}
                            className="p-1 rounded border border-edge text-fg-2 hover:bg-surface-hi disabled:opacity-30"
                            title="Move rule down"
                          >
                            <ArrowDown className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => void handleDeleteRule(rule.id)}
                            disabled={isDeleting}
                            className="p-1 rounded border border-edge text-fg-2 hover:bg-red-900/50 hover:text-red-200 disabled:opacity-50"
                            title="Delete rule"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
