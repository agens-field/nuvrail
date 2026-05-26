/**
 * CookieConsentBanner — GDPR-compliant cookie consent.
 *
 * State machine:
 *
 *   [first visit]
 *       │
 *       ▼
 *   banner visible ──► "Accept All"          → store "all",      hide banner
 *       │              "Accept Necessary Only"→ store "necessary", hide banner
 *       │
 *       ▼ (page reload / subsequent visits)
 *   localStorage["nuvrail_cookie_consent"] present → banner hidden
 *
 * "Cookie Preferences" footer link → calls window.nuvrailReopenCookieBanner()
 * which this component exposes on mount.
 *
 * Strictly necessary cookies (session) always active — no consent required.
 * Analytics cookies only active after "Accept All".
 */

import React, { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'nuvrail_cookie_consent';

type ConsentValue = 'all' | 'necessary';

/** Activate analytics (e.g. inject GA script) when user consents to all cookies. */
function activateAnalytics(): void {
  // Placeholder: add your analytics initialisation here once a provider is chosen.
  // Example: window.gtag('consent', 'update', { analytics_storage: 'granted' });
}

export default function CookieConsentBanner(): JSX.Element | null {
  const [visible, setVisible] = useState(false);

  // Read stored preference on mount; show banner only when no preference exists.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) as ConsentValue | null;
      if (!stored) {
        setVisible(true);
      } else if (stored === 'all') {
        activateAnalytics();
      }
    } catch {
      // localStorage unavailable (e.g. private browsing with strict settings) —
      // show banner as a safe fallback.
      setVisible(true);
    }

    // Expose re-open hook for "Cookie Preferences" footer link.
    (window as Window & { nuvrailReopenCookieBanner?: () => void }).nuvrailReopenCookieBanner =
      () => setVisible(true);

    return () => {
      delete (window as Window & { nuvrailReopenCookieBanner?: () => void })
        .nuvrailReopenCookieBanner;
    };
  }, []);

  const handleAcceptAll = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_KEY, 'all');
    } catch {
      // ignore
    }
    activateAnalytics();
    setVisible(false);
  }, []);

  const handleAcceptNecessary = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_KEY, 'necessary');
    } catch {
      // ignore
    }
    setVisible(false);
  }, []);

  if (!visible) return null;

  return (
    <div
      role="dialog"
      aria-label="Cookie consent"
      aria-live="polite"
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: 9999,
        background: '#1e293b',
        borderTop: '1px solid #334155',
        padding: '16px 24px',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: '12px',
        boxShadow: '0 -4px 24px rgba(0,0,0,0.4)',
      }}
    >
      <p
        style={{
          flex: '1 1 300px',
          color: '#cbd5e1',
          fontSize: '14px',
          lineHeight: '1.5',
          margin: 0,
        }}
      >
        We use strictly necessary cookies to keep the service running. With your
        consent we also use analytics cookies to understand how Nuvrail is used —
        no data is sold or shared with advertisers.{' '}
        <a
          href="/privacy"
          style={{ color: '#94a3b8', textDecoration: 'underline' }}
        >
          Privacy Policy
        </a>
        .
      </p>

      <div style={{ display: 'flex', gap: '10px', flexShrink: 0 }}>
        <button
          onClick={handleAcceptNecessary}
          style={{
            padding: '8px 16px',
            fontSize: '14px',
            fontWeight: 500,
            cursor: 'pointer',
            borderRadius: '6px',
            border: '1px solid #475569',
            background: 'transparent',
            color: '#cbd5e1',
            transition: 'border-color 0.15s, color 0.15s',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = '#94a3b8';
            (e.currentTarget as HTMLButtonElement).style.color = '#f1f5f9';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = '#475569';
            (e.currentTarget as HTMLButtonElement).style.color = '#cbd5e1';
          }}
        >
          Accept Necessary Only
        </button>

        <button
          onClick={handleAcceptAll}
          style={{
            padding: '8px 16px',
            fontSize: '14px',
            fontWeight: 600,
            cursor: 'pointer',
            borderRadius: '6px',
            border: '1px solid transparent',
            background: '#3b82f6',
            color: '#ffffff',
            transition: 'background 0.15s',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = '#2563eb';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = '#3b82f6';
          }}
        >
          Accept All
        </button>
      </div>
    </div>
  );
}
