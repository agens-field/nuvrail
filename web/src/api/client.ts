import type { Operation, OperationsResponse, DecisionResponse } from '../types'

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
