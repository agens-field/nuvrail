/**
 * OAuthCallbackView — lands here after Google OAuth2 consent.
 *
 * URL: /#/oauth2/callback?state=<nonce>
 *
 * On mount, reads ?state from the URL and polls GET /api/v1/oauth2/google/result.
 * Retries up to 10 times with 1s delay while the backend is still processing
 * (404 = not ready yet).
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

const PROXY_HOST = import.meta.env.VITE_PROXY_HOST ?? 'test.nuvrail.com'
const MAX_POLLS = 10
const POLL_INTERVAL_MS = 1000

export default function OAuthCallbackView() {
  const location = useLocation()
  const navigate = useNavigate()

  const params = new URLSearchParams(location.search)
  const state = params.get('state') ?? ''
  const urlError = params.get('error')

  const [status, setStatus] = useState<'polling' | 'success' | 'error'>('polling')
  const [result, setResult] = useState<OAuthResultResponse | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    // Immediate failure cases from the URL itself
    if (!state || urlError) {
      setStatus('error')
      setErrorMsg(
        urlError === 'invalid_state'
          ? 'The OAuth2 session expired or was invalid. Please try again.'
          : urlError
            ? `Google returned an error: ${urlError}`
            : 'Missing OAuth2 state parameter.',
      )
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
          // Any other error (400, network, etc.) is terminal.
          if (!cancelled) {
            setStatus('error')
            setErrorMsg(msg.replace(/^API \d+[^:]*: /, ''))
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
      <div className="max-w-lg mx-auto mt-16 flex flex-col items-center gap-4 text-slate-400">
        <Mail className="w-10 h-10 text-indigo-400 animate-pulse" />
        <p className="text-slate-300 font-medium">Connecting your Gmail account…</p>
        <p className="text-sm text-slate-500">This usually takes just a moment.</p>
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Error state
  // -------------------------------------------------------------------------
  if (status === 'error') {
    return (
      <div className="max-w-lg mx-auto mt-16">
        <div className="bg-slate-800 border border-red-700 rounded-lg p-6 space-y-4">
          <h2 className="text-lg font-semibold text-red-400">Gmail connection failed</h2>
          <p className="text-slate-300 text-sm">{errorMsg ?? 'An unknown error occurred.'}</p>
          <button
            onClick={() => navigate('/agents')}
            className="text-indigo-400 hover:text-indigo-300 text-sm underline"
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
        <div className="bg-slate-800 border border-amber-500 rounded-lg p-4 font-mono text-sm space-y-2">
          <p className="text-amber-400 font-semibold mb-3">⚠ Shown once — save these now</p>
          {result.upstream_user && (
            <div className="text-slate-300">
              <span className="text-slate-500">Gmail address: </span>
              {result.upstream_user}
            </div>
          )}
          <div className="text-slate-300">
            <span className="text-slate-500">IMAP server:   </span>{PROXY_HOST}
          </div>
          <div className="text-slate-300">
            <span className="text-slate-500">IMAP port:     </span>993
          </div>
          <div className="text-slate-300">
            <span className="text-slate-500">SMTP port:     </span>587
          </div>
          <div className="text-slate-300">
            <span className="text-slate-500">Username:      </span>
            <span className="text-indigo-300">{result.agent_username}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500">Password:      </span>
            <span className="text-indigo-300 break-all">{result.agent_token}</span>
            <button
              onClick={copyToken}
              className="ml-2 shrink-0 bg-slate-600 hover:bg-slate-500 text-slate-200 text-xs px-2 py-1 rounded"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      <button
        onClick={() => navigate('/agents')}
        className="bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 px-4 rounded transition-colors"
      >
        Done
      </button>
    </div>
  )
}
