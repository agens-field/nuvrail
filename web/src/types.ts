export interface MessagePreview {
  uid: number
  sender?: string | null
  subject?: string | null
  date_sent?: number | null
}

export interface SmtpEnvelope {
  from: string
  to: string[]
  subject: string
  body_preview: string
}

export interface Operation {
  id: string
  status: string
  op_type: string
  protocol: string
  description: string
  created_at: number
  expires_at: number
  decided_at?: number
  scheduled_execute_at?: number  // cool-down deadline (approve_after); auto-executes when passed
  imap_command?: string
  smtp_envelope?: SmtpEnvelope
  message_previews?: MessagePreview[]
  message_ids?: string[]
  folder_from?: string
  folder_to?: string
  flags_add?: string[]
  flags_remove?: string[]
  is_urgent?: number   // 1 = urgent (smtp_send, trash); show at top with red styling
  error?: string
  batch_id?: string | null
  // Semantic intent derived at staging time: 'archive' | 'delete' | 'mark_spam'
  // | 'not_spam' | 'restore_from_trash' | 'unarchive' | 'mark_answered'
  // | 'save_draft' | 'import_message'. Null = op_type says everything.
  intent_label?: string | null
  intent_confidence?: number | null  // 1.0 = provider match, 0.8 = folder-name heuristic
}

export interface OperationsResponse {
  operations: Operation[]
  total: number
}

export interface DecisionResponse {
  id: string
  status: string
}

export interface BatchApproveResult {
  id: string
  status: string
  executed_at?: number
  error?: string
}

export interface BatchApproveResponse {
  approved: BatchApproveResult[]
  failed: BatchApproveResult[]
  skipped: BatchApproveResult[]
  total: number
}

export interface BatchRejectResult {
  id: string
  status: string
  error?: string
}

export interface BatchRejectResponse {
  rejected: BatchRejectResult[]
  failed: BatchRejectResult[]
  skipped: BatchRejectResult[]
  total: number
}

export interface AuditEntry {
  id: number
  timestamp: number
  operation_id?: string
  event: string
  actor?: string
  agent_id?: string
  agent_label?: string
  detail?: Record<string, unknown>
  // Joined from staged_operations
  op_description?: string
  op_type?: string
  op_protocol?: string
  op_status?: string
  undo_expires_at?: number | null
}

export interface UndoResponse {
  id: string
  status: string
  op_type: string
  reverted: string
}

export interface AuditListResponse {
  entries: AuditEntry[]
  total: number
  limit: number
  offset: number
}

export type AutoApprovalAction = 'approve' | 'reject' | 'approve_after' | 'hold'

export interface AutoApprovalRule {
  id: number
  enabled: boolean
  priority: number
  op_type?: string | null
  sender_pattern?: string | null
  folder_from?: string | null
  // Tier 1 predicates (null/absent = "don't care")
  folder_to?: string | null
  recipient_pattern?: string | null
  subject_pattern?: string | null
  flag_pattern?: string | null
  min_message_count?: number | null
  max_message_count?: number | null
  agent_id?: number | null
  // Tier 2 action parameters
  delay_seconds?: number | null        // cool-down for action 'approve_after'
  rate_limit_per_hour?: number | null  // cap an approving rule's hourly budget
  set_urgent?: number | null           // urgency override for 'hold'/'approve_after'
  // Tier 3 guardrails
  is_guardrail?: boolean       // evaluated first; wins over normal rules
  shadow?: boolean             // observational — logs but never acts
  active_start_min?: number | null  // active-window start (minutes since midnight)
  active_end_min?: number | null    // active-window end (minutes since midnight)
  active_tz?: string | null         // IANA tz for the window (default UTC)
  action: AutoApprovalAction
  description: string
  created_at: number
  hits: number          // ops this rule acted on (from audit log; excludes shadow)
  shadow_hits?: number  // ops a shadow rule would have acted on
  last_fired_at?: number | null  // unix ts this rule last acted (excludes shadow)
}

export interface RuleTestRequest {
  op_type: string
  sender?: string | null
  folder_from?: string | null
  folder_to?: string | null
  recipient?: string | null
  subject?: string | null
  flags_add?: string[] | null
  flags_remove?: string[] | null
  message_ids?: string[] | null
  agent_id?: number | null
}

export interface RuleTestMatch {
  rule_id: number
  description: string
  action: string
  is_guardrail?: boolean
  shadow?: boolean
  active_now?: boolean
  selected?: boolean
}

export interface RuleTestResponse {
  matched: boolean
  action?: string | null
  rule_id?: number | null
  rule_description?: string | null
  matches?: RuleTestMatch[]  // all predicate-matching rules, in evaluation order
}

export interface AutoApprovalRuleCreateRequest {
  enabled?: boolean
  priority?: number
  op_type?: string | null
  sender_pattern?: string | null
  folder_from?: string | null
  folder_to?: string | null
  recipient_pattern?: string | null
  subject_pattern?: string | null
  flag_pattern?: string | null
  min_message_count?: number | null
  max_message_count?: number | null
  agent_id?: number | null
  delay_seconds?: number | null
  rate_limit_per_hour?: number | null
  set_urgent?: number | null
  is_guardrail?: boolean
  shadow?: boolean
  active_start_min?: number | null
  active_end_min?: number | null
  active_tz?: string | null
  action: AutoApprovalAction
  description: string
}

export interface AutoApprovalRuleUpdateRequest {
  enabled?: boolean
  priority?: number
  op_type?: string | null
  sender_pattern?: string | null
  folder_from?: string | null
  folder_to?: string | null
  recipient_pattern?: string | null
  subject_pattern?: string | null
  flag_pattern?: string | null
  min_message_count?: number | null
  max_message_count?: number | null
  agent_id?: number | null
  delay_seconds?: number | null
  rate_limit_per_hour?: number | null
  set_urgent?: number | null
  is_guardrail?: boolean
  shadow?: boolean
  active_start_min?: number | null
  active_end_min?: number | null
  active_tz?: string | null
  action?: AutoApprovalAction
  description?: string
}

// Web Push
export interface VapidKeyResponse {
  public_key: string
}

export interface PushSubscribeResponse {
  subscribed: boolean
  endpoint: string
}
