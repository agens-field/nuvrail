import Head from 'next/head';

/**
 * Nuvrail landing page — nuvrail.com
 *
 * Sections:
 *   Hero → Problem → How it works → Features → CTA → Footer
 */
export default function Home() {
  return (
    <>
      <Head>
        <title>Nuvrail — IMAP/SMTP approval proxy for AI agents</title>
        <meta
          name="description"
          content="Nuvrail is an IMAP/SMTP proxy that intercepts every proposed email action and stages it for human approval before it touches your real mailbox."
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.ico" />
      </Head>

      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#f1f5f9' }}>

        {/* ── Nav ─────────────────────────────────────────────────────── */}
        <nav style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '20px 40px',
          borderBottom: '1px solid #1e293b',
        }}>
          <span style={{ fontWeight: 700, fontSize: '20px', letterSpacing: '-0.5px' }}>
            nuvrail
          </span>
          <div style={{ display: 'flex', gap: '28px', fontSize: '14px', color: '#94a3b8' }}>
            <a href="#how-it-works" style={{ color: '#94a3b8' }}>How it works</a>
            <a href="https://github.com/agens-field/nuvrail" style={{ color: '#94a3b8' }}>GitHub</a>
            <a
              href="/app"
              style={{
                color: '#fff',
                background: '#3b82f6',
                padding: '7px 16px',
                borderRadius: '6px',
                fontWeight: 600,
              }}
            >
              Sign in
            </a>
          </div>
        </nav>

        {/* ── Hero ────────────────────────────────────────────────────── */}
        <section style={{
          maxWidth: '800px',
          margin: '0 auto',
          padding: '100px 40px 80px',
          textAlign: 'center',
        }}>
          <p style={{ color: '#64748b', fontSize: '13px', letterSpacing: '2px', textTransform: 'uppercase', marginBottom: '20px' }}>
            Open source · IMAP/SMTP · Agent-agnostic
          </p>
          <h1 style={{
            fontSize: 'clamp(36px, 6vw, 60px)',
            fontWeight: 800,
            lineHeight: 1.1,
            letterSpacing: '-1.5px',
            marginBottom: '24px',
          }}>
            What git did for code,<br />for email.
          </h1>
          <p style={{
            fontSize: '18px',
            color: '#94a3b8',
            maxWidth: '560px',
            margin: '0 auto 40px',
            lineHeight: 1.7,
          }}>
            Nuvrail is an IMAP/SMTP proxy that stages every AI-proposed email action for
            human approval before it executes — with an immutable audit log of every decision.
          </p>
          <div style={{ display: 'flex', gap: '12px', justifyContent: 'center', flexWrap: 'wrap' }}>
            <a
              href="/app"
              style={{
                display: 'inline-block',
                background: '#3b82f6',
                color: '#fff',
                padding: '14px 28px',
                borderRadius: '8px',
                fontWeight: 700,
                fontSize: '15px',
              }}
            >
              Get started →
            </a>
            <a
              href="https://github.com/agens-field/nuvrail"
              style={{
                display: 'inline-block',
                border: '1px solid #334155',
                color: '#cbd5e1',
                padding: '14px 28px',
                borderRadius: '8px',
                fontWeight: 600,
                fontSize: '15px',
              }}
            >
              View on GitHub
            </a>
          </div>
        </section>

        {/* ── Problem ─────────────────────────────────────────────────── */}
        <section style={{
          background: '#1e293b',
          borderTop: '1px solid #334155',
          borderBottom: '1px solid #334155',
          padding: '60px 40px',
        }}>
          <div style={{ maxWidth: '680px', margin: '0 auto', textAlign: 'center' }}>
            <h2 style={{ fontSize: '26px', fontWeight: 700, marginBottom: '16px', letterSpacing: '-0.5px' }}>
              AI agents with email access have no pull request.
            </h2>
            <p style={{ color: '#94a3b8', fontSize: '16px', lineHeight: 1.7 }}>
              Your agent can send, delete, and move messages with full IMAP permissions and no
              review step. Nuvrail adds the checkpoint that&apos;s been missing — transparent to
              your agent, safe for your inbox.
            </p>
          </div>
        </section>

        {/* ── How it works ────────────────────────────────────────────── */}
        <section id="how-it-works" style={{ maxWidth: '800px', margin: '0 auto', padding: '80px 40px' }}>
          <h2 style={{ fontSize: '26px', fontWeight: 700, marginBottom: '48px', textAlign: 'center', letterSpacing: '-0.5px' }}>
            How it works
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '32px' }}>
            {[
              {
                step: '01',
                title: 'Point your agent at Nuvrail',
                body: 'Change one IMAP/SMTP endpoint. No SDK changes, no code rewrites. Your agent thinks it\'s talking to a regular mail server.',
              },
              {
                step: '02',
                title: 'Actions are staged, not executed',
                body: 'Every write — send, delete, move — is held in a staging queue. Your agent receives an immediate OK. Nothing touches the real inbox yet.',
              },
              {
                step: '03',
                title: 'You approve on mobile',
                body: 'Review the proposed action in the Nuvrail app. Approve and it executes. Reject and it\'s reversed — the agent sees the corrected state on its next sync.',
              },
            ].map(({ step, title, body }) => (
              <div key={step} style={{ padding: '28px', background: '#1e293b', borderRadius: '10px', border: '1px solid #334155' }}>
                <div style={{ fontSize: '12px', color: '#3b82f6', fontWeight: 700, letterSpacing: '2px', marginBottom: '12px' }}>
                  STEP {step}
                </div>
                <h3 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '10px' }}>{title}</h3>
                <p style={{ color: '#94a3b8', fontSize: '14px', lineHeight: 1.7 }}>{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── Features ────────────────────────────────────────────────── */}
        <section style={{
          background: '#1e293b',
          borderTop: '1px solid #334155',
          borderBottom: '1px solid #334155',
          padding: '60px 40px',
        }}>
          <div style={{ maxWidth: '800px', margin: '0 auto' }}>
            <h2 style={{ fontSize: '26px', fontWeight: 700, marginBottom: '36px', textAlign: 'center', letterSpacing: '-0.5px' }}>
              Built for production email infrastructure
            </h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px' }}>
              {[
                ['Immutable audit log', 'Every approve/reject decision is recorded and tamper-evident.'],
                ['Auto-approval rules', 'Define conditions for low-risk actions that execute without review.'],
                ['No permanent deletes', 'EXPUNGE is blocked. Delete = staged move to Trash, always reversible.'],
                ['End-to-end encryption', 'AES-256-GCM at rest. No plaintext email metadata in push payloads.'],
                ['Open source core', 'The proxy is open source. Self-host the full stack or use our cloud.'],
                ['Provider-agnostic', 'Works with any IMAP/SMTP provider: Gmail, Fastmail, iCloud, Outlook.'],
              ].map(([title, desc]) => (
                <div key={title as string} style={{ padding: '20px', border: '1px solid #334155', borderRadius: '8px', background: '#0f172a' }}>
                  <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '6px' }}>{title}</div>
                  <div style={{ color: '#94a3b8', fontSize: '13px', lineHeight: 1.6 }}>{desc}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── CTA ─────────────────────────────────────────────────────── */}
        <section style={{ maxWidth: '600px', margin: '0 auto', padding: '80px 40px', textAlign: 'center' }}>
          <h2 style={{ fontSize: '30px', fontWeight: 800, marginBottom: '16px', letterSpacing: '-0.5px' }}>
            Ready to put a checkpoint on email?
          </h2>
          <p style={{ color: '#94a3b8', fontSize: '16px', marginBottom: '32px', lineHeight: 1.7 }}>
            Free to self-host. Managed cloud available. No credit card required to start.
          </p>
          <a
            href="/app"
            style={{
              display: 'inline-block',
              background: '#3b82f6',
              color: '#fff',
              padding: '14px 32px',
              borderRadius: '8px',
              fontWeight: 700,
              fontSize: '15px',
            }}
          >
            Get started →
          </a>
        </section>

        {/* ── Footer ──────────────────────────────────────────────────── */}
        <footer style={{
          borderTop: '1px solid #1e293b',
          padding: '32px 40px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px',
          color: '#475569',
          fontSize: '13px',
        }}>
          <span>© {new Date().getFullYear()} Nuvrail. All rights reserved.</span>
          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
            <a href="/privacy" style={{ color: '#475569' }}>Privacy Policy</a>
            <a href="/terms" style={{ color: '#475569' }}>Terms</a>
            {/* Triggers re-open of the cookie consent banner */}
            <button
              onClick={() => {
                const reopen = (window as Window & { nuvrailReopenCookieBanner?: () => void })
                  .nuvrailReopenCookieBanner;
                if (reopen) reopen();
              }}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: '#475569',
                fontSize: '13px',
                padding: 0,
              }}
            >
              Cookie Preferences
            </button>
          </div>
        </footer>

      </div>
    </>
  );
}
