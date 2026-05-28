/**
 * PlausibleAnalytics — injects the Plausible page-view script for the PWA.
 *
 * Only active when localStorage['nuvrail_cookie_consent'] === 'accepted_all'.
 * Listens for 'nuvrail_consent_updated' so analytics activate immediately
 * when the user accepts via CookieConsentBanner — no reload required.
 *
 * Domain is set via VITE_PLAUSIBLE_DOMAIN (defaults to 'app.nuvrail.com').
 * Set to an empty string to disable (e.g. staging, local dev).
 *
 * Deduplication: if the script tag is already present the effect is a no-op.
 */

import { useEffect } from 'react'
import { CONSENT_EVENT, CONSENT_KEY } from './CookieConsentBanner'

const PLAUSIBLE_DOMAIN = import.meta.env.VITE_PLAUSIBLE_DOMAIN ?? 'app.nuvrail.com'
const PLAUSIBLE_SRC = 'https://plausible.io/js/script.js'

function injectIfConsented() {
  if (!PLAUSIBLE_DOMAIN) return
  if (localStorage.getItem(CONSENT_KEY) !== 'accepted_all') return
  if (document.querySelector(`script[data-domain="${PLAUSIBLE_DOMAIN}"]`)) return
  const script = document.createElement('script')
  script.src = PLAUSIBLE_SRC
  script.defer = true
  script.setAttribute('data-domain', PLAUSIBLE_DOMAIN)
  document.head.appendChild(script)
}

export default function PlausibleAnalytics() {
  useEffect(() => {
    injectIfConsented()
    window.addEventListener(CONSENT_EVENT, injectIfConsented)
    return () => window.removeEventListener(CONSENT_EVENT, injectIfConsented)
  }, [])

  return null
}
