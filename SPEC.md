# Nuvrail IMAP/SMTP Approval Gateway — Specification

**Status:** Draft v3
**Date:** March 16, 2026
**Authors:** Martin Modahl, Jack (CEO)
**Changelog:** v2 — security/auth model added, SMTP moved to launch, failure modes defined, schema updated, localhost replaced with deployment URLs, all open questions closed. v3 — multi-provider support (Google, Microsoft, Apple, generic IMAP/SMTP) added to Milestone 2; provider abstraction layer specified.

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
| **Internal testing** | `test.nuvrail.com` | Development, QA, dogfooding |
| **Production** | `mail.nuvrail.com` | Live customer traffic |

**Ports:**
- IMAP proxy: `993` (TLS) external; `1143` internal
- SMTP proxy: `587` (STARTTLS) external; `1587` internal
- REST API: `443` (TLS) external; `8080` internal
- Approval web app: served via HTTPS on standard port

---

## 3. System Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                        Nuvrail Gateway                             │
│                                                                    │
│  ┌──────────────────────┐    ┌──────────────────────┐             │
│  │   IMAP Proxy Server  │    │   SMTP Proxy Server  │             │
│  │   (port 993/1143)    │    │   (port 587/1587)    │             │
│  └──────────┬───────────┘    └──────────┬───────────┘             │
│             │                            │                         │
│  ┌──────────▼────────────────────────────▼───────────┐            │
│  │              Staging Engine                        │            │
│  │  (intercept writes → create Operation records)    │            │
│  └──────────────────────┬────────────────────────────┘            │
│                         │                                          │
│  ┌──────────────────────▼────────────────────────────┐            │
│  │              Core Data Layer                       │            │
│  │   staged_operations │ audit_log │ local_state_db  │            │
│  │   (SQLite dev / Postgres prod)                    │            │
│  └──────────────────────┬────────────────────────────┘            │
│                         │                                          │
│  ┌──────────────────────▼────────────────────────────┐            │
│  │              Approval REST API (FastAPI)           │            │
│  │              + Notification Service               │            │
│  └──────────────────────┬────────────────────────────┘            │
└───────────────────────── │ ─────────────────────────────────────── ┘
                           │ HTTPS
          ┌────────────────▼────────────────┐
          │   Approval Web App / iOS App    │
          │   (review, approve, reject)     │
          └─────────────────────────────────┘

External connections:
  AI Agent  →  Nuvrail IMAP Proxy  →  Provider IMAP (Gmail, Outlook, etc.)
  AI Agent  →  Nuvrail SMTP Proxy  →  Provider SMTP
  Human personal clients (Apple Mail, etc.)  →  Provider directly (gateway not in path)
```

### Components

| Component | Role |
|---|---|
| **IMAP Proxy Server** | Presents as IMAP to AI agent; intercepts writes; passes reads |
| **SMTP Proxy Server** | Presents as SMTP to AI agent; stages all sends for approval |
| **Local State DB** | Mirror of mailbox state (headers, flags, folder structure) |
| **Staging Engine** | Parses intercepted commands → creates Operation records + descriptions |
| **Staging Queue** | Pending write/send operations awaiting approval |
| **Approval REST API** | HTTP API for the approval app to fetch pending ops and submit decisions |
| **Approval App** | Web/PWA + iOS interface for reviewing and approving/rejecting operations |
| **Notification Service** | Push notifications to user when operations are staged; fallback to email digest |
| **Audit Log** | Immutable append-only record of all operations and decisions |

---

## 4. Security & Authentication Model

Three distinct access lanes. No credential from one lane can be used in another. No lane bypasses the staging flow.

### 4.1 Lane 1 — Provider credentials (internal only)

The gateway authenticates to real email providers (Google, Outlook, etc.) using OAuth2 tokens obtained during user registration.

- OAuth2 refresh tokens are stored **encrypted at rest** using AES-256 with per-user derived keys
- Access tokens are obtained at connection time using the refresh token and held only in memory
- Provider credentials are **never exposed** to the AI agent or to the human-facing app
- If a refresh token is revoked, the gateway surfaces a reconnection prompt to the user via the approval app

### 4.2 Lane 2 — AI agent credentials (IMAP/SMTP access)

AI agents authenticate to the Nuvrail IMAP and SMTP proxy using Nuvrail-issued credentials:

- **Username:** the user's email address (e.g., `martin@example.com`)
- **Password:** a Nuvrail-generated random token (32 bytes, base58-encoded), stored as a bcrypt hash
- Credentials are generated at account setup, shown once, and never retrievable in plaintext
- The agent configures its email client with these credentials in place of the real provider credentials
- **Every write and send through these credentials is staged — no exceptions, no privilege levels that bypass staging**
- Credentials can be revoked and re-issued without touching provider OAuth2 tokens
- Multiple agent credential sets can be issued per account (Phase 2+); each carries its own `agent_id`

### 4.3 Lane 3 — Human credentials (web app / iOS app)

Humans authenticate to the approval web app and iOS app using:

- **Username:** email address
- **Password:** user-chosen, stored as bcrypt hash (min 12 rounds)
- Session managed via JWT (web) and secure token (iOS)
- No IMAP or SMTP access is granted by these credentials; they are approval-interface only

### 4.4 Personal client access (not through gateway)

Personal email clients (e.g., Apple Mail, Thunderbird used by a human) connect **directly to the email provider** — they are not routed through the Nuvrail gateway at all. This is intentional: the gateway is the AI lane only. Human personal clients bypass the gateway by design, not by privilege.

---

## 5. IMAP Proxy Server

### 5.1 Connection handling

- Listens on `993` (TLS) / `1143` (internal)
- Accepts IMAP4rev1 connections from AI agents authenticating with Lane 2 credentials
- Maintains a persistent upstream IMAP connection to the provider (XOAUTH2)
- Each AI session maps to one upstream provider connection

### 5.2 Read operations — pass-through

The following commands are forwarded directly to the provider and responses returned unmodified:

- `SELECT`, `EXAMINE` — open a mailbox
- `FETCH` — retrieve message headers, bodies, flags
- `SEARCH` — search messages
- `LIST`, `LSUB` — list folders
- `STATUS` — mailbox statistics
- `NOOP`, `CAPABILITY`, `ID`, `LOGOUT`

The local state DB is updated on each read to maintain a current mirror. This mirror is what the AI sees for flag states — important for consistent behavior during pending and rejected operations.

### 5.3 Write operations — staged

The following commands are **intercepted and staged**, not executed:

| IMAP Command | Proposed Action Type | Reversible |
|---|---|---|
| `STORE +FLAGS \Deleted` | Move to Trash (staged) | Yes |
| `STORE +FLAGS \Seen` | Mark as read | Yes |
| `STORE +FLAGS \Flagged` | Star / flag message | Yes |
| `STORE -FLAGS *` | Remove flags | Yes |
| `COPY uid folder` | Copy message to folder | Yes |
| `MOVE uid folder` | Move message (archive, label) | Yes |
| `EXPUNGE` | **Blocked permanently** | N/A |
| `APPEND folder` | Save to folder (draft only — see §5.5) | Yes |
| `CREATE folder` | Create new folder/label | Yes |
| `DELETE folder` | Delete folder — **blocked** | N/A |
| `RENAME folder` | Rename folder | Yes |

**On interception:**
1. Parse the command → generate a human-readable description (see §5.4)
2. Create an `Operation` record in the staging queue
3. Return `OK [STAGED] Operation queued for approval — ID: op_a3f9c2` to the AI agent
4. Update local state DB to reflect the **proposed** state (AI sees the change locally)
5. Notify the approval app; push notification sent to registered devices

**On rejection:**
1. Revert local state DB to pre-operation state
2. On next AI `SELECT`, `NOOP`, or `FETCH`, the gateway sends unsolicited `FETCH` responses reflecting the true state, followed by `EXPUNGE` / `EXISTS` corrections as appropriate
3. AI perceives this as another client modifying the mailbox — standard IMAP sync behavior
4. Log rejection to audit log

**On approval:**
1. Execute the real IMAP command against the provider
2. Update local state DB to reflect actual server state
3. Log approval + execution to audit log

**On execution failure (approved but provider returns error):**
1. Log the failure to audit log with full error detail
2. Revert local state DB
3. Set operation status to `failed`
4. Surface a visible alert in the approval app: "Operation op_a3f9c2 was approved but failed to execute — [details]. [Retry] [Dismiss]"
5. Do not silently swallow provider errors

### 5.4 Human-readable operation descriptions

The `description` field is the primary UX element of the approval flow. It must be accurate, concise, and unambiguous. Generated by the Staging Engine using a template system based on operation type + message metadata (sender name, subject, folder, count).

**Templates:**

| Operation | Description template |
|---|---|
| Archive (single) | `Archive 1 message from {sender} — "{subject}"` |
| Archive (batch) | `Archive {n} messages from {sender_list}` |
| Mark read | `Mark {n} message(s) as read from {sender_list}` |
| Move to folder | `Move "{subject}" to {folder}` |
| Label | `Apply label "{label}" to {n} message(s)` |
| Star | `Star {n} message(s) from {sender}` |
| Create folder | `Create new folder: "{folder_name}"` |
| Rename folder | `Rename folder "{old}" to "{new}"` |
| Move to Trash | `Move to Trash: {n} message(s) from {sender_list}` ⚠️ |
| SMTP send | `Send email to {recipient_list} — Subject: "{subject}"` ⚠️ |

Descriptions marked ⚠️ are shown with a warning indicator in the UI.
Sender names are resolved from local state DB (display name preferred over email address).
Batched operations targeting the same folder within the batching window produce a single combined description.

### 5.5 EXPUNGE blocking and APPEND handling

**EXPUNGE:** Never forwarded to the provider. Messages flagged `\Deleted` by the AI are staged as "Move to Trash" operations. Provider's trash retention (e.g., Gmail 30 days) provides an additional safety window.

**APPEND to Sent folder (AI sending email):** All `APPEND` to a Sent folder is blocked at the IMAP layer. AI sends must go through the SMTP proxy, which stages them for explicit approval. There is no path for an AI agent to record a sent message without a corresponding approved SMTP send. See §6.

**APPEND to Drafts folder:** Staged as a normal write operation. User approves saving the draft. This is not a send.

### 5.6 Operation expiry

Pending operations that have not been approved or rejected within **48 hours** are automatically:
1. Set to status `expired` (not `rejected` — distinct for auditability)
2. Local state reverted
3. Logged to audit log with event `expired`
4. User notified via the approval app

The 48-hour window is configurable per account. Urgency escalation (see §8.3) fires at the 12-hour mark if a pending operation remains unactioned.

---

## 6. SMTP Proxy Server

IMAP and SMTP launch together. The trust model is incomplete if an AI agent can be blocked from email writes but allowed to send freely.

### 6.1 Connection handling

- Listens on `587` (STARTTLS) / `1587` (internal)
- Accepts SMTP connections from AI agents using Lane 2 credentials
- AI agent authenticates with `AUTH PLAIN` or `AUTH LOGIN` using email + Nuvrail agent token
- Upstream relay to provider uses XOAUTH2

### 6.2 Send interception — always staged

All SMTP sends are **always staged, always require explicit human approval, no exceptions.**

This includes: `MAIL FROM`, `RCPT TO`, `DATA` sequences. The complete message (headers + body) is captured and stored as a staged send operation.

Staging response to AI agent: `250 OK [STAGED] Send queued for approval — ID: op_b8d2e1`

There is no auto-approval rule category that covers sends to external recipients. This is a hard policy, not a configuration option.

### 6.3 On approval

1. Relay the message to the provider SMTP using XOAUTH2
2. Log approval + relay confirmation to audit log
3. Provider delivery confirmation (250 OK) logged

### 6.4 On rejection

1. Log rejection to audit log
2. No message is sent
3. On AI agent's next SMTP session, if it queries status: `550 Message rejected by Nuvrail approval gateway`

### 6.5 On execution failure

Same as IMAP execution failure (§5.3): log, alert in approval app, do not silently discard.

---

## 7. Staging Queue

### 7.1 Operation schema

```sql
CREATE TABLE staged_operations (
    id            TEXT PRIMARY KEY,       -- op_a3f9c2 (short random ID)
    created_at    INTEGER NOT NULL,       -- unix timestamp
    expires_at    INTEGER NOT NULL,       -- created_at + 48h (configurable)
    status        TEXT NOT NULL,          -- 'pending' | 'approved' | 'rejected' | 'executed' | 'failed' | 'expired' | 'undone'
    op_type       TEXT NOT NULL,          -- 'archive' | 'label' | 'flag' | 'move' | 'append_draft' | 'smtp_send' | ...
    protocol      TEXT NOT NULL,          -- 'imap' | 'smtp'
    imap_command  TEXT,                   -- raw IMAP command (null for smtp ops)
    smtp_envelope TEXT,                   -- JSON: {from, to, subject, body_preview} (null for imap ops)
    description   TEXT NOT NULL,          -- human-readable (see §5.4 templates)
    is_urgent     INTEGER NOT NULL DEFAULT 0,  -- 1 if escalated (see §8.3)
    agent_id      TEXT,                   -- which agent credential set proposed this (null = Phase 0)
    message_ids   TEXT,                   -- JSON array of affected UIDs
    folder_from   TEXT,
    folder_to     TEXT,
    flags_add     TEXT,                   -- JSON array
    flags_remove  TEXT,                   -- JSON array
    decided_at    INTEGER,
    decided_by    TEXT,                   -- 'human' | 'auto_rule'
    executed_at   INTEGER,
    undo_expires_at INTEGER,              -- timestamp after which undo is no longer available
    error         TEXT
);
```

### 7.2 Batching

Operations targeting the same folder within a configurable window (default: 30 seconds) are grouped into a single staged batch for approval. Prevents a flood of individual approvals when the AI processes a full inbox scan.

Example combined description:
```
Archive 47 messages (Pottery Barn, Williams-Sonoma, Gap)
Mark 8 messages as read (newsletters)
Label 3 messages → "Needs Response"
```

SMTP sends are **never batched** — each send is its own approval card, always.

### 7.3 Undo window

Approved and executed reversible operations can be undone for **24 hours** after execution (`undo_expires_at = executed_at + 86400`). After 24 hours, the [Undo] button is removed from the UI and the field is no longer acted on.

SMTP sends that have already been relayed to the provider cannot be undone via Nuvrail (the message has left). The UI makes this clear.

### 7.4 Auto-approval rules (Phase 3)

Users can define rules that auto-approve low-risk IMAP operations without notification:
- `"Auto-approve: mark as read, sender matches /newsletter|noreply/"`
- `"Auto-approve: archive, sender in [known-safe list], no reply in thread"`

**Sends are never eligible for auto-approval rules.** This is a hard policy.

Auto-approved operations are logged to the audit trail with `decided_by = 'auto_rule'` and the matching rule ID.

---

## 8. Approval REST API

Base URL:
- Testing: `https://test.nuvrail.com/api/v1`
- Production: `https://mail.nuvrail.com/api/v1`

All endpoints require a valid human session JWT (Lane 3 credentials). Lane 2 (agent) credentials cannot call the API.

### 8.1 Endpoints

**`GET /operations`**
Returns list of operations. Query params: `status=pending|approved|rejected|all|expired`, `protocol=imap|smtp`, `limit`, `offset`

**`GET /operations/:id`**
Full detail including affected message previews (sender, subject, date, proposed action, description).

**`POST /operations/:id/approve`**
Approve single operation. Triggers immediate execution against provider.

**`POST /operations/:id/reject`**
Reject single operation. Reverts local state.

**`POST /operations/batch/approve`**
Approve multiple operations by ID array. SMTP sends in a batch are approved individually in sequence.

**`POST /operations/batch/reject`**
Reject multiple operations.

**`POST /operations/:id/undo`**
Undo a previously approved+executed reversible operation (within undo window).

**`GET /audit`**
Query audit log. Params: `from`, `to`, `op_type`, `protocol`, `status`, `agent_id`.

**`GET /audit/:id`**
Full audit record including before/after state snapshot.

**`GET /audit/export`**
Export audit log as JSON. Params: `from`, `to`.

**`GET /rules`** / **`POST /rules`** / **`PUT /rules/:id`** / **`DELETE /rules/:id`**
Manage auto-approval rules (IMAP only).

**`GET /agents`** / **`POST /agents`** / **`DELETE /agents/:id`**
Manage AI agent credential sets. POST issues a new credential (token shown once in response). DELETE revokes.

---

## 9. Approval App

### 9.1 Platform

Progressive Web App (PWA) — works in browser on desktop and mobile, installable on iOS/Android home screen, supports push notifications via Web Push API.

iOS native app ships in Phase 1 (see Roadmap §1.1) with APNs for reliable push delivery.

### 9.2 Main views

**Pending** (default view)
List of pending operation batches and sends. Each card shows:
- Protocol badge (IMAP / SMTP)
- Operation type icon
- Human-readable description (from §5.4 templates)
- Warning indicator ⚠️ for Trash moves and sends
- Time queued / time remaining before expiry
- [Approve] [Reject] [Expand]

SMTP send cards are visually distinguished. They cannot be bulk-approved — each requires an individual action.

**Expanded view**
For IMAP batches: each message listed with sender, subject, date, proposed action. Approve/reject whole batch or individual items.
For SMTP sends: full message headers + body preview. To, CC, BCC, Subject, first 500 chars of body.

**Audit Log**
Scrollable timeline of all past operations. Each entry: timestamp, description, protocol, status, agent (Phase 2+), [Undo] within window.

**Rules**
Auto-approval rules (IMAP only). Add, edit, toggle, delete. Clear label: "Sends are never auto-approved."

**Agents**
Manage AI agent credentials. Issue new credentials, revoke existing, view per-agent activity.

### 9.3 Notifications

When an operation is staged:
1. Web Push notification (desktop/web) or APNs (iOS) sent to registered devices
2. Notification shows: description + [Approve] [Reject] action buttons (no need to open app)
3. **Quiet hours:** configurable, default 10pm–7am. SMTP sends and Trash moves override quiet hours (always delivered).

**Notification fallback:** If a Web Push notification is sent but not acknowledged within 1 hour (device offline, browser closed), a fallback email digest is sent to the user's registered address summarizing pending operations. This ensures no staging event is silently missed.

### 9.4 Urgency escalation

An operation is marked `is_urgent = 1` when:
- It involves a send (SMTP) — always urgent by default
- It involves moving messages to Trash — always urgent
- A user-defined rule marks it urgent (e.g., sender matches a VIP list)
- It has been pending for 12+ hours without a decision

Urgent operations:
- Override quiet hours
- Are shown at the top of the Pending view with a distinct visual treatment
- Trigger a second push notification at the 12-hour escalation point

---

## 10. Audit Log

Append-only table, never modified after insert:

```sql
CREATE TABLE audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     INTEGER NOT NULL,
    operation_id  TEXT REFERENCES staged_operations(id),
    event         TEXT NOT NULL,   -- 'staged' | 'approved' | 'rejected' | 'executed' | 'execution_failed' | 'undone' | 'auto_approved' | 'expired'
    actor         TEXT,            -- 'ai_agent' | 'human' | 'system' | 'auto_rule'
    agent_id      TEXT,            -- which agent credential set (null = Phase 0)
    detail        TEXT             -- JSON: before/after state, error messages, rule_id, etc.
);
```

Audit log is queryable via the API, viewable in the app, and exportable to JSON. It is never modified, truncated, or deleted. No UI or API path exists to remove audit log entries.

---

## 11. Rejection Handling — IMAP Mechanics

When the human rejects an IMAP operation:

1. **Local state revert**: The state DB reverts the flag/folder change
2. **Unsolicited response on next command**: On the AI's next IMAP command (`SELECT`, `NOOP`, or `FETCH`), the gateway sends unsolicited `FETCH` responses reflecting true state, followed by any `EXPUNGE` / `EXISTS` corrections
3. **AI agent perception**: From the agent's view, the mailbox state changed between commands — standard IMAP sync behavior (another client modified the mailbox). The agent re-syncs and sees the reverted state.

This requires no IMAP extensions and works with any compliant IMAP client or agent.

---

## 12. Failure Modes

| Failure | Behavior |
|---|---|
| Upstream IMAP connection drops while operation pending | Queue operations in local staging; surface reconnect status in approval app; retry upstream on reconnect |
| Approved operation fails on provider execution | Status → `failed`; local state reverted; alert shown in approval app with [Retry] option; logged to audit |
| Staging queue grows large (user not responding) | Operations expire per §5.6; fallback email digest sent per §9.3; user prompted on next app open |
| JWT session expires mid-approval | Approval UI prompts re-authentication; in-flight decision is not lost |
| Push notification not delivered | Fallback email digest after 1 hour (§9.3) |
| Provider OAuth2 token revoked | Gateway surfaces reconnection prompt in approval app; IMAP/SMTP connections suspended until re-authed |

---

## 13. Tech Stack

| Component | Technology |
|---|---|
| IMAP Proxy Server | Python + `asyncio` + custom IMAP4rev1 state machine |
| SMTP Proxy Server | Python + `asyncio` + `aiosmtpd` |
| Provider IMAP connection | `aioimaplib` + provider auth (XOAUTH2 or app-specific password) |
| Provider SMTP connection | `aiosmtplib` + provider auth (XOAUTH2 or app-specific password) |
| Provider abstraction | `ProviderConnection` interface; implementations for Google, Microsoft, Apple, Generic IMAP/SMTP |
| Token encryption | AES-256, per-user derived keys |
| Local State DB | SQLite via `aiosqlite` (dev) / PostgreSQL (prod) |
| Approval REST API | FastAPI |
| Push notifications (web) | Web Push via `pywebpush` |
| Push notifications (iOS) | APNs via `apns2` (Phase 1) |
| Approval Web App | React + Vite, PWA manifest |
| iOS App | SwiftUI + Swift (Phase 1) |
| Dev/run environment | `docker-compose.yml` — gateway + SMTP proxy + API + web app |
| Prod infrastructure | fly.io or Railway (Phase 0–2); AWS/GCP (Phase 3) |

---

## 14. Milestones

### Milestone 0 — Core IMAP proxy, read-only (Week 1)
- [ ] IMAP proxy accepts connections on `test.nuvrail.com`, authenticates to Google via OAuth2
- [ ] Lane 2 credential issuance (email + generated token); stored as bcrypt hash
- [ ] OAuth2 refresh tokens stored encrypted at rest
- [ ] All read commands pass through correctly
- [ ] Local state DB syncing from provider
- [ ] Verify standard IMAP client (Thunderbird) works through proxy transparently

### Milestone 1 — Write interception + staging (Week 2)
- [ ] Write commands intercepted and staged to SQLite
- [ ] EXPUNGE blocked; `\Deleted` → Trash staged
- [ ] Human-readable description generation (§5.4 templates)
- [ ] Operation expiry at 48h (§5.6)
- [ ] Basic REST API (`GET /operations`, `POST .../approve`, `POST .../reject`)
- [ ] Lane 3 auth (username/password + JWT) on API
- [ ] Approved operations execute against provider correctly
- [ ] Execution failure handling: alert + retry (§5.3, §12)
- [ ] Rejection reverts local state; AI sees revert on next sync

### Milestone 2 — Approval app + SMTP proxy + notifications + multi-provider (Weeks 3–4)
- [ ] React PWA with Pending / Audit / Agents views
- [ ] SMTP proxy on `test.nuvrail.com:587`; all sends staged
- [ ] SMTP send approval cards (always individual, never batched)
- [ ] Web Push notifications on staging; urgency escalation (§9.3, §9.4)
- [ ] Approve/reject from notification action buttons
- [ ] Batch grouping of related IMAP operations (§7.2)
- [ ] Undo button for reversible IMAP operations within 24h window (§7.3)
- [ ] Fallback email digest for unacknowledged push notifications
- [ ] `ProviderConnection` abstraction layer — clean interface separating gateway logic from provider-specific auth
- [ ] **Microsoft / Outlook** — OAuth2 (Azure AD personal + work accounts), XOAUTH2 for IMAP/SMTP against `outlook.office365.com` / `smtp.office365.com`
- [ ] **Apple / iCloud** — app-specific password flow (guided setup UI), stored AES-256 encrypted; IMAP `imap.mail.me.com`, SMTP `smtp.mail.me.com`
- [ ] **Generic IMAP/SMTP** — username + password auth (stored encrypted); user supplies host, port, TLS settings; covers Yahoo, Fastmail, self-hosted, and any standard IMAP/SMTP server
- [ ] Provider selection UI in onboarding flow: Google / Microsoft / Apple / Other
- [ ] Per-provider connection health indicator in approval app

### Milestone 3 — Polish + rules + deployment (Week 5)
- [ ] Auto-approval rules engine for IMAP operations (sends excluded)
- [ ] Audit log export (JSON)
- [ ] Quiet hours with urgency override
- [ ] `docker-compose` single-command deployment
- [ ] Deployment to `test.nuvrail.com` (internal testing)
- [ ] Deployment to `mail.nuvrail.com` (production)

---

## 15. Decisions Log

| # | Question | Decision |
|---|---|---|
| 1 | SMTP proxy? | Yes — IMAP and SMTP launch together (Milestone 2) |
| 2 | Multi-agent support? | Single agent credential set for Phase 0; `agent_id` field reserved in schema for Phase 2 |
| 3 | Calendar scope? | Deferred — email only; calendar APIs too fragmented |
| 4 | Google OAuth app? | Fresh Google Cloud Console project |
| 5 | Personal clients (Apple Mail, etc.)? | Connect directly to provider, not through gateway — gateway is AI lane only, not a privileged bypass |
| 6 | APPEND to Sent folder? | Blocked at IMAP layer; AI sends must go through SMTP proxy and be approved there |
| 7 | APPEND to Drafts folder? | Staged as normal IMAP write; requires approval |
| 8 | Single vs. multi-account? | Single account (Phase 0); multi-account planned for Phase 2 |
| 9 | Auto-approval for sends? | Never — sends always require explicit human approval, no exceptions |
| 10 | Operation expiry? | 48h for pending operations; 24h undo window for executed reversible operations |
| 11 | Urgency definition? | Sends and Trash moves always urgent; 12h pending ops escalated; user-defined VIP rules |
| 12 | Notification fallback? | Email digest after 1 hour of unacknowledged Web Push |
| 13 | Deployment URLs? | `test.nuvrail.com` (testing), `mail.nuvrail.com` (production) |
| 14 | Multi-provider support timeline? | Google (Milestone 0–1), Microsoft + Apple + Generic IMAP/SMTP (Milestone 2) — provider-agnostic from launch |

---

## 16. Open Questions

- [ ] Should the open source release include the full gateway + staging engine, or proxy only?
- [ ] Android app: Phase 2 alongside multi-provider, or later?
