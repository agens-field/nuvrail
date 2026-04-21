# Nuvrail — Project Roadmap
**Owner:** KC (CTO)
**Status:** Active
**Last Updated:** 2026-04-20
**Source spec:** SPEC.md

---

## Guiding Principles

Every decision in this roadmap runs through the same filter:

> **What is the simplest thing that solves the actual problem? Does this belong in the current milestone, or are we just trying to look serious?**

The actual problem: give a human the ability to approve or reject an AI agent's email writes before they hit the real mailbox, with a full audit trail, using standard IMAP on both ends.

That is the thing we are building. Everything else is Phase N.

**What this roadmap is not:**
- A multi-tenant SaaS platform (that's Phase 3)
- A multi-provider story (that's Phase 2)
- An iOS native app (that's Phase 1)
- A rules engine (that's Phase 1 / 2)

Phase 0 is a working proof of concept for one user, one Gmail account, running locally.

---

## Build Order

```
OAuth2 setup (0.0) ← deferred (plain-password per-agent routing done instead)

Core proxy stack ✅ DONE
  ├── IMAP parser + write interception
  ├── Staging engine + SQLite state DB
  ├── Rejection revert (snapshot → unsolicited FETCH)
  ├── Upstream execution (approve path)
  ├── React PWA + Web Push
  ├── Per-agent credential routing
  └── Deploy → test.nuvrail.com ✅

Proxy intelligence ← IN PROGRESS
  ├── Rich operation descriptions (sender/subject from state DB) ✅ TODAY
  └── RFC822 literal FETCH header extraction ✅ TODAY

Next up:
  ├── SMTP 550 rejection response to AI
  ├── Credential encryption (AES-256-GCM)
  ├── Operation batching engine (30s window)
  └── TLS on proxy listen side
          │
  ┌───────┴────────┐
  │                │
  Undo (3.2)   iOS app (Phase 1)
                   │
           Multi-provider (Phase 2)
                   │
           Platform + billing (Phase 3)
```

Each arrow means: can't start the next until you can demo the current one to yourself.

---

## Phase 0: Single-User IMAP Proof of Concept

**Goal:** Martin can register an email account, connect an AI agent, propose email operations, and approve/reject from a web UI with push notifications.
**Scope:** Single user, single email account, IMAP + SMTP, local deployment.

---

### ✅ Sub-milestone 0.1 — Raw Async TCP Proxy (Pass-Through)

**Status: DONE**

Bidirectional asyncio byte pump. Client-side plain TCP; upstream SSL/TLS. Pass-through proxy wiring with per-session upstream connection.

---

### ✅ Sub-milestone 0.2 — IMAP Command Parser + Read/Write Classification

**Status: DONE**

```
Incoming IMAP Line
       │
       ▼
┌─────────────────┐
│  Line Parser    │  Extract: tag, command, args (respecting literals {n+})
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Command Router │
│                 │
│  READ class:    │  SELECT, EXAMINE, FETCH, SEARCH, LIST, LSUB,
│  ──────────►    │  STATUS, NOOP, CAPABILITY, ID, LOGOUT, CHECK,
│  (pass-through) │  SUBSCRIBE, UNSUBSCRIBE, NAMESPACE, IDLE
│                 │
│  WRITE class:   │  STORE, COPY, MOVE, APPEND, CREATE, DELETE,
│  ──────────►    │  RENAME, EXPUNGE, UID STORE, UID COPY, UID MOVE
│  (intercept)    │
│                 │
│  BLOCKED:       │  EXPUNGE — never forwarded, ever
│  ──────────►    │
└─────────────────┘
```

Full IMAP4rev1 command parser, UID prefix handling, literal handling, command classification. Tests covering 20+ real IMAP command patterns.

---

### ✅ Sub-milestone 0.4 — Local State DB

**Status: DONE**

SQLite (aiosqlite) mirror of mailbox state. Synced from FETCH/SELECT/LIST upstream responses.

Schema (key tables):
- `folders` — name, uidvalidity, uidnext, exists/recent/unseen counts
- `messages` — uid, sequence_num, subject, sender, date_sent, flags (JSON), size
- `staged_operations` — full staging queue with snapshot, status, op_type, envelope
- `pending_reverts` — per-UID revert queue for proxy injection
- `audit_log` — immutable event log
- `users` + `agent_credentials` — Lane 2/3 auth
- `push_subscriptions` — VAPID browser endpoint registrations
- `auto_approval_rules` — stub schema (engine not yet implemented)

---

### ✅ Sub-milestone 0.5 — SMTP Proxy

**Status: DONE**

STARTTLS upstream negotiation. AUTH PLAIN + AUTH LOGIN → agent credential verification. Full DATA interception with envelope extraction (sender, recipients, subject, body preview). Returns `250 OK [STAGED]` to AI agent. Relay via aiosmtplib on approval.

**Known gap:** SMTP 550 rejection response to AI on rejected/expired sends — not yet implemented (see below).

---

### ✅ Sub-milestone 1.0 — Staging Engine + REST API (Core)

**Status: DONE**

```
Write command arrives
         │
         ▼
┌──────────────────┐
│ Parse operation  │  Extract: op_type, UIDs, flags, folders
│ from IMAP cmd    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Take DB snapshot │  Save current message/flag state for rollback
│ of affected rows │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Write to         │  staged_operations row: status=pending
│ staging queue    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Update local     │  Apply optimistic change to messages table
│ state DB         │  AI sees the change immediately
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Notify (push)    │  notify_staged() called — VAPID push to browser
│ + Return OK      │  "{tag} OK [STAGED] Operation queued — ID: op_xxx"
└──────────────────┘
```

Staging engine, SQLite DB, op IDs (`op_XXXXXX`), 48h expiry background job.
`is_urgent` field: auto-set based on `op_type` urgency classification; passed through to push payload.
REST API: full CRUD for auth + agents + operations + audit.

---

### ✅ Sub-milestone 1.2 — Rejection Revert Mechanism

**Status: DONE**

```
Rejection via API
       │
       ▼
┌─────────────────────┐
│ Revert local DB     │  Restore messages.flags from op snapshot
│ state from snapshot │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Queue pending_reverts│  Per-UID rows: true_flags, delivered_at=NULL
└──────────┬──────────┘
           │   (on next AI IMAP command: SELECT/NOOP/FETCH)
           ▼
┌─────────────────────┐
│ Proxy injects        │  BEFORE forwarding tagged OK:
│ unsolicited FETCH   │  * N FETCH (UID M FLAGS (...original flags...))
└─────────────────────┘
```

Snapshot restore → `pending_reverts` → unsolicited FETCH injection before tagged OK. Correct IMAP ordering per RFC 3501. AI re-syncs naturally — no extensions required.

---

### ✅ Sub-milestone 2.0 — React PWA (Core UI)

**Status: DONE**

React 18 + Vite + TypeScript + Tailwind CSS. Installable PWA via `vite-plugin-pwa`.

Views:
- `/login` — email + password, stores bearer token
- `/setup` — two-step: account creation + email connection + one-time agent credential display
- `/` (Pending) — 5s auto-refresh, approve/reject cards, SMTP confirmation dialog, urgency visual treatment
- `/audit` — paginated timeline, event filters, JSON export

Bulk approve/reject UI implemented (backed by batch API endpoints).

---

### ✅ Sub-milestone 2.2 — Web Push (VAPID)

**Status: DONE** *(notification delivery — batching engine is still a stub)*

VAPID key pair generated on first run. `push_subscriptions` table. `notify_staged()` called from staging engine on every operation. Browser service worker (`sw.ts`) with push event handler.

Push payload: `{title, body: description, urgent: bool, operation_id, url: "/"}`.

**Known gap:** Operation batching engine (`gateway/batching.py`) raises `NotImplementedError`. Operations are currently individual — no 30s window grouping yet. Each staged op sends its own push notification.

---

### ✅ Auth — Lane 2 + Lane 3

**Status: DONE**

- **Lane 2 (AI agents):** `nuvrail_<16hex>` username + 32-byte base58 token, shown once, stored as bcrypt hash (rounds=10). Verified by both IMAP and SMTP proxies on every connection.
- **Lane 3 (humans):** email + password → long-lived bearer token. bcrypt rounds=12. All REST API endpoints gated.

---

### ✅ Sub-milestone 3.1 (partial) — Docker Compose

**Status: DONE** *(compose + Dockerfiles exist; fly.io deploy not yet done)*

`docker-compose.yml` with `gateway` (IMAP proxy + REST API) and `web` (React, served via nginx) services. `Dockerfile.gateway` and `Dockerfile.web` present.

**Known gap:** Not yet deployed to `test.nuvrail.com`.

---

### ✅ Deployed — test.nuvrail.com

**Status: DONE (2026-04-13)**

- Docker Compose running: IMAP proxy + SMTP proxy + FastAPI + web app
- TLS via Let's Encrypt / certbot
- nginx reverse proxy on host
- Per-agent upstream routing live (TODO 0.3 closed)
- MAIL FROM rewrite — agents don't need to know the real upstream address

---

### 🔲 Sub-milestone 0.0 — Dev Environment + Gmail OAuth2 Setup

**Status: IN PROGRESS (mmodahl)**

Google Cloud Console project, Gmail API enabled, OAuth2 credentials (Desktop app), test user added, `client_secret.json` gitignored.

Smoke test script: `scripts/test_gmail_auth.py` — OAuth2 consent → token storage → IMAP select INBOX.

**Blocking:** 0.3 (XOAUTH2 proxy integration)

---

### ✅ Sub-milestone 0.3 — Per-Agent Upstream Routing

**Status: DONE (2026-04-13)**

Both IMAP and SMTP proxies now defer opening the upstream connection until after agent authentication. The upstream host/port/credentials come from the `agent_credentials` row, not static env vars. Each agent can point to a different IMAP/SMTP server.

Also: `MAIL FROM` is rewritten with `upstream_user` so agents don't need to know or specify the real email address.

Note: XOAUTH2/Gmail OAuth2 is a separate concern — tracked as Phase 2 when we add multi-provider support.

---

### ✅ Sub-milestone 1.3 — Upstream Execution (Approve Path)

**Status: DONE**

Fresh `aioimaplib` IMAP4_SSL connection per approve. Credentials from `agent_credentials` by `agent_id` (env var fallback for legacy ops). Credential values decrypted via `gateway/credentials.py`. Supports: STORE/UID STORE, MOVE/UID MOVE, COPY/UID COPY, CREATE, RENAME. APPEND skipped (body not stored in staging DB). SMTP relay via `aiosmtplib` with STARTTLS.

Batch approve (`POST /operations/batch/approve`) executes sequentially to preserve IMAP ordering. Both single and batch paths log `executed` / `execution_failed` to audit log.

```
POST /operations/:id/approve
         │
         ▼
┌──────────────────────┐
│ Look up operation    │
│ in staged_operations │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Execute against      │  IMAP: fresh aioimaplib IMAP4_SSL → SELECT → replay → LOGOUT
│ upstream server      │  SMTP: aiosmtplib STARTTLS relay
└──────────┬───────────┘
           │
     ┌─────┴─────┐
     │ success   │ failure
     ▼           ▼
status=       status=failed
executed      error=<msg>
     │             │
audit_log     audit_log
event=        event=
executed      execution_failed
```

---

### 🔲 MVP Polish Sprint

**Status: IN PROGRESS**

Core proxy is functional and deployed. These are the remaining gaps before Phase 0 is demo-ready to an external audience.

| Item | Status | Priority | Effort |
|---|---|---|---|
| Upstream execution (1.3) | ✅ Done | — | — |
| Per-agent upstream routing | ✅ Done | — | — |
| Upstream credential encryption (AES-256-GCM) | ✅ Done (`gateway/credentials.py`) | — | — |
| Deploy to `test.nuvrail.com` | ✅ Done (2026-04-13) | — | — |
| Rich operation descriptions (sender/subject) | ✅ Done (2026-04-20) | — | — |
| RFC822 literal FETCH header extraction | ✅ Done (2026-04-20) | — | — |
| CORS locked to production domains | ✅ Env-var driven; set `NUVRAIL_CORS_ORIGINS` on server | — | — |
| Operation batching engine (30s window) | ❌ Stub (`NotImplementedError`) | 🟠 Medium | ~2 days |
| TLS on proxy listen side | ❌ Not built | 🟠 Medium | ~1 day |
| Rate limiting on proxy connections | ❌ Not built | 🟠 Medium | ~1 day |
| SMTP rejection signal to AI agent | ⏳ Deferred — connection is gone by reject time; needs design | 🟠 Medium | TBD |
| Gmail XOAUTH2 (0.3) | ⏳ Deferred to Phase 2 | 🟠 Medium | ~2 days |

---

### 🔲 Sub-milestone 3.2 — Undo + Audit Export

**Status: STUB** — `api/undo.py::undo_operation()` raises `NotImplementedError`. Export endpoint is built.

**Work remaining:**
- [ ] Implement `undo_operation(operation_id)`: reverse STORE/MOVE/COPY/ARCHIVE via upstream
- [ ] Check: `status == executed` and within undo window (default 24h)
- [ ] Populate `undo_expires_at` on execute (schema column exists but is never set)
- [ ] `POST /api/v1/operations/:id/undo` endpoint (route exists as stub)
- [ ] Enable [Undo] button in audit view

---

### 🔲 Sub-milestone 3.3 — Auto-Approval Rules + Quiet Hours

**Status: SCHEMA STUB ONLY** — `auto_approval_rules` table exists, no engine.

**Graham check:** This can slip to Phase 1. Phase 0 is shippable after 1.3 + MVP polish.

---

### Phase 0 Exit Criteria

All of the following must be true before Phase 0 is done:

- [ ] AI agent connects to proxy at `test.nuvrail.com:10143` with `nuvrail_*` credentials
- [x] AI agent issues `SELECT INBOX`, `SEARCH`, `FETCH`: all pass through correctly
- [x] AI agent issues `STORE +FLAGS (\Seen)`: operation staged, local DB updated, push notification fires
- [x] Operation cards show sender + subject (not just UID) — via RFC822 FETCH header extraction
- [x] Martin clicks [Approve]: upstream IMAP server reflects the change
- [x] Martin clicks [Reject]: AI agent's next NOOP receives unsolicited FETCH; agent re-syncs
- [x] AI agent issues `EXPUNGE`: no messages deleted, correct response returned
- [x] All of the above logged to audit trail
- [x] `docker-compose up` starts the full system
- [x] Deployed and accessible at `test.nuvrail.com`
- [x] Upstream credentials encrypted at rest (AES-256-GCM) — `gateway/credentials.py`
- [x] CORS locked via `NUVRAIL_CORS_ORIGINS` env var — set on server
- [ ] SMTP rejection signal to AI — connection is gone by reject time, needs design
- [ ] Operation batching engine (30s window grouping)

---

## Phase 1: iOS App + Reliability (Weeks 5–8)

**Pre-condition:** Phase 0 fully complete and demo'd.

### Priority order

```
JWT token refresh (replace long-lived bearer tokens)
    │
    └── iOS app (SwiftUI + APNs)
            │
            ├── Rich push notification (Approve/Reject action buttons)
            ├── Pending list view
            └── Audit view
                    │
                    └── Operation batching engine (30s window grouping)
                            │
                            └── Message previews in operation detail
```

| Item | Description |
|---|---|
| iOS app (SwiftUI) | Native pending approvals, APNs push notifications |
| APNs push | Rich notifications with Approve/Reject action buttons |
| JWT token refresh | Replace long-lived bearer tokens with refresh cycle |
| Operation batching engine | Group same-folder ops within 30s window; single notification per batch |
| Message previews | Sender/subject/date joined into operation detail card |
| Undo within 24h | Enable undo path (already scaffolded) |
| SMTP 550 rejection | AI's next SMTP session sees 550 on rejected/expired sends |

---

## Phase 2: Multi-Provider + Rules (Weeks 9–14)

**Goal:** Gmail XOAUTH2 (if not done in Phase 0), Outlook, iCloud. Rules engine. Per-agent audit.

```
Provider abstraction layer (base.py already exists)
    │
    ├── Gmail (XOAUTH2) — may already be done from 0.3
    ├── Microsoft Outlook (Azure AD + XOAUTH2)
    └── Apple iCloud (app-specific password guided flow)

Auto-approval rules engine (schema stub exists)
    │
    └── Per-agent audit (agent_id already in schema)

Postgres migration
    │
    └── Row-level tenant isolation (user_id FK on every table)
```

| Item | Description |
|---|---|
| Provider abstraction layer | `ProviderConnection` interface — extract Gmail, add Outlook |
| Microsoft / Outlook | Azure AD OAuth2, office365 IMAP/SMTP |
| Apple iCloud | App-specific password guided setup |
| Auto-approval rules | Condition + action rules (sender pattern, op type, folder) |
| Per-agent audit | Agent ID tracking through all operations |
| Postgres migration | Row-level tenant isolation, production-grade storage |

---

## Phase 3: Platform + Public Launch (Weeks 15–20)

| Item | Description |
|---|---|
| Multi-tenancy + Postgres | User accounts, row-level isolation, connection pooling |
| User registration + account linking | Public registration flow |
| Billing (Stripe) | Free / Pro / Team tiers; tier enforcement |
| Developer API + webhooks | POST to developer URL on stage/approve/reject |
| OpenAPI docs + SDK stubs | Python + TypeScript |
| Google Calendar support | Stage calendar event create/modify/delete |
| App Store submission | TestFlight → production iOS release |
| Infrastructure hardening | Rate limiting, monitoring, SOC2 prep |

---

## Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | Google OAuth2 app review blocks production use | High | High | Use "Testing" mode (100 users max) through Phase 0–1; submit for verification in Phase 2 |
| R2 | Upstream execution (1.3) uncovers IMAP state machine gaps | Medium | High | 0.3 and 1.3 must be tested with real Gmail sessions; add integration tests that execute against live upstream |
| R3 | XOAUTH2 token refresh race condition in async context | Medium | Medium | Token refresh must be serialized (asyncio Lock) — enforce this explicitly in token_store.py |
| R4 | Unsolicited FETCH path breaks some AI IMAP clients | Medium | High | Already tested in e2e suite; test with mutt + Python imaplib as well |
| R5 | APPEND with large literals (AI saving large drafts) | Medium | Medium | Test with 1MB+ APPEND in 1.1 testing; asyncio buffering can bite here |
| R6 | Postgres migration in Phase 3 requires downtime | Low | High | Keep `user_id` columns in all SQLite tables from day one (already in schema) |
| R7 | Plaintext upstream credentials in Phase 0 | High | Medium | Disclosed risk; acceptable for demo. AES-256-GCM path must land before any real user data |
| R8 | Web Push action buttons limited Safari support | Low | Low | Phase 0 user (Martin) on Chrome; iOS handles via APNs in Phase 1 |

---

## Dependency Map

```
Core proxy stack ✅ DONE (as of 2026-04-20)
  ├── IMAP proxy + command router + staging engine
  ├── Rejection revert (snapshot → unsolicited FETCH)
  ├── Upstream execution (approve path) — aioimaplib + aiosmtplib
  ├── React PWA + Web Push (VAPID)
  ├── Per-agent credential routing
  ├── Credential encryption at rest (AES-256-GCM)
  ├── CORS — env-var driven, locked to test + mail domains
  ├── test.nuvrail.com deployed + TLS via nginx/Let's Encrypt
  └── Rich op descriptions: RFC822 header extraction → sender/subject

MVP polish [IN PROGRESS] ← active work
  ├── SMTP rejection signal design (connection gone by reject time)
  ├── Operation batching engine (30s window)
  └── TLS + rate limiting on proxy listen side
          │
  ┌───────┴────────┐
  │                │
Batching engine  Undo (3.2)
  │
iOS + APNs (Phase 1)
  │
Multi-provider + Gmail XOAUTH2 (Phase 2)
  │
Platform + billing (Phase 3)
```

---

## Open Questions

- [ ] **APPEND to Sent Mail:** Should AI `APPEND` to `[Gmail]/Sent Mail` be staged, or is it treated as a shadow of an already-approved send? Answer needed before 1.3 is finalized.
- [ ] **Single vs. multi-account Phase 1:** Does Phase 1 need account switching, or is one upstream per running instance acceptable until Phase 3?
- [ ] **Self-hosted vs. SaaS:** Affects Phase 3 architecture significantly. Decision must be made before Phase 2 scoping.
- [ ] **Android app:** iOS is Phase 1. Android is TBD — needs a decision before Phase 2 is scoped.
- [ ] **Open source timeline:** When does the repo go public? Affects how we handle the credential encryption gap.
