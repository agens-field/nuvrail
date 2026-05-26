import Head from 'next/head';

/**
 * Nuvrail landing page — nuvrail.com
 *
 * Brand palette (2026-05 rebrand, aligned to icon + social card):
 *   bg-primary:      #111c27  deep navy-black
 *   bg-surface:      #1a2e42  mid navy — cards, sections
 *   bg-surface-raised: #1f3a55 lifted surfaces — logo shield match
 *   border:          #2a4560  navy border
 *   accent:          #2dd4bf  teal — checkmark from shield
 *   accent-dark:     #0f9d8a  teal hover
 *   text-primary:    #ffffff
 *   text-secondary:  #a8c4d8  blue-grey body
 *   text-muted:      #3d5a72  placeholder / footnotes
 *
 * Typography:
 *   Display (h1): Montserrat Black (900) — matches wordmark
 *   Headings (h2/h3): Montserrat ExtraBold (800)
 *   Body: Inter
 *   Mono labels: JetBrains Mono
 *
 * Sections:
 *   Nav → Hero → Problem → HowItWorks → TrustSignals → CTA/WaitlistForm → Footer
 */
export default function Home() {
  return (
    <>
      <Head>
        <title>Nuvrail — The approval layer between AI agents and your inbox.</title>
        <meta
          name="description"
          content="Nuvrail sits between your AI agent and your inbox. Every proposed action is staged for human review, logged immutably, and held until you approve the diff."
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" type="image/png" href="/favicon.png" />
        <link rel="apple-touch-icon" href="/icon-192.png" />
        {/* Open Graph */}
        <meta property="og:title" content="Nuvrail — The approval layer between AI agents and your inbox." />
        <meta property="og:description" content="The pull request model for AI email access. Every proposed action is staged, diffed, and held for your approval." />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="https://nuvrail.com" />
        <meta property="og:image" content="/Nuvrail-social-card.png" />
        <meta property="og:image:width" content="1200" />
        <meta property="og:image:height" content="630" />
        {/* Twitter */}
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content="Nuvrail — The approval layer between AI agents and your inbox." />
        <meta name="twitter:description" content="The pull request model for AI email access. Every proposed action is staged, diffed, and held for your approval." />
        <meta name="twitter:image" content="/Nuvrail-social-card.png" />
        <meta name="theme-color" content="#111c27" />
      </Head>

      <div style={{ minHeight: '100vh', background: '#111c27', color: '#ffffff' }}>

        {/* ── Nav ─────────────────────────────────────────────────────── */}
        <nav style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '20px 40px',
          borderBottom: '1px solid #2a4560',
        }}>
          <span style={{ fontFamily: 'Montserrat, sans-serif', fontWeight: 900, fontSize: '20px', letterSpacing: '-0.5px', color: '#ffffff' }}>
            nuvrail
          </span>
          <div style={{ display: 'flex', gap: '28px', fontSize: '14px', color: '#a8c4d8' }}>
            <a href="#how-it-works" style={{ color: '#a8c4d8' }}>How it works</a>
            <a href="https://github.com/agens-field/nuvrail" style={{ color: '#a8c4d8' }}>GitHub</a>
            <a
              href="/app"
              style={{
                color: '#111c27',
                background: '#2dd4bf',
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
          borderBottom: '1px solid #2a4560',
        }}>
          {/* Badge */}
          <p style={{
            display: 'inline-block',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: '11px',
            letterSpacing: '2px',
            textTransform: 'uppercase',
            color: '#2dd4bf',
            background: '#2dd4bf12',
            border: '1px solid #2dd4bf33',
            padding: '4px 12px',
            borderRadius: '4px',
            marginBottom: '24px',
          }}>
            Open source · IMAP/SMTP · Agent-agnostic
          </p>
          <h1 style={{
            fontSize: 'clamp(36px, 6vw, 60px)',
            fontWeight: 900,
            lineHeight: 1.1,
            letterSpacing: '-1.5px',
            marginBottom: '16px',
            color: '#ffffff',
          }}>
            What git did for code,<br />for email.
          </h1>
          <p style={{
            fontSize: '20px',
            fontWeight: 600,
            color: '#2dd4bf',
            marginBottom: '12px',
          }}>
            Email has no git. Nuvrail adds it.
          </p>
          <p style={{
            fontSize: '18px',
            color: '#a8c4d8',
            maxWidth: '560px',
            margin: '0 auto 40px',
            lineHeight: 1.7,
          }}>
            Nuvrail sits between your AI agent and your inbox. Every proposed action is staged
            for human review, logged immutably, and held until you approve the diff.
          </p>
          <div style={{ display: 'flex', gap: '12px', justifyContent: 'center', flexWrap: 'wrap' }}>
            <a
              href="/app"
              style={{
                display: 'inline-block',
                background: '#2dd4bf',
                color: '#111c27',
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
                border: '1px solid #2a4560',
                color: '#a8c4d8',
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
          background: '#1a2e42',
          borderBottom: '1px solid #2a4560',
          padding: '60px 40px',
        }}>
          <div style={{ maxWidth: '680px', margin: '0 auto', textAlign: 'center' }}>
            <p style={{
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: '11px',
              color: '#3d5a72',
              textTransform: 'uppercase',
              letterSpacing: '2px',
              marginBottom: '20px',
            }}>
              The problem
            </p>
            <h2 style={{ fontSize: '26px', fontWeight: 800, marginBottom: '16px', letterSpacing: '-0.5px', color: '#ffffff' }}>
              AI agents with email access have no pull request.
            </h2>
            <p style={{ color: '#a8c4d8', fontSize: '16px', lineHeight: 1.7 }}>
              Your agent can send, delete, and move messages with full IMAP permissions and no
              review step. That's not a risk you should be taking on.
            </p>
            <p style={{ color: '#a8c4d8', fontSize: '16px', lineHeight: 1.7, marginTop: '12px' }}>
              Nuvrail adds the checkpoint that's been missing — transparent to your agent, safe for your inbox.
            </p>
          </div>
        </section>

        {/* ── How it works ────────────────────────────────────────────── */}
        <section id="how-it-works" style={{ maxWidth: '800px', margin: '0 auto', padding: '80px 40px', borderBottom: '1px solid #2a4560' }}>
          <p style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: '11px',
            color: '#3d5a72',
            textTransform: 'uppercase',
            letterSpacing: '2px',
            marginBottom: '12px',
            textAlign: 'center',
          }}>
            How it works
          </p>
          <h2 style={{ fontSize: '26px', fontWeight: 800, marginBottom: '48px', textAlign: 'center', letterSpacing: '-0.5px', color: '#ffffff' }}>
            Three steps. No SDK changes.
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
              <div key={step} style={{ padding: '28px', background: '#1a2e42', borderRadius: '10px', border: '1px solid #2a4560' }}>
                <div style={{
                  fontFamily: 'JetBrains Mono, monospace',
                  fontSize: '28px',
                  fontWeight: 700,
                  color: '#2dd4bf',
                  marginBottom: '12px',
                }}>
                  {step}
                </div>
                <h3 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '10px', color: '#ffffff' }}>{title}</h3>
                <p style={{ color: '#a8c4d8', fontSize: '14px', lineHeight: 1.7 }}>{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── Trust signals / Features ─────────────────────────────────── */}
        <section style={{
          background: '#1a2e42',
          borderBottom: '1px solid #2a4560',
          padding: '60px 40px',
        }}>
          <div style={{ maxWidth: '800px', margin: '0 auto' }}>
            <p style={{
              fontFamily: 'JetBrains Mono, monospace',
              fontSize: '11px',
              color: '#3d5a72',
              textTransform: 'uppercase',
              letterSpacing: '2px',
              marginBottom: '12px',
              textAlign: 'center',
            }}>
              Built for production
            </p>
            <h2 style={{ fontSize: '26px', fontWeight: 800, marginBottom: '36px', textAlign: 'center', letterSpacing: '-0.5px', color: '#ffffff' }}>
              Built for production email infrastructure
            </h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px' }}>
              {[
                ['🔒', 'Immutable audit log', 'Every approve/reject decision is recorded and tamper-evident.'],
                ['⚡', 'Auto-approval rules', 'Define conditions for low-risk actions that execute without review.'],
                ['🗑️', 'No permanent deletes', 'EXPUNGE is blocked. Delete = staged move to Trash, always reversible.'],
                ['🔐', 'End-to-end encryption', 'AES-256-GCM at rest. No plaintext email metadata in push payloads.'],
                ['📦', 'Open source core', 'The proxy is open source. Self-host the full stack or use our cloud.'],
                ['🌐', 'Provider-agnostic', 'Works with any IMAP/SMTP provider: Gmail, Fastmail, iCloud, Outlook.'],
              ].map(([icon, title, desc]) => (
                <div key={title as string} style={{ padding: '20px', border: '1px solid #2a4560', borderRadius: '8px', background: '#111c27' }}>
                  <div style={{ fontSize: '22px', color: '#2dd4bf', marginBottom: '10px' }}>{icon}</div>
                  <div style={{ fontWeight: 700, fontSize: '14px', marginBottom: '6px', color: '#ffffff' }}>{title}</div>
                  <div style={{ color: '#a8c4d8', fontSize: '13px', lineHeight: 1.6 }}>{desc}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── CTA ─────────────────────────────────────────────────────── */}
        <section style={{ maxWidth: '600px', margin: '0 auto', padding: '80px 40px', textAlign: 'center', borderBottom: '1px solid #2a4560' }}>
          <p style={{
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: '11px',
            color: '#3d5a72',
            textTransform: 'uppercase',
            letterSpacing: '2px',
            marginBottom: '12px',
          }}>
            Get started
          </p>
          <h2 style={{ fontSize: '30px', fontWeight: 800, marginBottom: '16px', letterSpacing: '-0.5px', color: '#ffffff' }}>
            Ready to put a checkpoint on email?
          </h2>
          <p style={{ color: '#a8c4d8', fontSize: '16px', marginBottom: '32px', lineHeight: 1.7 }}>
            Free to self-host. Managed cloud available. No credit card required to start.
          </p>
          <a
            href="/app"
            style={{
              display: 'inline-block',
              background: '#2dd4bf',
              color: '#111c27',
              padding: '14px 32px',
              borderRadius: '8px',
              fontWeight: 700,
              fontSize: '15px',
            }}
          >
            Get started →
          </a>
          <p style={{ color: '#3d5a72', fontSize: '12px', marginTop: '16px' }}>
            No spam. No credit card.
          </p>
        </section>

        {/* ── Footer ──────────────────────────────────────────────────── */}
        <footer style={{
          borderTop: '1px solid #2a4560',
          padding: '32px 40px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px',
          color: '#3d5a72',
          fontSize: '13px',
          fontFamily: 'JetBrains Mono, monospace',
        }}>
          <span>© {new Date().getFullYear()} Nuvrail. All rights reserved.</span>
          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
            <a href="/privacy" style={{ color: '#3d5a72' }} onMouseOver={e => (e.currentTarget.style.color = '#a8c4d8')} onMouseOut={e => (e.currentTarget.style.color = '#3d5a72')}>Privacy Policy</a>
            <a href="/terms" style={{ color: '#3d5a72' }} onMouseOver={e => (e.currentTarget.style.color = '#a8c4d8')} onMouseOut={e => (e.currentTarget.style.color = '#3d5a72')}>Terms</a>
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
                color: '#3d5a72',
                fontSize: '13px',
                fontFamily: 'JetBrains Mono, monospace',
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
