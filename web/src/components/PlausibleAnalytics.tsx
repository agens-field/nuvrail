/**
 * PlausibleAnalytics — injects the Plausible page-view script for the PWA.
 *
 * Plausible collects no cookies and no PII (aggregate page views only),
 * so it qualifies as legitimate interest under GDPR and does not require
 * an explicit opt-in consent flow.
 *
 * Domain is set via VITE_PLAUSIBLE_DOMAIN (defaults to 'app.nuvrail.com').
 * Set to an empty string or omit the env var to disable tracking (e.g. in
 * local dev or staging).
 *
 * Deduplication: if the script tag is already present (e.g. hot-reload),
 * the effect is a no-op.
 */

import { useEffect } from 'react'

const PLAUSIBLE_DOMAIN = import.meta.env.VITE_PLAUSIBLE_DOMAIN ?? 'app.nuvrail.com'
const PLAUSIBLE_SRC = 'https://plausible.io/js/script.js'

export default function PlausibleAnalytics() {
  useEffect(() => {
    if (!PLAUSIBLE_DOMAIN) return // disabled (empty env var)
    if (document.querySelector(`script[data-domain="${PLAUSIBLE_DOMAIN}"]`)) return

    const script = document.createElement('script')
    script.src = PLAUSIBLE_SRC
    script.defer = true
    script.setAttribute('data-domain', PLAUSIBLE_DOMAIN)
    document.head.appendChild(script)
  }, [])

  return null
}
