# Nuvrail IMAP/SMTP Approval Gateway — Specification

**Status:** Draft v5
**Date:** March 19, 2026
**Authors:** Martin Modahl, Jack (CEO)
**Changelog:**
- v2 — security/auth model added, SMTP moved to launch, failure modes defined, schema updated, deployment URLs added, open questions closed
- v3 — multi-provider support (Google, Microsoft, Apple, generic IMAP/SMTP) added to Milestone 2; provider abstraction layer specified
- v4 — open source scope defined: IMAP/SMTP gateway + web approval app open source (MIT or Apache 2.0); iOS app proprietary
- v5 — updated to reflect actual built state as of 2026-03-19. Sections marked **[BUILT]** are implemented and tested. Auth lanes revised to match implementation. New sections: §17 Implementation Status, §18 MVP Demo Checklist.

---

## 1. Overview

### Problem

AI agents that interact with email must be given raw IMAP credentials today — which grants unrestricted access. There is no mechanism for a human to review and approve proposed email operations before they execute, no audit trail, and no rollback path. This is the primary reason email access is the most dangerous thing to give an AI agent.

### Solution

An **IMAP/SMTP Approval Gateway** — a proxy server that sits between the AI agent and the real email provider. The AI agent believes it is talking to a normal IMAP/SMTP server. All read operations pass through transparently. All write and send operations are **staged** — queued for human approval via a separate notification interface — before executing against the real provider.

The result: the AI can propose email changes freely, but nothing reaches the real mailbox without a human approving a readable diff.

### Design Principles

1. **Propose before execute** — every write and send is staged, never immediate
2. **Readable diffs** — approvals show exactly what will change, in human language
3. **Immutable audit log** — every action (proposed, approved, rejected, executed) is logged forever
4. **No deletes** — permanent deletion is disabled at the gateway layer; archive/label only
5. **Standard IMAP/SMTP on both ends** — no special client or agent modifications required
6. **No credential bypass** — there is no path through the gateway that skips staging; every write is reviewed, always

---

## 2. Deployment Environments

| Environment | Base URL | Purpose |
|---|---|---|
| **Internal testing** | *(self-hosted)* | Development, QA, dogfooding |
| **Production** | `nuvrail.example.com` | Live customer traffic |

**Ports:**
- IMAP proxy: `993` (TLS) external; `10143` internal/dev
- SMTP proxy: `587` (STARTTLS) external; `10587` internal/dev
- REST API: `443` (TLS) external; `8080` internal
- Approval web app: served via HTTPS on standard port

---

## 3. System Architecture **[BUILT]**

```
┌────────────────────────────────────────────────────────────────────┐
│                        Nuvrail Gateway                             │
│                                                                    │
│  ┌──────────────────────┐    ┌──────────────────────┐             │
│  │   IMAP Proxy Server  │    │   SMTP Proxy Server  │             │
│  │   (port 993/10143)   │    │   (port 587/10587)   │             │
│  └──────────┬───────────┘    └──────────┬───────────┘             │
│             │                            │                         │
│  ┌──────────▼────────────────────────────▼───────────┐            │
│  │              Staging Engine                        │            │
│  │  (intercept writes → create Operation records)    │            │
│  └──────────────────────┬────────────────────────────┘            │
│                         │                                          │
│  ┌──────────────────────▼────────────────────────────┐            │
│  │              Core Data Layer (SQLite → Postgres)  │            │
│  │   staged_operations │ audit_log │ local_state_db  │            │
│  │   users │ agent_credentials │ pending_reverts     │            │
│  └──────────────────────┬────────────────────────────┘            │
│                         │                                          │
│  ┌──────────────────────▼────────────────────────────┐            │
│  │              Approval REST API (FastAPI)           │            │
│  │              + Background Jobs (expiry loop)      │            │
│  └──────────────────────┬────────────────────────────┘            │
└───────────────────────── │ ─────────────────────────────────────── ┘
                           │ HTTPS + Bearer token
          ┌────────────────▼────────────────┐
          │   Web App (React PWA)            │
          │   Pending / Audit / Setup views  │
          └──────────────────────────────────┘
```

### Components

| Component | Role | Status |
|---|---|---|
| **IMAP Proxy Server** | Presents as IMAP to AI agent; intercepts writes; passes reads | **BUILT** |
| **SMTP Proxy Server** | Presents as SMTP to AI agent; stages all sends for approval | **BUILT** |
| **Local State DB** | Mirror of mailbox state (headers, flags, folder structure) | **BUILT** |
| **Staging Engine** | Parses intercepted commands → creates Operation records | **BUILT** |
| **Rejection Revert** | Snapshot pre-op state, restore on reject, inject FETCH to AI | **BUILT** |
| **Operation Expiry** | 48h background job; expires pending ops, reverts state | **BUILT** |
| **Approval REST API** | HTTP API for the approval app to fetch, approve, reject ops | **BUILT** |
| **Audit Log API** | Paginated, filterable, exportable audit trail | **BUILT** |
| **User Accounts** | Human accounts with bcrypt password + bearer token | **BUILT** |
| **Agent Credentials** | Per-user agent username + one-time token for proxy auth | **BUILT** |
| **Approval Web App** | React PWA: Pending, Audit Log, Setup/Login views | **BUILT** |
| **Notification Service** | Push notifications to user when operations are staged | Not built |
| **iOS App** | Native iOS approval app | Not built |

---

## 4. Security & Authentication Model

Three distinct access lanes. No credential from one lane can be used in another.

### 4.1 Lane 1 — Provider credentials (internal only) **[PARTIAL]**

The gateway authenticates to real email providers using credentials obtained during agent setup.

**Current state (Phase 0):**
- Upstream host, port, username, and password stored in `agent_credentials` table
- Stored in **plaintext** for Phase 0 — connection-level TLS protects in transit
- TODO: AES-256-GCM per-user encryption at rest (Phase 1 priority)
- No XOAUTH2 yet — plain LOGIN to upstream (works with MXrouting, standard IMAP servers)
- Gmail XOAUTH2 deferred to milestone 0.3

**Target state (Phase 1+):**
- Upstream credentials encrypted at rest with AES-256, per-user derived keys
- XOAUTH2 for Gmail, OAuth2 for Microsoft/Apple

### 4.2 Lane 2 — AI agent credentials (IMAP/SMTP access) **[BUILT]**

AI agents authenticate to the Nuvrail IMAP and SMTP proxy using Nuvrail-issued credentials:

- **Username:** `nuvrail_<16 hex chars>` (e.g. `nuvrail_a1b2c3d4e5f6g7h8`)
- **Password:** 32-byte base58-encoded random token, shown **once** at creation, stored as bcrypt hash (rounds=10)
- Generated via `POST /api/v1/agents` and displayed once in the web app Setup view
- Credentials verified by proxy on every IMAP LOGIN / SMTP AUTH
- Revoked credentials (`revoked_at IS NOT NULL`) are rejected immediately
- Multiple agent credential sets per user supported

### 4.3 Lane 3 — Human credentials (web app / REST API) **[BUILT]**

Humans authenticate to the REST API using:

- **Email + Password** → `POST /api/v1/auth/login` → long-lived bearer token
- Token stored in browser localStorage, sent as `Authorization: Bearer <token>` header
- All REST API endpoints require a valid bearer token
- Passwords stored as bcrypt hashes (rounds=12)
- No JWT expiry yet — tokens are long-lived; revocation requires DB deletion (Phase 1)

### 4.4 Personal client access (not through gateway)

Personal email clients (Apple Mail, Thunderbird used by a human) connect **directly to the email provider** — they are not routed through the Nuvrail gateway at all. Gateway is the AI lane only.

---

## 5. IMAP Proxy Server **[BUILT]**

### 5.1 Connection handling

- Listens on `10143` (plain TCP, dev) / `993` (TLS, prod)
- Accepts IMAP4rev1 connections from AI agents using Lane 2 credentials
- On LOGIN: verifies `agent_username` + `agent_token` against `agent_credentials` table; closes with `* BYE Authentication failed` on failure
- Connects upstream using stored upstream credentials (plain LOGIN for Phase 0)
- Each AI session maps to one upstream provider connection

### 5.2 Read operations — pass-through **[BUILT]**

Commands forwarded directly: `SELECT`, `EXAMINE`, `FETCH`, `SEARCH`, `LIST`, `LSUB`, `STATUS`, `NOOP`, `CAPABILITY`, `ID`, `LOGOUT`, `CHECK`, `SUBSCRIBE`, `UNSUBSCRIBE`, `NAMESPACE`, `IDLE`

The local state DB is updated on each FETCH/SELECT/LIST response to maintain a current mirror. Upstream → client direction is line-buffered so pending reverts can be injected before tagged responses.

### 5.3 Write operations — staged **[BUILT]**

Intercepted and staged (not executed):

| IMAP Command | Op Type | Snapshot | Reverts on reject |
|---|---|---|---|
| `STORE +FLAGS \Deleted` | trash | ✅ | ✅ |
| `STORE +FLAGS \Seen` | mark_read | ✅ | ✅ |
| `STORE +FLAGS \Flagged` | flag | ✅ | ✅ |
| `STORE -FLAGS *` | unflag/mark_unread | ✅ | ✅ |
| `COPY uid folder` | copy | — | — |
| `MOVE uid folder` | move | — | — |
| `EXPUNGE` | **blocked permanently** | — | — |
| `APPEND folder` | append | — | — |
| `CREATE folder` | create | — | — |
| `RENAME folder` | rename | — | — |

**On interception:**
1. Snapshot pre-op flag state for affected messages
2. Apply optimistic update to local state DB (AI sees proposed change)
3. Create `staged_operations` record with snapshot stored
4. Return `OK [STAGED] Operation queued — ID: op_XXXXXX` to AI agent
5. (Notifications not yet implemented)

**On rejection:** **[BUILT]**
1. Restore messages table from snapshot
2. Insert `pending_reverts` rows for affected UIDs
3. On AI's next SELECT/NOOP/FETCH: inject unsolicited `* N FETCH (UID M FLAGS (...))` before tagged OK
4. AI perceives it as another client modifying mailbox — standard IMAP sync, no extensions needed
5. Log rejection to audit log

**On approval:** **[BUILT]**
1. Execute IMAP command against upstream (STORE, MOVE, COPY, CREATE, RENAME)
2. Update operation status → `executed`
3. Log to audit trail

**On expiry (48h timeout):** **[BUILT]**
1. Background loop runs every hour; finds `status='pending' AND expires_at <= now()`
2. Restores snapshot, queues pending_reverts
3. Sets status → `expired` (distinct from `rejected`)
4. Logs to audit trail

### 5.4 Human-readable operation descriptions **[BUILT]**

`gateway/operation_parser.py` generates descriptions:

| Op type | Example description |
|---|---|
| mark_read | `Mark as read: 42` |
| trash | `Move to Trash: 1:5` |
| flag | `Star: 3` |
| move | `Move 1:5 to Archive` |
| smtp_send | `Send email to alice@example.com — Subject: "Q1 Report"` |

### 5.5 EXPUNGE blocking and APPEND handling **[BUILT]**

**EXPUNGE:** Never forwarded. Intercepted with `OK Noted`. Messages flagged `\Deleted` → staged as "trash" op.

**APPEND with sync literal `{N}`:** Proxy sends `+ Ready`, consumes literal bytes from client, returns `OK [STAGED]` without forwarding body to upstream.

---

## 6. SMTP Proxy Server **[BUILT]**

### 6.1 Connection handling

- Listens on `10587` (plain TCP, dev) / `587` (prod)
- AI agent connects plain TCP (proxy handles STARTTLS upstream)
- On AUTH: verifies `agent_username` + `agent_token` against `agent_credentials` table
- STARTTLS strip: proxy removes STARTTLS from EHLO capabilities forwarded to client
- AUTH LOGIN (multi-step) and AUTH PLAIN (single-shot) both handled

### 6.2 Send interception — always staged **[BUILT]**

All SMTP sends are always staged, always require explicit human approval.

DATA interception:
1. Forward `DATA` to upstream, read `354` response
2. Consume full message body from client (read until `.\r\n`)
3. Extract sender, recipients, Subject header, body preview (200 chars)
4. Store as `staged_operations` with `op_type='smtp_send'` and `smtp_envelope` JSON
5. Return `250 OK [STAGED] Send queued for approval — ID: op_XXXXXX` to client

**On approval:** Relay full message to upstream via `aiosmtplib` with STARTTLS.

**On rejection:** Nothing sent. TODO: `550 Message rejected` on AI's next SMTP session (not yet implemented).

**On expiry:** Same as IMAP expiry — status → `expired`, log audit event.

---

## 7. Staging Queue **[BUILT]**

### 7.1 Operation schema (actual)

```sql
CREATE TABLE staged_operations (
    id              TEXT PRIMARY KEY,       -- op_XXXXXX (6 random alphanum)
    created_at      INTEGER NOT NULL,
    expires_at      INTEGER NOT NULL,       -- created_at + 48h (configurable via NUVRAIL_EXPIRY_HOURS)
    status          TEXT NOT NULL,          -- 'pending'|'approved'|'rejected'|'executed'|'failed'|'expired'
    op_type         TEXT NOT NULL,
    protocol        TEXT NOT NULL,          -- 'imap'|'smtp'
    imap_command    TEXT,
    smtp_envelope   TEXT,                   -- JSON {from, to, subject, body_preview}
    description     TEXT NOT NULL,
    agent_id        TEXT,                   -- NULL for Phase 0
    message_ids     TEXT,                   -- JSON array
    folder_from     TEXT,
    folder_to       TEXT,
    flags_add       TEXT,                   -- JSON array
    flags_remove    TEXT,                   -- JSON array
    snapshot        TEXT,                   -- JSON {uid: {flags, seq_num, folder_id}} — pre-op state
    decided_at      INTEGER,
    decided_by      TEXT,
    executed_at     INTEGER,
    undo_expires_at INTEGER,
    error           TEXT
);
```

### 7.2 Batching

**Not yet implemented.** `gateway/batching.py` is a stub. Operations are currently individual. Planned for Phase 1.

### 7.3 Undo window

**Not yet implemented.** `undo_expires_at` column exists in schema but is never populated. `api/undo.py` is a stub. Planned for Phase 1.

### 7.4 Pending reverts table **[BUILT]**

```sql
CREATE TABLE pending_reverts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES staged_operations(id),
    folder_id    INTEGER NOT NULL,
    uid          INTEGER NOT NULL,
    true_flags   TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    delivered_at INTEGER     -- NULL until proxy injects the FETCH
);
```

---

## 8. Approval REST API **[BUILT]**

Base URL: `http://localhost:8080/api/v1` (dev) / `https://nuvrail.example.com/api/v1` (prod)

All endpoints require `Authorization: Bearer <token>` except `/auth/register` and `/auth/login`.

### 8.1 Auth endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create user account (email, password, display_name) |
| POST | `/auth/login` | Exchange email+password for bearer token |
| GET | `/auth/me` | Current user profile |

### 8.2 Agent credential endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/agents` | Register upstream email, generate agent credentials (token shown once) |
| GET | `/agents` | List credentials (no token field) |
| DELETE | `/agents/{id}` | Revoke credential |

### 8.3 Operation endpoints

| Method | Path | Description | Status |
|---|---|---|---|
| GET | `/operations` | List ops (optional `?status=` filter) | **BUILT** |
| GET | `/operations/{id}` | Single op detail | **BUILT** |
| POST | `/operations/{id}/approve` | Approve + execute | **BUILT** |
| POST | `/operations/{id}/reject` | Reject + revert | **BUILT** |
| POST | `/operations/batch/approve` | Bulk approve | **NOT BUILT** |
| POST | `/operations/batch/reject` | Bulk reject | **NOT BUILT** |
| POST | `/operations/{id}/undo` | Undo within 24h window | **NOT BUILT** |

### 8.4 Audit endpoints

| Method | Path | Description | Status |
|---|---|---|---|
| GET | `/audit` | List entries (limit, offset, event, actor filters) | **BUILT** |
| GET | `/audit/{id}` | Single entry | **BUILT** |
| GET | `/audit/export` | JSON download of full log | **BUILT** |

---

## 9. Approval App (Web) **[BUILT — Phase 0]**

React + Vite PWA. Installable on iOS Safari and Android Chrome.

### 9.1 Current views

**Login (`/login`):** Email + password form. Stores bearer token in localStorage.

**Setup (`/setup`):** Two-step flow:
1. Create account (email + password + display name)
2. Connect email (upstream host/ports/credentials + label) → generates agent credentials with one-time display

**Pending (`/`):** Live-refreshing (5s) list of pending operations. Each card shows protocol badge, description, time queued, expiry countdown. Approve/reject buttons. SMTP sends have confirmation dialog.

**Audit Log (`/audit`):** Paginated timeline with event filters, inline row expansion, JSON export.

**Agents (`/agents`):** Coming soon placeholder.

### 9.2 Not yet implemented

- Web Push subscription management
- Notification action buttons
- Operation detail expanded view with message previews
- Bulk approve/reject UI
- Undo button within 24h window
- Quiet hours configuration

### 9.3 Notifications

**Not yet implemented.** `gateway/push.py` and `api/routes/push.py` are stubs. Web Push via VAPID is planned for Phase 1.

---

## 10. Audit Log **[BUILT]**

Append-only `audit_log` table. Events: `staged`, `approved`, `rejected`, `executed`, `execution_failed`, `expired`.

Queryable via `GET /api/v1/audit` with pagination and filtering. Exportable as JSON. Never modified, truncated, or deleted.

```sql
CREATE TABLE audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     INTEGER NOT NULL,
    operation_id  TEXT REFERENCES staged_operations(id),
    event         TEXT NOT NULL,
    actor         TEXT,            -- 'ai_agent'|'human'|'system'
    agent_id      TEXT,
    detail        TEXT             -- JSON
);
```

---

## 11. Rejection Handling — IMAP Mechanics **[BUILT]**

When the human rejects an IMAP operation:

1. **Snapshot restore:** `restore_from_snapshot(op_id)` reads the JSON snapshot, restores `messages.flags` to pre-op values
2. **Pending reverts queued:** `insert_pending_reverts(op_id, reverts)` inserts rows into `pending_reverts`
3. **Next AI command:** `_upstream_to_client` detects `revert_trigger_tag` match on SELECT/NOOP/FETCH tagged OK; calls `_inject_pending_reverts` BEFORE forwarding the tagged OK
4. **Unsolicited FETCH injected:** `* N FETCH (UID M FLAGS (...))` showing true state
5. **AI re-syncs naturally:** Sees mailbox state changed between commands — standard IMAP behavior

This requires no IMAP extensions and works with any compliant IMAP client or agent.

---

## 12. Failure Modes

| Failure | Behavior |
|---|---|
| Upstream IMAP connection fails | `* BYE Upstream connection failed` returned to client |
| Upstream SMTP connection fails | `421 Service temporarily unavailable` returned to client |
| IMAP approved op fails on upstream | Status → `failed`; audit log event=`execution_failed`; 500 returned to caller |
| SMTP relay fails on approve | Same — status → `failed`; audit logged |
| Operation expires (48h) | Background job: status → `expired`, local state reverted, audit logged |
| Snapshot revert fails on reject | Non-fatal — rejection still succeeds, warning logged |
| DB sync error in proxy (state sync) | Always swallowed — byte pump continues; warning logged |

---

## 13. Tech Stack (actual)

| Component | Technology |
|---|---|
| IMAP Proxy Server | Python + asyncio, custom IMAP4rev1 state machine |
| SMTP Proxy Server | Python + asyncio, single-coroutine per-session |
| Provider IMAP connection | Direct SSL (plain LOGIN for Phase 0; XOAUTH2 deferred to 0.3) |
| Provider SMTP connection | aiosmtplib + STARTTLS |
| Token encryption | Plaintext for Phase 0; TODO AES-256-GCM per-user (Phase 1) |
| Local State DB | SQLite via aiosqlite |
| Approval REST API | FastAPI + uvicorn |
| Background jobs | asyncio.create_task in FastAPI lifespan |
| Web App | React 18 + Vite + TypeScript + Tailwind CSS (PWA via vite-plugin-pwa) |
| State management | @tanstack/react-query v5 |
| Auth | bcrypt (rounds=12 human, rounds=10 agent) + base58 bearer tokens |
| Testing | pytest + pytest-asyncio + httpx; 226 tests (unit + integration + e2e) |
| Dev infrastructure | Docker-compose (gateway + API + web); fly.io target for prod |

---

## 14. Database Schema Summary (actual)

Tables in `nuvrail.db`:

| Table | Purpose | Status |
|---|---|---|
| `users` | Human accounts | **BUILT** |
| `agent_credentials` | Lane 2 agent tokens + upstream config | **BUILT** |
| `staged_operations` | Write/send operations pending approval | **BUILT** |
| `audit_log` | Immutable event log | **BUILT** |
| `pending_reverts` | Per-UID revert queue for proxy injection | **BUILT** |
| `folders` | Local mailbox mirror — folder metadata | **BUILT** |
| `messages` | Local mailbox mirror — message headers + flags | **BUILT** |
| `push_subscriptions` | Web Push subscriber registrations | STUB |
| `auto_approval_rules` | Auto-approval rule definitions | STUB |

---

## 15. Milestones

### Phase 0 (completed)

| Milestone | Description | Status |
|---|---|---|
| 0.1 | Raw async TCP proxy + LOGIN passthrough | ✅ Done |
| 0.2 | IMAP command parser + router + proxy wiring | ✅ Done |
| 0.4 | Local state DB (folder + message sync from upstream) | ✅ Done |
| 0.5 | SMTP proxy + DATA staging | ✅ Done |
| 1.0 | Staging engine + SQLite DB + minimal REST API | ✅ Done |
| 1.1 | End-to-end scenario test suite | ✅ Done |
| 1.2 | Rejection revert mechanism (snapshot → restore → inject FETCH) | ✅ Done |
| 2.0 | React PWA (Pending + Audit + Setup + Login views) | ✅ Done |
| Audit API | GET /audit, /audit/{id}, /audit/export | ✅ Done |
| Expiry | 48h expiry background job + revert on expiry | ✅ Done |
| Auth | Lane 2 + Lane 3 authentication | ✅ Done |

### Phase 0 (in progress — parallel)

| Milestone | Description | Status |
|---|---|---|
| 0.0 | Dev environment + Google Cloud Console OAuth2 | In progress (mmodahl) |
| 0.3 | XOAUTH2 real auth (Gmail) | Waiting on 0.0 |

### Phase 1 (next)

| Milestone | Description |
|---|---|
| MVP Demo | Polish pass for external demo (see §18) |
| 1.3 | Batch approve/reject API + UI |
| 1.4 | Web Push notifications (VAPID) |
| 1.5 | Undo within 24h window |
| 1.6 | Upstream credential encryption (AES-256-GCM, per-user keys) |
| 1.7 | SMTP 550 rejection response to AI |
| 1.8 | Operation batching engine (30s window grouping) |
| 1.9 | is_urgent field + urgency escalation logic |

### Phase 2+

- iOS app (SwiftUI + APNs)
- Microsoft / Outlook OAuth2
- Apple iCloud (app-specific password flow)
- Auto-approval rules engine
- Postgres migration
- Multi-tenant platform layer

---

## 16. Open Source Boundary (unchanged)

Open source (MIT or Apache 2.0): IMAP proxy, SMTP proxy, staging engine, audit log, REST API, web PWA, Docker Compose.

Proprietary: iOS app, Nuvrail Cloud managed infrastructure, enterprise features.

---

## 17. Implementation Notes (architecture decisions)

**Single-coroutine SMTP vs. two-task IMAP:**
IMAP proxy uses two concurrent asyncio tasks (client→upstream, upstream→client) because IMAP supports unsolicited server pushes. SMTP is strictly request/response — DATA interception requires synchronous control of both sides, so SMTP uses a single coroutine.

**Revert injection timing:**
Pending reverts are injected BEFORE the tagged OK for SELECT/NOOP/FETCH is forwarded to the client. This ensures the AI receives `[* N FETCH ...][tag OK]` in the correct order — the unsolicited FETCH arrives before the command completes, which is valid per RFC 3501.

**DB path injection for testing:**
All DB-accessing functions accept `db_path: Path = DB_PATH` as a keyword parameter. FastAPI endpoints use `Depends(get_db_path)` to override in tests. Proxy modules read `gateway.state_db.DB_PATH` at connection time (not at import time) so test fixtures can patch the module attribute.

**Agent token bcrypt rounds:**
Human passwords use rounds=12 (slow, logged-in-once). Agent tokens use rounds=10 (faster — verified on every IMAP/SMTP connection attempt). Both are sufficient for their threat models.

**Upstream credentials plaintext (Phase 0):**
`agent_credentials.upstream_password` is stored in plaintext. This is an acknowledged technical debt for Phase 0. Encryption path: AES-256-GCM with a per-user key derived from a master secret (KMS or Vault). Flagged as Phase 1 priority.

---

## 18. MVP Demo Checklist

For an external demo, the following must work end-to-end:

1. **Account creation** — new user registers at `/setup`, enters email + password
2. **Email connection** — user enters upstream IMAP/SMTP host + credentials, generates agent token
3. **Agent configures** — AI agent configured with proxy host/port, `agent_username`, `agent_token`
4. **AI proposes operations** — agent connects to IMAP proxy, performs STORE/MOVE commands → `OK [STAGED]`
5. **Human sees pending** — web app shows operation cards with description
6. **Human approves** — button click; operation executes against upstream; AI re-syncs
7. **Human rejects** — operation reverted; AI sees corrected state on next command
8. **Audit trail** — complete timeline visible at `/audit`

**Not required for demo but nice to have:**
- Web Push notifications (currently requires manual refresh)
- SMTP send staging (works but is secondary to IMAP for demo)
- Batch approve

**Known gaps before demo:**
- No CORS configuration for production domain (currently `*` in dev mode)
- Upstream credentials stored in plaintext (acceptable for demo, disclose to audience)
- No rate limiting on proxy connections
- No TLS on proxy listen side (SSH tunnel needed for remote agents)

---

## 19. Decisions Log

| # | Question | Decision |
|---|---|---|
| 1 | SMTP proxy? | Yes — IMAP and SMTP together |
| 2 | Multi-agent support? | Multiple agent credential sets per user from day 1 |
| 3 | Calendar scope? | Deferred — email only |
| 4 | Personal clients bypass gateway? | Yes — AI lane only |
| 5 | APPEND to Sent folder? | Blocked at IMAP layer |
| 6 | APPEND to Drafts? | Staged as normal write |
| 7 | Single vs. multi-account? | Multiple agents per user; multi-upstream planned |
| 8 | Auto-approval for sends? | Never — sends always explicit |
| 9 | Operation expiry? | 48h; status='expired' distinct from 'rejected' |
| 10 | Gmail vs. generic IMAP first? | Generic IMAP/SMTP first (MXrouting); Gmail in 0.3 |
| 11 | Auth: JWT vs. long-lived token? | Long-lived bearer token for Phase 0; JWT refresh in Phase 1 |
| 12 | Agent token storage? | bcrypt hash only; plaintext shown once at creation |
| 13 | Upstream cred encryption? | Plaintext Phase 0; AES-256-GCM per-user key Phase 1 |
| 14 | Rejection revert timing? | Inject FETCH BEFORE tagged OK (correct IMAP ordering) |
| 15 | Expiry distinct from rejection? | Yes — `expired` vs `rejected` for auditability |
| 16 | Open source scope? | Gateway + web app OSS; iOS app proprietary |
