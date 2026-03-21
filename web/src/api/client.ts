import type {
  AuditListResponse,
  BatchApproveResponse,
  BatchRejectResponse,
  DecisionResponse,
  Operation,
  OperationsResponse,
} from '../types'

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'

// ---------------------------------------------------------------------------
// Auth token helpers
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'nuvrail_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

// ---------------------------------------------------------------------------
// Core fetch wrapper — injects Bearer token and handles 401
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string> | undefined),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${BASE}${path}`, { ...options, headers })

  if (res.status === 401) {
    // Token expired or invalid — clear and redirect to login
    clearToken()
    window.location.hash = '#/login'
    throw new Error('Session expired — please log in again')
  }

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Auth endpoints
// ---------------------------------------------------------------------------

export interface RegisterRequest {
  email: string
  password: string
  display_name?: string
}

export interface RegisterResponse {
  user_id: number
  email: string
  display_name?: string
  created_at: number
}

export interface LoginResponse {
  token: string
  token_type: string
  user_id: number
  email: string
}

export interface MeResponse {
  user_id: number
  email: string
  display_name?: string
  created_at: number
}

export async function registerUser(body: RegisterRequest): Promise<RegisterResponse> {
  const res = await fetch(`${BASE}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Register ${res.status}: ${text}`)
  }
  return res.json()
}

export async function loginUser(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Login ${res.status}: ${text}`)
  }
  return res.json()
}

export async function fetchMe(): Promise<MeResponse> {
  return apiFetch<MeResponse>('/api/v1/auth/me')
}

// ---------------------------------------------------------------------------
// Agent credential endpoints
// ---------------------------------------------------------------------------

export interface AgentCreateRequest {
  label?: string
  upstream_host: string
  upstream_imap_port?: number
  upstream_smtp_port?: number
  upstream_user: string
  upstream_password: string
}

export interface AgentCreateResponse {
  id: number
  agent_username: string
  agent_token: string // SHOWN ONCE
  label: string
  upstream_host: string
  upstream_user: string
}

export interface AgentResponse {
  id: number
  agent_username: string
  label: string
  upstream_host: string
  upstream_user: string
  created_at: number
  revoked_at?: number | null
}

export async function createAgent(body: AgentCreateRequest): Promise<AgentCreateResponse> {
  return apiFetch<AgentCreateResponse>('/api/v1/agents', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function fetchAgents(): Promise<AgentResponse[]> {
  return apiFetch<AgentResponse[]>('/api/v1/agents')
}

export async function revokeAgent(id: number): Promise<void> {
  await apiFetch<void>(`/api/v1/agents/${id}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Operations endpoints
// ---------------------------------------------------------------------------

export async function fetchOperations(status?: string): Promise<OperationsResponse> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : ''
  return apiFetch<OperationsResponse>(`/api/v1/operations${qs}`)
}

export async function fetchOperation(id: string): Promise<Operation> {
  return apiFetch<Operation>(`/api/v1/operations/${encodeURIComponent(id)}`)
}

export async function approveOperation(id: string): Promise<DecisionResponse> {
  return apiFetch<DecisionResponse>(`/api/v1/operations/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
  })
}

export async function rejectOperation(id: string): Promise<DecisionResponse> {
  return apiFetch<DecisionResponse>(`/api/v1/operations/${encodeURIComponent(id)}/reject`, {
    method: 'POST',
  })
}

export async function batchApproveOperations(ids: string[]): Promise<BatchApproveResponse> {
  return apiFetch<BatchApproveResponse>('/api/v1/operations/batch/approve', {
    method: 'POST',
    body: JSON.stringify({ operation_ids: ids }),
  })
}

export async function batchRejectOperations(ids: string[]): Promise<BatchRejectResponse> {
  return apiFetch<BatchRejectResponse>('/api/v1/operations/batch/reject', {
    method: 'POST',
    body: JSON.stringify({ operation_ids: ids }),
  })
}

// ---------------------------------------------------------------------------
// Audit endpoints
// ---------------------------------------------------------------------------

export async function fetchAuditLog(
  params: { limit?: number; offset?: number; event?: string; actor?: string } = {}
): Promise<AuditListResponse> {
  const qs = new URLSearchParams()
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  if (params.offset !== undefined) qs.set('offset', String(params.offset))
  if (params.event) qs.set('event', params.event)
  if (params.actor) qs.set('actor', params.actor)
  const q = qs.toString()
  return apiFetch<AuditListResponse>(`/api/v1/audit${q ? `?${q}` : ''}`)
}

export async function exportAuditLog(): Promise<void> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${BASE}/api/v1/audit/export`, { headers })
  if (!res.ok) throw new Error(`Export failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'nuvrail-audit.json'
  a.click()
  URL.revokeObjectURL(url)
}
