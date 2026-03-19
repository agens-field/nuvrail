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
  message_ids?: string[]
  folder_from?: string
  folder_to?: string
  flags_add?: string[]
  flags_remove?: string[]
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
