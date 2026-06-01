/**
 * OAuthCallbackView — lands here after Google OAuth2 consent.
 *
 * URL: /#/oauth2/callback?state=<nonce>
 *
 * On mount, reads ?state from the URL and polls GET /api/v1/oauth2/google/result.
 * Retries up to 10 times with 1s delay while the backend is still processing
 * (404 = not ready yet).
 *
 * Result is cached in sessionStorage under OAUTH_RESULT_KEY so a page
 * reload does not lose the credentials before the user copies them.
 * The cache is cleared when the user navigates to /agents via "Done".
 *
 * Shows:
 *   - Spinner while polling
 *   - Credential card (same style as SetupView) on success
 *   - Error message with retry link on failure
 */
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Mail, CheckCircle } from 'lucide-react'
import { getOAuthResult } from '../api/client'
import type { OAuthResultResponse } from '../api/client'

const PROXY_HOST = import.meta.env.VITE_PROXY_HOST ?? 'nuvrail.example.com'
const MAX_POLLS = 10
const POLL_INTERVAL_MS = 1000
const OAUTH_RESULT_KEY = 'nuvrail_oauth_result'

// Maps raw server/Google error codes to human-readable messages.
const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  no_code:
    'Google did not return an authorization code. The request may have been cancelled or denied. Please try again.',
  access_denied:
    'Access was denied by Google. Please try connecting again and allow the requested permissions.',
  invalid_state:
    'The OAuth2 session expired or was invalid. Please try again.',
  oauth2_not_configured:
    'Gmail OAuth2 is not configured on this server. Contact your administrator.',
}

function humanizeOAuthError(raw: string): string {
  return OAUTH_ERROR_MESSAGES[raw] ?? raw
}

export default function OAuthCallbackView() {
  const location = useLocation()
  const navigate = useNavigate()

  const params = new URLSearchParams(location.search)
  const state = params.get('state') ?? ''
  const urlError = params.get('error')

  // Restore from sessionStorage so a page reload doesn't lose the credentials.
  const cachedRaw = sessionStorage.getItem(OAUTH_RESULT_KEY)
  const cached: OAuthResultResponse | null = cachedRaw
    ? (JSON.parse(cachedRaw) as OAuthResultResponse)
    : null

  const [status, setStatus] = useState<'polling' | 'success' | 'error'>(cached ? 'success' : 'polling')
  const [result, setResult] = useState<OAuthResultResponse | null>(cached)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    // Already restored from sessionStorage — nothing to poll.
    if (cached) return

    // Immediate failure cases from the URL itself
    if (!state || urlError) {
      setStatus('error')
      setErrorMsg(humanizeOAuthError(urlError ?? ''))
      return
    }

    let cancelled = false
    let attempts = 0

    async function poll() {
      while (!cancelled && attempts < MAX_POLLS) {
        attempts++
        try {
          const data = await getOAuthResult(state)
          if (!cancelled) {
            // Cache so a page reload doesn't lose the credentials before
            // the user copies them. Cleared when they click Done.
            sessionStorage.setItem(OAUTH_RESULT_KEY, JSON.stringify(data))
            setResult(data)
            setStatus('success')
          }
          return
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err)
          // 404 means the backend hasn't finished processing yet — keep polling.
          if (msg.includes('404') && attempts < MAX_POLLS) {
            await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
            continue
          }
          // Any other error (400, 401, network, etc.) is terminal.
          if (!cancelled) {
            setStatus('error')
            // Strip the raw API prefix and humanize known error codes.
            const bare = msg.replace(/^API \d+[^:]*: /, '')
            setErrorMsg(humanizeOAuthError(bare))
          }
          return
        }
      }
      // Exhausted retries
      if (!cancelled) {
        setStatus('error')
        setErrorMsg('Timed out waiting for Google to complete the authorization. Please try again.')
      }
    }

    void poll()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, urlError])

  async function copyToken() {
    if (!result) return
    await navigator.clipboard.writeText(result.agent_token)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // -------------------------------------------------------------------------
  // Polling state
  // -------------------------------------------------------------------------
  if (status === 'polling') {
    return (
      <div className="max-w-lg mx-auto mt-16 flex flex-col items-center gap-4 text-fg-3">
        <Mail className="w-10 h-10 text-accent animate-pulse" />
        <p className="text-fg-2 font-medium">Connecting your Gmail account…</p>
        <p className="text-sm text-fg-3">This usually takes just a moment.</p>
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Error state
  // -------------------------------------------------------------------------
  if (status === 'error') {
    return (
      <div className="max-w-lg mx-auto mt-16">
        <div className="bg-surface border border-red-700 rounded-lg p-6 space-y-4">
          <h2 className="text-lg font-semibold text-red-400">Gmail connection failed</h2>
          <p className="text-fg-2 text-sm">{errorMsg ?? 'An unknown error occurred.'}</p>
          <button
            onClick={() => navigate('/agents')}
            className="text-accent hover:text-accent text-sm underline"
          >
            ← Try again
          </button>
        </div>
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Success state — credential card (matches SetupView style)
  // -------------------------------------------------------------------------
  return (
    <div className="max-w-lg mx-auto mt-8 space-y-4">
      <div className="flex items-center gap-2 text-green-400">
        <CheckCircle className="w-5 h-5" />
        <h2 className="text-lg font-semibold">Gmail account connected</h2>
      </div>

      {result && (
        <div className="bg-surface border border-amber-500 rounded-lg p-4 font-mono text-sm space-y-2">
          <p className="text-amber-400 font-semibold mb-3">⚠ Shown once — save these now</p>
          {result.upstream_user && (
            <div className="text-fg-2">
              <span className="text-fg-3">Gmail address: </span>
              {result.upstream_user}
            </div>
          )}
          <div className="text-fg-2">
            <span className="text-fg-3">IMAP server:   </span>{PROXY_HOST}
          </div>
          <div className="text-fg-2">
            <span className="text-fg-3">IMAP port:     </span>993
          </div>
          <div className="text-fg-2">
            <span className="text-fg-3">SMTP port:     </span>587
          </div>
          <div className="text-fg-2">
            <span className="text-fg-3">Username:      </span>
            <span className="text-accent">{result.agent_username}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-fg-3">Password:      </span>
            <span className="text-accent break-all">{result.agent_token}</span>
            <button
              onClick={copyToken}
              className="ml-2 shrink-0 bg-slate-600 hover:bg-slate-500 text-fg-2 text-xs px-2 py-1 rounded"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      <button
        onClick={() => {
          // Clear the sessionStorage cache now that the user has seen the credentials.
          sessionStorage.removeItem(OAUTH_RESULT_KEY)
          navigate('/agents')
        }}
        className="bg-accent hover:bg-accent-hi text-white font-medium py-2 px-4 rounded transition-colors"
      >
        Done
      </button>
    </div>
  )
}
