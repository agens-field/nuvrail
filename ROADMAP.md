# Nuvrail — Product Roadmap
**Tagline:** _The approval layer between AI agents and your inbox._  
**Status:** Pre-build  
**Date:** March 9, 2026  

---

## Vision

AI agents that access email are unsafe today — not because AI is untrustworthy, but because there is no mechanism for humans to review and sanction what agents do before they do it. Nuvrail is the approval layer. Every email action an AI proposes passes through Nuvrail before reaching your actual inbox. You see a readable diff, you approve or reject, and Nuvrail logs everything permanently.

Nuvrail is provider-agnostic (Gmail, Outlook, Yahoo, iCloud) and agent-agnostic (any AI that speaks IMAP). It is the git pull request model applied to email.

---

## Product Architecture

```
┌────────────────────────────────────────────────────────────┐
│                     Nuvrail Platform                        │
│                                                            │
│  ┌────────────────┐   ┌────────────────┐  ┌─────────────┐ │
│  │  IMAP Proxy    │   │  SMTP Proxy    │  │  Admin API  │ │
│  │  (AI ← → real) │   │  (AI → real)   │  │  (REST)     │ │
│  └───────┬────────┘   └───────┬────────┘  └──────┬──────┘ │
│          │                    │                   │        │
│  ┌───────▼────────────────────▼───────────────────▼──────┐ │
│  │              Core: Staging Engine + State DB           │ │
│  │         (per-user SQLite or Postgres tenant)           │ │
│  └───────────────────────────┬────────────────────────────┘ │
│                              │                             │
│  ┌───────────────────────────▼────────────────────────────┐ │
│  │              Notification Service                       │ │
│  │   (Web Push / APNs / Email digests)                    │ │
│  └───────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
         │                          │
┌────────▼──────────┐    ┌──────────▼──────────┐
│  Web App          │    │  iOS App             │
│  (Approval UI)    │    │  (Approval + Push)   │
└───────────────────┘    └─────────────────────┘
```

---

## Phase 0 — Core Infrastructure (Weeks 1–4)

_Goal: A working IMAP proxy that a single user can register a Gmail account with and connect an AI agent to. No mobile app yet. Approvals via web only._

### 0.1 — User accounts + OAuth2 registration
- [ ] User account model (email, hashed password, created_at)
- [ ] Google OAuth2 registration flow: user clicks "Connect Gmail" → consent screen → Nuvrail stores access + refresh tokens (encrypted at rest)
- [ ] Token storage: AES-256 encryption, per-user derived keys
- [ ] Session management (JWT for web sessions)

### 0.2 — AI agent credential issuance
- [ ] On Gmail registration, generate AI agent credentials:
  - `agent_imap_user`: `agent_{uuid}`
  - `agent_imap_password`: 32-byte random, stored as bcrypt hash
- [ ] Agent credential display UI (shown once, copy-to-clipboard)
- [ ] Agent credential revocation endpoint

### 0.3 — IMAP proxy server
- [ ] Asyncio IMAP4rev1 server listening on dedicated port per tenant (or SNI-based routing)
- [ ] AI agent authenticates with agent credentials
- [ ] All IMAP read commands (`SELECT`, `FETCH`, `SEARCH`, `LIST`, `STATUS`) proxied transparently to Google via XOAUTH2
- [ ] Local state DB (SQLite per user) mirrors mailbox: headers, flags, folder structure
- [ ] Verify: standard IMAP client (Thunderbird) works through proxy without modification

### 0.4 — Write interception + staging engine
- [ ] Intercept all write commands (`STORE`, `COPY`, `MOVE`, `APPEND`, `CREATE`, `RENAME`)
- [ ] `EXPUNGE` permanently blocked; `\Deleted` flags → staged "move to Trash" operation
- [ ] Each intercepted write → `Operation` record in staging queue
- [ ] Return `OK [STAGED] op_{id}` to AI agent immediately; update local state optimistically
- [ ] Rejection flow: revert local state → unsolicited `FETCH` on next AI command → AI re-syncs naturally
- [ ] Batching: group operations to same folder within 30s window into single approval card

### 0.5 — Approval REST API
- [ ] `GET /api/v1/operations` — list pending/recent
- [ ] `GET /api/v1/operations/:id` — full detail with message previews (sender, subject, date, proposed action)
- [ ] `POST /api/v1/operations/:id/approve` — approve single; executes against Google immediately
- [ ] `POST /api/v1/operations/:id/reject` — reject; reverts local state
- [ ] `POST /api/v1/operations/batch/approve` — bulk approve
- [ ] `POST /api/v1/operations/batch/reject` — bulk reject
- [ ] `GET /api/v1/audit` — queryable audit log

### 0.6 — Basic web approval UI
- [ ] React + Vite SPA, mobile-friendly (not yet native mobile)
- [ ] Pending operations list view: operation card with sender, subject, proposed action, [Approve] [Reject]
- [ ] Expanded batch view: individual messages within batch, per-item approve/reject
- [ ] Audit log view: scrollable timeline, status badges, [Undo] for reversible ops within window
- [ ] Browser-based Web Push subscription for notifications

**Phase 0 exit criteria:** Martin can register Gmail, connect an AI agent, have the agent propose email operations, and approve/reject from the web UI with notifications firing in-browser.

---

## Phase 1 — iOS App + SMTP (Weeks 5–8)

_Goal: Native iOS app with push notifications. SMTP proxy. Approvals from the notification itself._

### 1.1 — iOS app (Swift / SwiftUI)
- [ ] User login (JWT auth)
- [ ] Pending approvals list — same data as web, native UI
- [ ] Operation detail view: readable diff, message previews
- [ ] [Approve] / [Reject] buttons with haptic confirmation
- [ ] Audit log tab
- [ ] APNs (Apple Push Notification service) integration

### 1.2 — Notification action buttons (iOS)
- [ ] Rich push notification with operation summary
- [ ] Notification action buttons: [Approve] [Reject] without opening app
- [ ] On action: API call from notification extension → response updates badge count
- [ ] Quiet hours: configurable in app settings (default: no notifications 10pm–7am unless marked urgent)

### 1.3 — SMTP proxy
- [ ] SMTP server (port 587 / STARTTLS) accepting AI agent connections
- [ ] `MAIL FROM`, `RCPT TO`, `DATA` intercepted and staged as a send operation
- [ ] Staged send: shows To, Subject, body preview in approval UI
- [ ] On approval: relay to Google SMTP (OAuth2 XOAUTH2)
- [ ] On rejection: return `550 Message rejected by Nuvrail` to AI agent
- [ ] Sent mail always requires single-item explicit approval (never auto-approved)

### 1.4 — Undo / rollback
- [ ] [Undo] button visible on reversible approved operations for 24h window
- [ ] Undo executes reverse IMAP operation (un-archive, remove label, etc.)
- [ ] Audit log records undo with timestamp

**Phase 1 exit criteria:** iOS app on TestFlight. Push notifications with action buttons. SMTP proxy blocking unauthorized sends. Martin testing daily.

---

## Phase 2 — Multi-Provider + Rules Engine (Weeks 9–14)

_Goal: Outlook/Microsoft support. Auto-approval rules. Gateway as OAuth2 server for AI agents._

### 2.1 — Microsoft / Outlook support
- [ ] Microsoft OAuth2 registration flow (Azure AD, personal accounts)
- [ ] IMAP XOAUTH2 against `outlook.office365.com`
- [ ] SMTP XOAUTH2 against `smtp.office365.com`
- [ ] Provider abstraction layer: `ProviderConnection` interface with Gmail + Outlook implementations

### 2.2 — Yahoo + iCloud support
- [ ] Yahoo OAuth2 → XOAUTH2
- [ ] iCloud: app-specific password flow (guided setup), stored encrypted

### 2.3 — Auto-approval rules engine
- [ ] Rule model: condition (sender pattern, operation type, label, folder) + action (auto-approve / require approval / auto-reject)
- [ ] Rules editor in web UI and iOS app
- [ ] Auto-approved operations: execute immediately, logged to audit as `auto_approved`
- [ ] Example rules:
  - "Auto-approve: mark as read, sender matches /newsletter|noreply/"
  - "Auto-approve: archive, sender in known-safe list, no reply in thread"
  - "Always require approval: any send, any delete"

### 2.4 — Gateway as OAuth2 server (AI agent auth upgrade)
- [ ] Nuvrail exposes `/oauth/authorize` and `/oauth/token` endpoints
- [ ] AI agents register as OAuth2 clients with defined scopes:
  - `email:read` — IMAP read-only passthrough
  - `email:write_staged` — IMAP writes (always staged)
  - `email:send_staged` — SMTP sends (always staged)
- [ ] Scoped tokens: an agent with only `email:read` cannot stage writes
- [ ] Token expiry + refresh cycle

### 2.5 — Multiple AI agents per user
- [ ] Users can issue multiple agent credentials with different scope profiles
- [ ] Audit log shows which agent proposed each operation
- [ ] Per-agent approval rules possible

**Phase 2 exit criteria:** Gmail + Outlook + Yahoo + iCloud all working. Auto-approval rules reducing approval fatigue. Multiple agents per user supported.

---

## Phase 3 — Platform + Launch (Weeks 15–20)

_Goal: Public launch. Billing. Developer API. Calendar support._

### 3.1 — Billing + tiers
- [ ] Stripe integration
- [ ] Free tier: 1 email account, 1 AI agent, 30-day audit history
- [ ] Pro tier (~$10/mo): 3 accounts, 3 agents, unlimited audit history, auto-approval rules
- [ ] Team tier (~$25/mo): 10 accounts, unlimited agents, API access, priority support

### 3.2 — Developer API + documentation
- [ ] Public API documentation (OpenAPI spec)
- [ ] Developer portal: register AI agent apps against Nuvrail
- [ ] Webhook support: POST to developer URL on staging, approval, rejection events
- [ ] SDK stubs (Python, TypeScript) for common AI agent frameworks (LangChain, OpenAI Assistants, etc.)

### 3.3 — Google Calendar support
- [ ] Google Calendar API OAuth2 scope added to registration
- [ ] Calendar write operations staged: event create, modify, delete, RSVP
- [ ] Calendar approval cards: show event details before committing
- [ ] Approval UI: calendar diff view (before/after)

### 3.4 — iOS App Store submission
- [ ] App Store review preparation
- [ ] Privacy policy + data handling disclosure
- [ ] TestFlight → production release

### 3.5 — Infrastructure hardening
- [ ] Move from per-user SQLite to Postgres with row-level tenant isolation
- [ ] Secrets management: AWS KMS or Hashicorp Vault for token encryption keys
- [ ] Rate limiting on proxy connections
- [ ] Monitoring + alerting (uptime, proxy latency, staging queue depth)
- [ ] SOC2 Type I preparation

**Phase 3 exit criteria:** Public launch. Paying customers. Developer integrations live.

---

## Tech Stack

| Layer | Technology |
|---|---|
| IMAP Proxy | Python + asyncio (custom IMAP4rev1 state machine) |
| SMTP Proxy | Python + asyncio (aiosmtpd) |
| Google auth | `google-auth` + XOAUTH2 |
| Microsoft auth | MSAL Python + XOAUTH2 |
| State DB (dev) | SQLite via aiosqlite |
| State DB (prod) | PostgreSQL (per-tenant schemas) |
| REST API | FastAPI |
| Web Push | `pywebpush` |
| APNs | `apns2` or AWS SNS |
| Web App | React + Vite + TailwindCSS |
| iOS App | SwiftUI + Swift |
| Infrastructure | Docker + fly.io or Railway (Phase 0–2); AWS/GCP (Phase 3) |

---

## Competitive Landscape

| Product | What they do | What they lack |
|---|---|---|
| **Nylas** | IMAP/SMTP abstraction API for developers | No staging/approval layer; raw access |
| **Zapier / Make** | Workflow automation for email | No AI agent primitives; no IMAP proxy |
| **Superhuman** | Premium email client with AI | Single-user, no agent access model |
| **Microsoft Copilot for M365** | AI in Outlook | No approval layer; closed ecosystem |

**Nuvrail's moat:** The first IMAP/SMTP proxy with a human-in-the-loop approval layer designed specifically for AI agents. Provider-agnostic. Agent-agnostic. Audit-first.

---

## Open Questions

- [ ] Domain: nuvrail.ai, getnuvrail.com, nuvrail.email?
- [ ] Should Nuvrail be self-hostable (open core) or SaaS-only?
- [ ] Android app in scope, or iOS first and Android later?
- [ ] Should the web app also handle non-email actions (calendar, tasks) from Day 1 or stay email-focused?
