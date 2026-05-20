import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ArrowDown, ArrowUp, FlaskConical, Inbox, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { createRule, deleteRule, fetchRules, testRule, updateRule } from '../api/client'
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

type RuleFormState = {
  op_type: string
  sender_pattern: string
  folder_from: string
  action: AutoApprovalAction
  description: string
}

const initialFormState: RuleFormState = {
  op_type: '',
  sender_pattern: '',
  folder_from: '',
  action: 'approve',
  description: '',
}

function describeSender(pattern: string): string {
  const trimmed = pattern.trim()
  if (!trimmed) return 'from any sender'
  if (trimmed.startsWith('*@')) {
    return `from addresses ending in ${trimmed.slice(1)}`
  }
  return `from senders matching "${trimmed}"`
}

function buildRulePreview(form: RuleFormState): string {
  const opTypeLabel = form.op_type ? form.op_type : 'any'
  const senderLabel = describeSender(form.sender_pattern)
  const folder = form.folder_from.trim()
  const folderLabel = folder ? ` in folder ${folder}` : ''
  return `This rule would match: ${opTypeLabel} ops ${senderLabel}${folderLabel}.`
}

function normalizeOptionalInput(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export default function RulesView() {
  const qc = useQueryClient()
  const [form, setForm] = useState<RuleFormState>(initialFormState)
  const [deletingRuleId, setDeletingRuleId] = useState<number | null>(null)
  const [updatingRuleId, setUpdatingRuleId] = useState<number | null>(null)

  // Rule test panel
  const [testForm, setTestForm] = useState({ op_type: '', sender: '', folder_from: '' })
  const [testResult, setTestResult] = useState<RuleTestResponse | null>(null)
  const [testPending, setTestPending] = useState(false)

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['rules'],
    queryFn: fetchRules,
  })

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

  const previewText = useMemo(() => buildRulePreview(form), [form])
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

    const maxPriority = rules.reduce((currentMax, rule) => Math.max(currentMax, rule.priority), 0)

    createMutation.mutate({
      action: form.action,
      description: form.description.trim(),
      priority: maxPriority + 1,
      op_type: normalizeOptionalInput(form.op_type),
      sender_pattern: normalizeOptionalInput(form.sender_pattern),
      folder_from: normalizeOptionalInput(form.folder_from),
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
    try {
      const result = await testRule({
        op_type: testForm.op_type.trim(),
        sender: testForm.sender.trim() || null,
        folder_from: testForm.folder_from.trim() || null,
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Auto-approval Rules</h1>
          <p className="text-sm text-slate-400 mt-1">
            Create filters to auto-approve or auto-reject predictable operations.
          </p>
        </div>
        <button
          onClick={() => void refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600 transition-colors disabled:opacity-50"
          title="Refresh rules"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      <section className="rounded-lg border border-slate-700 bg-slate-800/40 p-4">
        <div className="flex items-center gap-2 mb-4">
          <Plus className="w-4 h-4 text-indigo-400" />
          <h2 className="text-sm font-semibold text-slate-100">Add rule</h2>
        </div>
        <form onSubmit={(e) => void handleCreateRule(e)} className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Op type</label>
              <select
                value={form.op_type}
                onChange={(e) => setForm((prev) => ({ ...prev, op_type: e.target.value }))}
                className="w-full rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200"
              >
                {OP_TYPE_OPTIONS.map((option) => (
                  <option key={option.value || 'any'} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Action</label>
              <div className="flex items-center gap-4 rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200">
                <label className="inline-flex items-center gap-2">
                  <input
                    type="radio"
                    checked={form.action === 'approve'}
                    onChange={() => setForm((prev) => ({ ...prev, action: 'approve' }))}
                    className="accent-emerald-500"
                  />
                  Approve
                </label>
                <label className="inline-flex items-center gap-2">
                  <input
                    type="radio"
                    checked={form.action === 'reject'}
                    onChange={() => setForm((prev) => ({ ...prev, action: 'reject' }))}
                    className="accent-red-500"
                  />
                  Reject
                </label>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Sender pattern
              </label>
              <input
                value={form.sender_pattern}
                onChange={(e) => setForm((prev) => ({ ...prev, sender_pattern: e.target.value }))}
                placeholder="*@newsletter.com"
                className="w-full rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Source folder (optional)
              </label>
              <input
                value={form.folder_from}
                onChange={(e) => setForm((prev) => ({ ...prev, folder_from: e.target.value }))}
                placeholder="INBOX"
                className="w-full rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Description</label>
            <input
              value={form.description}
              onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
              placeholder="Auto-approve mark_read from newsletters"
              required
              className="w-full rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500"
            />
          </div>
          <div className="rounded-md border border-indigo-800/60 bg-indigo-900/20 px-3 py-2 text-sm text-indigo-100">
            {previewText}
          </div>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium bg-indigo-600 hover:bg-indigo-500 text-white transition-colors disabled:opacity-50"
          >
            <Plus className="w-3.5 h-3.5" />
            {createMutation.isPending ? 'Adding…' : 'Add rule'}
          </button>
        </form>
      </section>

      {/* Rule test panel */}
      <section className="rounded-lg border border-slate-700 bg-slate-800/40 p-4">
        <div className="flex items-center gap-2 mb-4">
          <FlaskConical className="w-4 h-4 text-amber-400" />
          <h2 className="text-sm font-semibold text-slate-100">Test rules</h2>
          <span className="text-xs text-slate-400 ml-1">— dry-run a sample operation against your ruleset</span>
        </div>
        <form onSubmit={(e) => void handleTestRule(e)} className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Op type <span className="text-red-400">*</span></label>
              <select
                value={testForm.op_type}
                onChange={(e) => { setTestForm((p) => ({ ...p, op_type: e.target.value })); setTestResult(null) }}
                className="w-full rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200"
              >
                <option value="">— select —</option>
                {OP_TYPE_OPTIONS.filter((o) => o.value).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Sender (optional)</label>
              <input
                value={testForm.sender}
                onChange={(e) => { setTestForm((p) => ({ ...p, sender: e.target.value })); setTestResult(null) }}
                placeholder="digest@example.com"
                className="w-full rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Source folder (optional)</label>
              <input
                value={testForm.folder_from}
                onChange={(e) => { setTestForm((p) => ({ ...p, folder_from: e.target.value })); setTestResult(null) }}
                placeholder="INBOX"
                className="w-full rounded-md bg-slate-700 border border-slate-600 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500"
              />
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
            {testResult !== null && (
              <div className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium border ${
                !testResult.matched
                  ? 'border-slate-600 bg-slate-700/60 text-slate-300'
                  : testResult.action === 'approve'
                  ? 'border-emerald-700 bg-emerald-900/30 text-emerald-200'
                  : 'border-red-700 bg-red-900/30 text-red-200'
              }`}>
                {!testResult.matched && '⚪ No rule matched — op would be queued for review'}
                {testResult.matched && testResult.action === 'approve' && `✅ Would be auto-approved by: "${testResult.rule_description}"`}
                {testResult.matched && testResult.action === 'reject' && `🚫 Would be auto-rejected by: "${testResult.rule_description}"`}
              </div>
            )}
          </div>
        </form>
      </section>

      <section className="rounded-lg border border-slate-700 bg-slate-800/30 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-100">Rules</h2>
          <span className="text-xs text-slate-400">
            {rules.length} total
          </span>
        </div>

        {isLoading && (
          <div className="p-4 space-y-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-10 rounded bg-slate-800 animate-pulse" />
            ))}
          </div>
        )}

        {isError && (
          <div className="p-4 text-sm text-red-400">
            Failed to load rules: {error instanceof Error ? error.message : 'Unknown error'}
          </div>
        )}

        {!isLoading && !isError && rules.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-slate-500 gap-4">
            <Inbox className="w-10 h-10 opacity-40" />
            <p className="text-sm">
              No auto-approval rules yet. Add one to reduce approval noise.
            </p>
          </div>
        )}

        {!isLoading && !isError && rules.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-xs text-slate-400 uppercase tracking-wide">
                  <th className="px-3 py-2">Priority</th>
                  <th className="px-3 py-2">Description</th>
                  <th className="px-3 py-2">Op type</th>
                  <th className="px-3 py-2">Sender pattern</th>
                  <th className="px-3 py-2">Folder</th>
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
                  return (
                    <tr
                      key={rule.id}
                      className="border-b border-slate-700/60 last:border-b-0 hover:bg-slate-800/50"
                    >
                      <td className="px-3 py-2 text-slate-300">{rule.priority}</td>
                      <td className="px-3 py-2 text-slate-100">{rule.description}</td>
                      <td className="px-3 py-2 text-slate-300">{rule.op_type ?? 'any'}</td>
                      <td className="px-3 py-2 text-slate-300">{rule.sender_pattern ?? 'any'}</td>
                      <td className="px-3 py-2 text-slate-300">{rule.folder_from ?? 'any'}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-flex px-2 py-1 rounded text-xs font-medium ${
                            rule.action === 'approve'
                              ? 'bg-emerald-900/70 text-emerald-200'
                              : 'bg-red-900/70 text-red-200'
                          }`}
                        >
                          {rule.action}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className="inline-flex px-2 py-1 rounded text-xs font-medium bg-slate-700 text-slate-300"
                          title="Operations auto-decided by this rule"
                        >
                          {rule.hits}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <button
                          onClick={() => void handleToggleRule(rule)}
                          disabled={isUpdating}
                          className={`inline-flex items-center px-2 py-1 rounded text-xs font-medium transition-colors ${
                            rule.enabled
                              ? 'bg-emerald-900/70 text-emerald-200 hover:bg-emerald-800/80'
                              : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
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
                            className="p-1 rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-30"
                            title="Move rule up"
                          >
                            <ArrowUp className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => void handleMoveRule(index, 'down')}
                            disabled={index === rules.length - 1 || isUpdating}
                            className="p-1 rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-30"
                            title="Move rule down"
                          >
                            <ArrowDown className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => void handleDeleteRule(rule.id)}
                            disabled={isDeleting}
                            className="p-1 rounded border border-slate-600 text-slate-300 hover:bg-red-900/50 hover:text-red-200 disabled:opacity-50"
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
