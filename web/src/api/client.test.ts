/**
 * Network-vs-HTTP error handling in the API client (GH #140).
 *
 * The core fetch wrapper (apiFetch) and the other user-facing fetch sites must
 * tell two failure modes apart:
 *
 *   - CONNECTION FAILURE — fetch() itself rejects with a TypeError because the
 *     request never reached the server (API down, wrong VITE_API_URL/baked-in
 *     localhost, mixed content, CORS). This must surface as a typed
 *     NetworkError whose message names the target BASE and the likely cause,
 *     NOT the browser's bare "Failed to fetch" / "Load failed".
 *
 *   - HTTP ERROR — the server actually answered with a 4xx/5xx. This must be
 *     left exactly as it was: an `API <status>: ...` Error, never remapped to
 *     a NetworkError (that would hide a real server rejection like a 403).
 *
 * We mock global.fetch so no real network is involved and assert the thrown
 * shape for each path.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { NetworkError, fetchOrThrow, loginUser } from './client'

// The client reads VITE_API_URL at module-load; the default is localhost:8080.
const BASE = 'http://localhost:8080'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('fetchOrThrow (GH #140)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('remaps a connection-level TypeError to a NetworkError naming BASE', async () => {
    // fetch() rejecting with a TypeError is the Fetch-spec shape for "the
    // request never reached the server".
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    )

    await expect(fetchOrThrow(`${BASE}/api/v1/anything`)).rejects.toBeInstanceOf(
      NetworkError,
    )

    // Re-run to inspect the message/fields (the rejection above is consumed).
    let caught: unknown
    try {
      await fetchOrThrow(`${BASE}/api/v1/anything`)
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(NetworkError)
    const netErr = caught as NetworkError
    expect(netErr.message).toContain(BASE)
    expect(netErr.message).toContain("Couldn't reach the Nuvrail API")
    expect(netErr.message).toMatch(/VITE_API_URL/)
    expect(netErr.targetUrl).toBe(BASE)
    // The original rejection is preserved for debugging.
    expect(netErr.cause).toBeInstanceOf(TypeError)
    // It must NOT surface the bare browser string on its own.
    expect(netErr.message).not.toBe('Failed to fetch')
  })

  it('returns a resolved Response unchanged even when it is an HTTP error', async () => {
    // An HTTP error is a real answer from the server, not a network failure —
    // fetchOrThrow must hand the Response back so callers do status handling.
    const res403 = jsonResponse(403, { error: 'registration_closed' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res403))

    const res = await fetchOrThrow(`${BASE}/api/v1/auth/register`)
    expect(res.status).toBe(403)
    expect(res).toBe(res403)
  })

  it('rethrows a non-TypeError rejection untouched (does not mask real bugs)', async () => {
    const boom = new Error('some other bug')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(boom))

    await expect(fetchOrThrow(`${BASE}/x`)).rejects.toBe(boom)
    await expect(fetchOrThrow(`${BASE}/x`)).rejects.not.toBeInstanceOf(
      NetworkError,
    )
  })
})

describe('loginUser() network vs HTTP error (GH #140)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('a network failure produces the actionable NetworkError, not "Load failed"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Load failed')),
    )

    let caught: unknown
    try {
      await loginUser('a@b.com', 'pw')
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(NetworkError)
    expect((caught as NetworkError).message).toContain(BASE)
  })

  it('an HTTP 403 stays a plain API error, unchanged (not remapped)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(403, { error: 'invalid_credentials' })),
    )

    let caught: unknown
    try {
      await loginUser('a@b.com', 'wrong')
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(Error)
    expect(caught).not.toBeInstanceOf(NetworkError)
    expect((caught as Error).message).toContain('403')
  })
})
