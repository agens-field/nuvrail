# Nuvrail — Product Roadmap
**Tagline:** _The approval layer between AI agents and your inbox._
**Status:** Phase 0 largely complete — MVP demo prep in progress
**Last updated:** March 19, 2026

---

## What's been built (as of 2026-03-19)

```
IMAP proxy (asyncio)
  ├── Command parser + router (read / write / blocked)
  ├── LOGIN passthrough → agent credential verification
  ├── Write interception → staging engine (op_XXXXXX)
  ├── Local state DB sync (folders, messages, flags from upstream FETCH/SELECT/LIST)
  ├── Snapshot + optimistic update on STORE intercept
  ├── Pending revert injection (FETCH before tagged OK after rejection/expiry)
  └── 226 tests passing (unit + integration + e2e)

SMTP proxy (asyncio)
  ├── STARTTLS upstream negotiation
  ├── AUTH PLAIN + AUTH LOGIN → agent credential verification
  ├── DATA interception → staged send with envelope extraction
  └── aiosmtplib relay on approval

Staging engine + SQLite DB
  ├── staged_operations (create, approve, reject, expire)
  ├── audit_log (immutable, queryable, exportable)
  ├── pending_reverts (proxy-to-API coordination via DB)
  ├── users + agent_credentials (Lane 2 + Lane 3 auth)
  └── 48h expiry background job (asyncio task in FastAPI lifespan)

FastAPI REST API
  ├── Auth: /auth/register, /auth/login, /auth/me (bcrypt + bearer token)
  ├── Agents: POST/GET/DELETE /agents (one-time token display)
  ├── Operations: GET/POST approve/reject /operations
  └── Audit: GET /audit, /audit/{id}, /audit/export

React PWA (Vite + TypeScript + Tailwind)
  ├── /login — email + password form
  ├── /setup — account creation + connect email + one-time agent credential display
  ├── / (Pending) — 5s auto-refresh, approve/reject cards, SMTP confirmation dialog
  └── /audit — paginated timeline, event filters, export
```

---

## Vision

AI agents that access email are unsafe today — not because AI is untrustworthy, but because there is no mechanism for humans to review and sanction what agents do before they do it. Nuvrail is the approval layer. Every email action an AI proposes passes through Nuvrail before reaching your actual inbox. You see a readable description, you approve or reject, and Nuvrail logs everything permanently.

Nuvrail is provider-agnostic (any standard IMAP/SMTP server) and agent-agnostic (any AI that speaks IMAP). It is the pull request model applied to email.

---

## Phase 0 — Core infrastructure + demo-ready MVP

### ✅ Done

| Item | Description |
|---|---|
| IMAP proxy | Raw async TCP proxy, line-buffered, bidirectional |
| IMAP parser | Full IMAP command parser + read/write/blocked classifier |
| IMAP staging | All write commands intercepted, staged, caller gets OK [STAGED] |
| IMAP state DB | Local mailbox mirror synced from FETCH/SELECT/LIST responses |
| IMAP rejection revert | Snapshot → restore → unsolicited FETCH injection |
| SMTP proxy | STARTTLS upstream, DATA interception, relay on approval |
| Staging engine | SQLite DB, op IDs, audit log, expiry background job |
| REST API | Auth + agents + operations + audit (all endpoints) |
| Web PWA | Login, Setup, Pending, Audit views; mobile-responsive; installable |
| Auth (Lane 2 + 3) | Human accounts + agent credential management |

### 🔲 In progress (parallel)

| Item | Owner | Blocking |
|---|---|---|
| Gmail OAuth2 setup (Google Cloud Console) | mmodahl | milestone 0.3 |
| XOAUTH2 proxy integration | KC | needs 0.0 complete |

### 🔲 MVP demo polish (next sprint)

| Item | Priority | Effort |
|---|---|---|
| Batch approve/reject API + web UI | High | Medium |
| Web Push notifications (VAPID) | High | Medium |
| `is_urgent` field + urgency logic | Medium | Small |
| SMTP 550 rejection response to AI | Medium | Small |
| Upstream credential encryption (AES-256-GCM) | High (security) | Medium |
| Rate limiting on proxy connections | Medium | Small |
| TLS on proxy listen side | Medium | Medium |
| Deployment to test.nuvrail.com | High | Small |

---

## Phase 1 — Reliability + notifications + undo (Weeks 5–8)

| Item | Description |
|---|---|
| iOS app (Swift / SwiftUI) | Native pending approvals, APNs push notifications |
| Notification action buttons | Approve/reject from notification without opening app |
| Undo within 24h window | Reverse approved executed operations |
| JWT token refresh | Replace long-lived bearer tokens with refresh cycle |
| Operation batching engine | Group same-folder ops within 30s window |
| Message previews in API | Sender/subject/date joined into operation detail |
| SMTP 550 rejection | AI's next SMTP session sees 550 on rejected sends |

---

## Phase 2 — Multi-provider + rules (Weeks 9–14)

| Item | Description |
|---|---|
| Gmail (XOAUTH2) | Google OAuth2 consent flow, XOAUTH2 IMAP/SMTP |
| Microsoft / Outlook | Azure AD OAuth2, office365 IMAP/SMTP |
| Apple iCloud | App-specific password guided flow |
| Provider abstraction layer | `ProviderConnection` interface |
| Auto-approval rules | Condition + action rules (sender pattern, op type, folder) |
| Per-agent audit | Agent ID tracking through all operations |
| Postgres migration | Row-level tenant isolation, production-grade storage |

---

## Phase 3 — Platform + launch (Weeks 15–20)

| Item | Description |
|---|---|
| Billing (Stripe) | Free / Pro / Team tiers |
| Developer API + webhooks | POST to developer URL on stage/approve/reject events |
| OpenAPI docs + SDK stubs | Python + TypeScript for LangChain, OpenAI Assistants etc. |
| Google Calendar support | Stage calendar event create/modify/delete |
| App Store submission | TestFlight → production iOS release |
| Infrastructure hardening | Rate limiting, monitoring, SOC2 prep |

---

## Tech Stack

| Layer | Technology |
|---|---|
| IMAP Proxy | Python + asyncio, custom IMAP4rev1 state machine |
| SMTP Proxy | Python + asyncio, single-coroutine per-session |
| Provider auth (Phase 0) | Plain LOGIN to upstream (MXrouting / any standard IMAP) |
| Provider auth (Phase 1+) | XOAUTH2 (Gmail, Outlook), app-specific password (iCloud) |
| Token storage | bcrypt hashes; upstream credentials plaintext Phase 0 → AES-256-GCM Phase 1 |
| State DB (dev) | SQLite via aiosqlite |
| State DB (prod) | PostgreSQL (per-tenant schemas) |
| REST API | FastAPI + uvicorn |
| Background jobs | asyncio.create_task in FastAPI lifespan |
| Web Push | pywebpush (planned Phase 1) |
| APNs | apns2 (planned Phase 1 with iOS app) |
| Web App | React 18 + Vite + TypeScript + Tailwind CSS (PWA) |
| iOS App | SwiftUI + Swift (planned Phase 1) |
| Auth | bcrypt + base58 bearer tokens (Phase 0); JWT refresh (Phase 1) |
| Infrastructure | Docker-compose + fly.io (Phase 0–2); AWS/GCP (Phase 3) |
