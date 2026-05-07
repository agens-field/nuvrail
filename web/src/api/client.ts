import type {
  AutoApprovalRule,
  AutoApprovalRuleCreateRequest,
  AutoApprovalRuleUpdateRequest,
  AuditListResponse,
  BatchApproveResponse,
  BatchRejectResponse,
  DecisionResponse,
  Operation,
  OperationsResponse,
  PushSubscribeResponse,
  VapidKeyResponse,
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

async function apiFetch<T>(path: string, options?: RequestInit & { skipRedirectOn401?: boolean }): Promise<T> {
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
    // Token expired or invalid — clear local token.
    // By default, redirect to login. Pass skipRedirectOn401 to suppress the
    // redirect (e.g. OAuth callback polling, where a redirect would destroy
    // the credential display before the user sees it).
    clearToken()
    if (!options?.skipRedirectOn401) {
      window.location.hash = '#/login'
    }
    throw new Error('Session expired — please log in again')
  }

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    let parsed: { error?: unknown; detail?: unknown } | null = null
    try {
      parsed = JSON.parse(text) as { error?: unknown; detail?: unknown }
    } catch {
      parsed = null
    }
    const error = parsed && typeof parsed.error === 'string' ? parsed.error : undefined
    const detail = parsed && typeof parsed.detail === 'string' ? parsed.detail : undefined
    if (error || detail) {
      throw new Error(`API ${res.status}${error ? ` ${error}` : ''}: ${detail ?? text}`)
    }
    throw new Error(`API ${res.status}: ${text}`)
  }

  // Some successful endpoints intentionally return 204 No Content.
  if (res.status === 204) {
    return undefined as T
  }

  const bodyText = await res.text()
  if (!bodyText.trim()) {
    return undefined as T
  }

  try {
    return JSON.parse(bodyText) as T
  } catch {
    throw new Error(`API returned invalid JSON for ${path}`)
  }
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
// OAuth2 Gmail flow
// ---------------------------------------------------------------------------

export interface OAuthStartResponse {
  auth_url: string
  state: string
}

export interface OAuthResultResponse {
  agent_username: string
  agent_token: string
  label: string
  upstream_user: string
}

export async function startGmailOAuth(label?: string): Promise<OAuthStartResponse> {
  const qs = label ? `?label=${encodeURIComponent(label)}` : ''
  return apiFetch<OAuthStartResponse>(`/api/v1/oauth2/google/start${qs}`)
}

export async function getOAuthResult(state: string): Promise<OAuthResultResponse> {
  // skipRedirectOn401: a 401 here must NOT force-navigate away from the
  // callback page — that would destroy the credential display before the
  // user has a chance to copy their token.
  return apiFetch<OAuthResultResponse>(
    `/api/v1/oauth2/google/result?state=${encodeURIComponent(state)}`,
    { skipRedirectOn401: true },
  )
}

// ---------------------------------------------------------------------------
// Operations endpoints
// ---------------------------------------------------------------------------

export async function fetchOperations(status?: string, agentId?: number): Promise<OperationsResponse> {
  const qs = new URLSearchParams()
  if (status) qs.set('status', status)
  if (agentId !== undefined) qs.set('agent_id', String(agentId))
  const q = qs.toString()
  return apiFetch<OperationsResponse>(`/api/v1/operations${q ? `?${q}` : ''}`)
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
  params: { limit?: number; offset?: number; event?: string; actor?: string; agent_id?: number } = {}
): Promise<AuditListResponse> {
  const qs = new URLSearchParams()
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  if (params.offset !== undefined) qs.set('offset', String(params.offset))
  if (params.event) qs.set('event', params.event)
  if (params.actor) qs.set('actor', params.actor)
  if (params.agent_id !== undefined) qs.set('agent_id', String(params.agent_id))
  const q = qs.toString()
  return apiFetch<AuditListResponse>(`/api/v1/audit${q ? `?${q}` : ''}`)
}

export async function exportAuditLog(agentId?: number): Promise<void> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const qs = new URLSearchParams()
  if (agentId !== undefined) qs.set('agent_id', String(agentId))
  const res = await fetch(`${BASE}/api/v1/audit/export${qs.toString() ? `?${qs.toString()}` : ''}`, { headers })
  if (!res.ok) throw new Error(`Export failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'nuvrail-audit.json'
  a.click()
  URL.revokeObjectURL(url)
}

// ---------------------------------------------------------------------------
// Auto-approval rule endpoints
// ---------------------------------------------------------------------------

export async function fetchRules(): Promise<AutoApprovalRule[]> {
  return apiFetch<AutoApprovalRule[]>('/api/v1/rules')
}

export async function createRule(body: AutoApprovalRuleCreateRequest): Promise<AutoApprovalRule> {
  return apiFetch<AutoApprovalRule>('/api/v1/rules', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function updateRule(
  id: number,
  body: AutoApprovalRuleUpdateRequest
): Promise<AutoApprovalRule> {
  return apiFetch<AutoApprovalRule>(`/api/v1/rules/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function deleteRule(id: number): Promise<void> {
  await apiFetch<void>(`/api/v1/rules/${id}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Web Push
// ---------------------------------------------------------------------------

export async function fetchVapidKey(): Promise<VapidKeyResponse> {
  return apiFetch<VapidKeyResponse>('/api/v1/push/vapid-key')
}

export async function registerPushSubscription(
  subscription: PushSubscriptionJSON
): Promise<PushSubscribeResponse> {
  const { endpoint, keys } = subscription
  if (!endpoint || !keys?.p256dh || !keys?.auth) {
    throw new Error('Invalid push subscription: missing endpoint or keys')
  }
  return apiFetch<PushSubscribeResponse>('/api/v1/push/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint, p256dh: keys.p256dh, auth: keys.auth }),
  })
}

/**
 * Request notification permission, subscribe to push, and register with the API.
 * Returns true if subscription was established, false if permission denied or unavailable.
 */
export async function setupPushNotifications(): Promise<boolean> {
  if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) {
    console.warn('[push] Web Push not supported in this browser')
    return false
  }

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    console.info('[push] Notification permission denied')
    return false
  }

  try {
    const { public_key } = await fetchVapidKey()

    // Convert base64url to Uint8Array for applicationServerKey
    const padding = '='.repeat((4 - (public_key.length % 4)) % 4)
    const base64 = (public_key + padding).replace(/-/g, '+').replace(/_/g, '/')
    const rawKey = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))

    const reg = await navigator.serviceWorker.ready
    const subscription = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: rawKey,
    })

    await registerPushSubscription(subscription.toJSON())
    console.info('[push] Push subscription registered')
    return true
  } catch (err) {
    console.error('[push] Failed to set up push notifications:', err)
    return false
  }
}
