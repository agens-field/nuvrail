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
}

export interface AuditListResponse {
  entries: AuditEntry[]
  total: number
  limit: number
  offset: number
}

// Web Push
export interface VapidKeyResponse {
  public_key: string
}

export interface PushSubscribeResponse {
  subscribed: boolean
  endpoint: string
}
