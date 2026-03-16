# IMAP Approval Gateway — Project Specification
**Status:** Draft  
**Date:** March 8, 2026  
**Author:** Martin Modahl / Steven  

---

## 1. Overview

### Problem

AI agents that interact with email must be given raw IMAP credentials today — which grants unrestricted access. There is no mechanism for a human to review and approve proposed email operations before they execute, no audit trail, and no rollback path. This is the primary reason email access is the most dangerous thing to give an AI agent.

### Solution

An **IMAP Approval Gateway** — a local proxy server that sits between the AI agent and the real Google IMAP server. The AI agent believes it is talking to a normal IMAP server. All read operations pass through transparently. All write operations are **staged** — queued for human approval via a separate notification interface — before executing against Google.

The result: the AI can propose email changes freely, but nothing reaches the real mailbox without a human approving a readable diff.

### Design principles

1. **Propose before execute** — every write is staged, never immediate
2. **Readable diffs** — approvals show exactly what will change, in human language
3. **Immutable audit log** — every action (proposed, approved, rejected, executed) is logged forever
4. **No deletes** — permanent deletion is disabled at the gateway layer; archive/label only
5. **Standard IMAP on both ends** — no special client or agent modifications required

---

## 2. System Architecture

```
┌─────────────────┐         ┌───────────────────────────┐         ┌──────────────────┐
│   AI Agent      │  IMAP   │   IMAP Approval Gateway   │  IMAP   │  Google IMAP     │
│  (any client)   │◄───────►│                           │◄───────►│  (real mailbox)  │
└─────────────────┘         │  ┌─────────────────────┐  │         └──────────────────┘
                            │  │  Local State DB      │  │
                            │  │  (SQLite)            │  │
                            │  └──────────┬──────────┘  │
                            │             │               │
                            │  ┌──────────▼──────────┐  │
                            │  │  Staging Queue       │  │
                            │  │  (pending ops)       │  │
                            │  └──────────┬──────────┘  │
                            │             │               │
                            │  ┌──────────▼──────────┐  │
                            │  │  Approval REST API   │  │
                            │  └──────────┬──────────┘  │
                            └─────────────┼─────────────┘
                                          │ HTTP
                            ┌─────────────▼─────────────┐
                            │   Approval Web App / PWA  │
                            │   (push notifications)    │
                            └───────────────────────────┘
```

### Components

| Component | Role |
|---|---|
| **IMAP Proxy Server** | Presents as IMAP to AI agent; intercepts writes; passes reads |
| **Local State DB** | SQLite mirror of mailbox state (headers, flags, folder structure) |
| **Staging Queue** | Pending write operations awaiting approval |
| **Approval REST API** | HTTP API for the approval app to fetch pending ops and submit decisions |
| **Approval App** | Web/PWA interface for reviewing and approving/rejecting operations |
| **Notification Service** | Push notifications to user when operations are staged |
| **Audit Log** | Immutable append-only record of all operations and decisions |

---

## 3. IMAP Proxy Server

### 3.1 Connection handling

- Listens on localhost:1143 (non-privileged port, TLS optional internally)
- Accepts IMAP4rev1 connections from the AI agent
- Maintains a persistent upstream IMAP connection to Google (OAuth2 / XOAUTH2)
- Each AI session maps to one upstream Google connection

### 3.2 Read operations — pass-through

The following commands are forwarded directly to Google and responses returned unmodified:

- `SELECT`, `EXAMINE` — open a mailbox
- `FETCH` — retrieve message headers, bodies, flags
- `SEARCH` — search messages
- `LIST`, `LSUB` — list folders
- `STATUS` — mailbox statistics
- `NOOP`, `CAPABILITY`, `ID`, `LOGOUT`

The local state DB is updated on each read to maintain a current mirror. This mirror is what the AI sees for flag states — important for rejections.

### 3.3 Write operations — staged

The following commands are **intercepted and staged**, not executed:

| IMAP Command | Proposed Action Type | Reversible |
|---|---|---|
| `STORE +FLAGS \Deleted` | Archive / mark for deletion | Yes |
| `STORE +FLAGS \Seen` | Mark as read | Yes |
| `STORE +FLAGS \Flagged` | Star / flag message | Yes |
| `STORE -FLAGS *` | Remove flags | Yes |
| `COPY uid folder` | Copy message to folder | Yes |
| `MOVE uid folder` | Move message (archive, label) | Yes |
| `EXPUNGE` | **Blocked permanently** | N/A |
| `APPEND folder` | Save to folder (sent, draft) | Yes |
| `CREATE folder` | Create new folder/label | Yes |
| `DELETE folder` | Delete folder — **blocked** | N/A |
| `RENAME folder` | Rename folder | Yes |

**On interception:**
1. Parse the command → create an `Operation` record in the staging queue
2. Return `OK [STAGED] Operation queued for approval — ID: op_a3f9c2` to the AI agent
3. Update local state DB to reflect the **proposed** state (AI sees the change locally)
4. Notify the approval app

**On rejection:**
1. Revert local state DB to pre-operation state
2. On next AI `SELECT` or `NOOP`, IMAP server sends `EXPUNGE` + updated `FETCH` responses to signal the state change back
3. AI sees the operation as if it was never applied — standard IMAP sync behavior
4. Log rejection to audit log

**On approval:**
1. Execute the real IMAP command against Google
2. Update local state DB to reflect actual server state
3. Log approval + execution to audit log

### 3.4 EXPUNGE blocking

`EXPUNGE` permanently deletes messages marked `\Deleted`. The gateway **never forwards EXPUNGE to Google**. Instead:
- Messages flagged `\Deleted` by the AI are intercepted as a `MOVE to [Gmail]/Trash` staging operation
- User must explicitly approve the trash move
- Gmail's 30-day trash retention provides an additional safety window
- `\Deleted` flags are stripped from the local state mirror after staging

---

## 4. Staging Queue

### 4.1 Operation schema

```sql
CREATE TABLE staged_operations (
    id          TEXT PRIMARY KEY,       -- op_a3f9c2 (short random ID)
    created_at  INTEGER NOT NULL,       -- unix timestamp
    status      TEXT NOT NULL,          -- 'pending' | 'approved' | 'rejected' | 'executed' | 'failed'
    op_type     TEXT NOT NULL,          -- 'archive' | 'label' | 'flag' | 'move' | 'append' | ...
    imap_command TEXT NOT NULL,         -- raw IMAP command
    description TEXT NOT NULL,          -- human-readable: "Archive 12 messages from Pottery Barn"
    message_ids TEXT,                   -- JSON array of affected UIDs
    folder_from TEXT,
    folder_to   TEXT,
    flags_add   TEXT,                   -- JSON array
    flags_remove TEXT,                  -- JSON array
    decided_at  INTEGER,
    decided_by  TEXT,                   -- 'human' | 'auto_rule'
    executed_at INTEGER,
    error       TEXT
);
```

### 4.2 Batching

Operations targeting the same folder within a short window (configurable, default 30 seconds) are grouped into a single staged batch for approval. This prevents a flood of individual approvals when the AI processes a full inbox scan.

Example batch description:
```
Archive 47 messages (Pottery Barn, Williams-Sonoma, Gap)
Mark 8 messages as read (newsletters)
Label 3 messages → "Needs Response"
```

### 4.3 Auto-approval rules (optional, future)

Users can define rules that auto-approve low-risk operations without notification:
- `"Auto-approve: mark as read, sender matches /newsletter/"` 
- `"Auto-approve: archive, sender in [known-brands list], if no reply in thread"`

Auto-approved operations are still logged to the audit trail.

---

## 5. Approval REST API

Base URL: `http://localhost:8080/api/v1`

### Endpoints

**`GET /operations`**  
Returns list of pending operations, most recent first.  
Query params: `status=pending|approved|rejected|all`, `limit`, `offset`

**`GET /operations/:id`**  
Full detail for one operation including affected message previews (sender, subject, date).

**`POST /operations/:id/approve`**  
Approve a single operation. Triggers immediate execution against Google.

**`POST /operations/:id/reject`**  
Reject a single operation. Reverts local state.

**`POST /operations/batch/approve`**  
Approve multiple operations by ID array.

**`POST /operations/batch/reject`**  
Reject multiple operations.

**`GET /audit`**  
Query the audit log. Params: `from`, `to`, `op_type`, `status`.

**`GET /audit/:id`**  
Full audit record for one operation including before/after state.

**`GET /rules`** / **`POST /rules`** / **`DELETE /rules/:id`**  
Manage auto-approval rules.

---

## 6. Approval App

### 6.1 Platform

Progressive Web App (PWA) — works in browser on desktop and mobile, installable on iOS/Android home screen, supports push notifications via Web Push API. No app store required.

### 6.2 Main views

**Pending** (default view)  
List of pending operation batches. Each shows:
- Operation type icon
- Human-readable summary ("Archive 47 messages from 3 senders")
- Time queued
- [Approve] [Reject] [Expand]

**Expanded batch view**  
Shows each message affected: sender, subject, date, proposed action. User can approve/reject the whole batch or individual items.

**Audit Log**  
Scrollable timeline of all past operations. Each entry: timestamp, description, status (approved/rejected/auto), [Undo] button for reversible operations within their window.

**Rules**  
List of auto-approval rules. Add, edit, toggle, delete.

### 6.3 Notifications

When an operation is staged:
1. Web Push notification sent to registered devices
2. Notification shows: operation summary + [Approve] [Reject] action buttons directly in the notification (no need to open the app)
3. Configurable quiet hours (no notifications 10pm–7am unless flagged urgent)

---

## 7. Audit Log

Append-only table, never modified after insert:

```sql
CREATE TABLE audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     INTEGER NOT NULL,
    operation_id  TEXT REFERENCES staged_operations(id),
    event         TEXT NOT NULL,   -- 'staged' | 'approved' | 'rejected' | 'executed' | 'undone' | 'auto_approved'
    actor         TEXT,            -- 'ai_agent' | 'human' | 'system'
    detail        TEXT             -- JSON: before/after state, error messages, etc.
);
```

Audit log is queryable via the API and viewable in the app. Export to JSON available.

---

## 8. Rejection Handling — IMAP Mechanics

When the human rejects an operation, the AI agent must be made aware. Since IMAP is a stateful protocol, here's the flow:

1. **Local state revert**: The state DB reverts the flag/folder change
2. **Unsolicited response on next command**: On the AI's next IMAP command (any `SELECT`, `NOOP`, or `FETCH`), the gateway sends unsolicited `FETCH` responses reflecting the true state (e.g., flag removed), followed by any applicable `EXPUNGE` or `EXISTS` count corrections
3. **AI agent perception**: From the agent's view, the mailbox state changed between commands — which is normal IMAP behavior (another client modified the mailbox). The agent should re-sync and see the reverted state.

This requires no IMAP extensions and works with any compliant IMAP client/agent.

---

## 9. Tech Stack

| Component | Technology |
|---|---|
| IMAP Proxy Server | Python + `asyncio` + custom IMAP4 state machine |
| Google IMAP connection | `aioimaplib` (async IMAP client) + OAuth2 |
| Local State DB | SQLite (via `aiosqlite`) |
| Approval REST API | FastAPI |
| Push notifications | Web Push (via `pywebpush`) |
| Approval App | React + Vite, PWA manifest |
| Dev/run environment | Single `docker-compose.yml` for gateway + API + app |

---

## 10. Milestones

### Milestone 0 — Core proxy, read-only (Week 1)
- [ ] IMAP proxy accepts connections, authenticates to Google via OAuth2
- [ ] All read commands pass through correctly
- [ ] Basic local state DB syncing from Google
- [ ] Verify any standard IMAP client (Thunderbird) works through the proxy transparently

### Milestone 1 — Write interception + staging (Week 2)
- [ ] Write commands intercepted and staged to SQLite
- [ ] EXPUNGE blocked, \Deleted → archive staged
- [ ] Basic REST API (`GET /operations`, `POST /operations/:id/approve`, reject)
- [ ] Approved operations execute against Google correctly
- [ ] Rejection reverts local state, AI sees revert on next sync

### Milestone 2 — Approval app + notifications (Week 3)
- [ ] React PWA with Pending / Audit views
- [ ] Web Push notifications on staging
- [ ] Approve/reject from notification action buttons
- [ ] Batch grouping of related operations

### Milestone 3 — Polish + rules (Week 4)
- [ ] Auto-approval rules engine
- [ ] Audit log export
- [ ] Undo button for reversible operations
- [ ] Quiet hours for notifications
- [ ] Docker-compose single-command deployment

---

## 11. Decisions Log

| # | Question | Decision |
|---|---|---|
| 1 | SMTP proxy? | Yes — build after IMAP; same staging/approval model |
| 2 | Multi-agent support? | Single AI agent for now; multi-session not required |
| 3 | Calendar scope? | Deferred — IMAP only; calendar APIs too fragmented |
| 4 | Google OAuth app? | Fresh Google Cloud Console project; no existing app |
| 5 | Martin bypasses gateway? | Yes — Martin's personal clients (Apple Mail, etc.) connect directly to Google; gateway is AI-only lane |

## 12. Remaining Open Questions

- [ ] Should APPEND to Sent folder (AI drafting → sending) be staged like other writes, or auto-approved once the send itself is approved?
- [ ] Single Gmail account (mmodahl@gmail.com) only, or multiple accounts eventually?
