/**
 * CookieConsentBanner — GDPR consent bar for the Nuvrail web app.
 *
 * Stores consent in localStorage under 'nuvrail_cookie_consent':
 *   'accepted_all'      — analytics + necessary
 *   'necessary_only'    — necessary only
 *
 * Fires 'nuvrail_consent_updated' on any change so PlausibleAnalytics
 * can react without a reload.
 *
 * window.nuvrailReopenCookieBanner() allows reopening from an account
 * settings page or anywhere else in the app.
 *
 * Note: localStorage is origin-scoped. Consent given on nuvrail.com is
 * not visible here (different origin). Users will see this banner once
 * on the landing page and once in the app — that's correct behaviour.
 */

import { useEffect, useState } from 'react'

export const CONSENT_KEY = 'nuvrail_cookie_consent'
export const CONSENT_EVENT = 'nuvrail_consent_updated'

export type ConsentLevel = 'accepted_all' | 'necessary_only'

export function getConsent(): ConsentLevel | null {
  try {
    return (localStorage.getItem(CONSENT_KEY) as ConsentLevel) ?? null
  } catch {
    return null
  }
}

function saveConsent(level: ConsentLevel) {
  localStorage.setItem(CONSENT_KEY, level)
  window.dispatchEvent(new Event(CONSENT_EVENT))
}

export default function CookieConsentBanner() {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (!localStorage.getItem(CONSENT_KEY)) setVisible(true)
    ;(window as any).nuvrailReopenCookieBanner = () => setVisible(true)
    return () => { delete (window as any).nuvrailReopenCookieBanner }
  }, [])

  function accept(level: ConsentLevel) {
    saveConsent(level)
    setVisible(false)
  }

  if (!visible) return null

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-surface border-t border-edge px-6 py-4 flex flex-col sm:flex-row items-start sm:items-center gap-4 shadow-lg">
      <p className="text-sm text-fg-2 flex-1">
        We use{' '}
        <a
          href="https://plausible.io"
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent underline"
        >
          Plausible Analytics
        </a>
        {' '}— no cookies, no personal data — to understand how the app is used.{' '}
        <a href="https://nuvrail.com/privacy" target="_blank" rel="noopener noreferrer" className="text-accent underline">
          Privacy Policy
        </a>
      </p>
      <div className="flex gap-2 shrink-0">
        <button
          onClick={() => accept('necessary_only')}
          className="px-4 py-2 text-sm border border-edge text-fg-2 rounded hover:bg-surface-hi transition-colors"
        >
          Necessary only
        </button>
        <button
          onClick={() => accept('accepted_all')}
          className="px-4 py-2 text-sm bg-accent hover:bg-accent-hi text-white dark:text-[#111c27] rounded font-semibold transition-colors"
        >
          Accept all
        </button>
      </div>
    </div>
  )
}
