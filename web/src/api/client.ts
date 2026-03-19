import type { AuditListResponse, Operation, OperationsResponse, DecisionResponse } from '../types'

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options)
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

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
  const res = await fetch(`${BASE}/api/v1/audit/export`)
  if (!res.ok) throw new Error(`Export failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'nuvrail-audit.json'
  a.click()
  URL.revokeObjectURL(url)
}
