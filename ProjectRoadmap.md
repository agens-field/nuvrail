# Nuvrail — Project Roadmap
**Owner:** KC (CTO)  
**Status:** Active  
**Last Updated:** 2026-03-16  
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

## A Note on the Original Milestone Structure

The SPEC lists 4 milestones across 4 weeks. Each milestone bundles too many independent concerns. When you're building for the first time, parallel concerns within a milestone mean you can't test incrementally — you have to get everything working at once before you can verify anything.

The restructured approach below breaks each SPEC milestone into 2-3 sub-milestones that can each be independently verified. **Each sub-milestone ends with a thing you can run and test.** That's the bar.

---

## Phase 0: Single-User IMAP Proof of Concept

**Duration:** ~4 weeks  
**Goal:** Martin can register a Gmail account, connect an AI agent, propose email operations, and approve/reject from a web UI with push notifications.  
**Scope:** Single user, single Gmail account, IMAP only, local deployment.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Phase 0 Build Order                         │
│                                                                 │
│  0.0 Dev env + OAuth2 setup                                     │
│   └─► 0.1 Raw TCP async proxy (pass-through, hardcoded auth)    │
│         └─► 0.2 IMAP command parser + read routing              │
│               └─► 0.3 OAuth2 / XOAUTH2 real auth               │
│                     └─► 0.4 Local state DB                      │
│                           └─► 1.1 Write interception + staging  │
│                                 └─► 1.2 Approval REST API       │
│                                       └─► 1.3 Execute + reject  │
│                                             └─► 2.1 React PWA   │
│                                                   └─► 2.2 Push + batch
│                                                         └─► 3.1 Docker
│                                                               └─► 3.2 Undo + audit
│                                                                     └─► 3.3 Rules
└─────────────────────────────────────────────────────────────────┘
```

Each arrow means: can't start the next until you can demo the current one to yourself.

---

### Sub-milestone 0.0 — Dev Environment + Google OAuth2 Setup

**Time estimate:** 1–2 days  
**Deliverable:** You can authenticate to Gmail's IMAP server via OAuth2 in a Python script.

This sub-milestone exists because OAuth2 + XOAUTH2 is the biggest external dependency. If Google's consent flow breaks you, find out now, not in week 2.

#### Tasks

**Environment:**
- [ ] Python 3.11+ virtual environment
- [ ] Install: `aioimaplib`, `fastapi`, `uvicorn`, `aiosqlite`, `google-auth`, `google-auth-oauthlib`, `pywebpush`
- [ ] `docker` + `docker-compose` installed and working
- [ ] `pyproject.toml` or `requirements.txt` committed

**Google Cloud Console:**
- [ ] Create new project: `nuvrail-dev`
- [ ] Enable Gmail API
- [ ] Create OAuth2 credentials (type: Desktop app for Phase 0; Web app in Phase 1)
- [ ] Add test user: mmodahl@gmail.com (required for unverified apps)
- [ ] Download `client_secret.json`; add to `.gitignore` immediately

**XOAUTH2 smoke test:**
- [ ] Write `scripts/test_gmail_auth.py`:
  - Run OAuth2 consent flow (opens browser, redirects to localhost)
  - Exchange code for access + refresh tokens
  - Store tokens to `~/.nuvrail/tokens.json`
  - Connect to `imap.gmail.com:993` via `aioimaplib`
  - Issue `SELECT INBOX` and print response
  - Issue `SEARCH ALL` and print UID count
- [ ] Script runs successfully end-to-end
- [ ] Token refresh path tested: manually expire token, verify re-auth works

**Exit criteria:** Running `python scripts/test_gmail_auth.py` connects to Gmail, selects INBOX, and returns a message count. No proxy yet — just direct auth.

---

### Sub-milestone 0.1 — Raw Async TCP Proxy (Pass-Through)

**Time estimate:** 2–3 days  
**Deliverable:** Thunderbird connects through the proxy and can read email. Auth is hardcoded (app password or static token). No IMAP parsing yet.

The goal here is to prove the asyncio bidirectional pipe architecture before adding any logic to it. If this takes more than 3 days, there is an architecture problem.

#### Tasks

**Proxy skeleton:**
- [ ] `gateway/proxy.py`: asyncio TCP server on `localhost:1143`
- [ ] On client connect: open upstream connection to `imap.gmail.com:993` (TLS)
- [ ] Bidirectional byte pump: client → upstream, upstream → client
- [ ] Log all bytes in both directions (debug mode only)
- [ ] Graceful close: when either side disconnects, close the other

**Auth handling (hardcoded for now):**
- [ ] Accept any `LOGIN` command from client
- [ ] Replace with upstream XOAUTH2 using static token from `test_gmail_auth.py` output
- [ ] This is a temporary shim — will be replaced in 0.3
- [ ] Add a `TODO: replace with per-user OAuth2` comment

**Connection lifecycle:**
- [ ] Handle `CAPABILITY` response from upstream — pass through unmodified
- [ ] Handle `LOGOUT` — clean close on both sides
- [ ] Handle upstream disconnect (network error, token expiry) — send `* BYE` to client, log error

```
Client (Thunderbird)     Proxy (localhost:1143)       Google (imap.gmail.com:993)
        │                          │                              │
        │── CAPABILITY ──────────► │── CAPABILITY ─────────────► │
        │ ◄──── * CAPABILITY resp ─┤ ◄──── * CAPABILITY resp ────│
        │── LOGIN ───────────────► │  (intercept, replace with)  │
        │                          │── AUTHENTICATE XOAUTH2 ────► │
        │ ◄──── A001 OK logged in ─┤ ◄──── A001 OK ──────────────│
        │── SELECT INBOX ────────► │── SELECT INBOX ────────────► │
        │ ◄──── * 2847 EXISTS ─────┤ ◄──── * 2847 EXISTS ─────── │
```

**Testing:**
- [ ] Configure Thunderbird: IMAP server = localhost, port = 1143, no TLS, any username/password
- [ ] Thunderbird can see inbox, folder list, open messages
- [ ] `FETCH` of a message body returns the full message

**Exit criteria:** Thunderbird works through the proxy indistinguishably from connecting to Gmail directly. Zero IMAP parsing has been done — it's a dumb byte pipe with one substitution (LOGIN → XOAUTH2).

---

### Sub-milestone 0.2 — IMAP Command Parser + Read/Write Classification

**Time estimate:** 3–4 days  
**Deliverable:** Proxy correctly classifies every IMAP command as read (pass-through) or write (stub-intercept). Read commands work. Write commands return a stub `OK [STAGED]` and are logged to stdout.

This is where you build the IMAP state machine. It's the hardest single component. Do it before OAuth2 and DB so you can test in isolation.

#### Architecture

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
│  (pass-through) │  SUBSCRIBE, UNSUBSCRIBE, NAMESPACE
│                 │
│  WRITE class:   │  STORE, COPY, MOVE, APPEND, CREATE, DELETE,
│  ──────────►    │  RENAME, EXPUNGE, UID STORE, UID COPY, UID MOVE
│  (intercept)    │
└─────────────────┘
```

#### Tasks

**IMAP parser:**
- [ ] `gateway/imap_parser.py`
- [ ] Parse tag + command from line: `A001 SELECT INBOX` → `{tag: "A001", cmd: "SELECT", args: ["INBOX"]}`
- [ ] Handle `UID` prefix: `A001 UID FETCH 1:* (FLAGS)` → `{tag: "A001", uid: True, cmd: "FETCH", args: [...]}`
- [ ] Handle IMAP literals: `{12+}\r\n<12 bytes>` inline in command lines (needed for APPEND)
- [ ] Handle multi-line commands (APPEND is the main case)
- [ ] Handle command continuation (`+ OK` prompts for literals)
- [ ] Unit tests: parse 20+ real IMAP command lines from Thunderbird/AI agent captures

**Command classifier:**
- [ ] `gateway/command_router.py`
- [ ] `classify(parsed_command) → "read" | "write" | "blocked"`
- [ ] `EXPUNGE` and `DELETE folder` → `"blocked"`
- [ ] Everything else write → `"write"`
- [ ] Unit tests: classifier returns correct class for every command in the spec table

**Proxy integration:**
- [ ] Replace byte pump with line-by-line parsing
- [ ] Read commands: forward to upstream, return response to client (existing behavior)
- [ ] Write commands: log `INTERCEPTED: {command}` to stdout, return `{tag} OK [STAGED] op_stub` to client
- [ ] Blocked commands: log `BLOCKED: {command}`, return `{tag} OK [BLOCKED]` (EXPUNGE specific: see note)
- [ ] State tracking: current session's selected mailbox

**EXPUNGE handling note:**  
Never forward EXPUNGE. The stub response must be plausible: `A001 OK [READ-WRITE] EXPUNGE completed` — the AI agent should believe it worked. Real revert happens via local state (later).

**Testing:**
- [ ] Thunderbird still works (all reads pass through)
- [ ] Issue write commands from a test script (STORE, COPY, MOVE): get `OK [STAGED]` back, nothing changes on Gmail
- [ ] Issue EXPUNGE: get back OK, nothing deleted on Gmail
- [ ] Capture and replay 50+ real IMAP sessions from Thunderbird/mutt; all classify correctly

**Edge cases to handle explicitly:**
- `UID FETCH` vs `FETCH` (sequence vs UID addressing)
- `STORE 1:* +FLAGS.SILENT (\Seen)` — the `.SILENT` modifier suppresses FETCH responses
- `APPEND` with very large literals (draft saving)

**Exit criteria:** Every IMAP command is correctly classified. Reads pass through. Writes return stub OK. Nothing is written to Gmail during any write operation.

---

### Sub-milestone 0.3 — OAuth2 / XOAUTH2 Real Authentication

**Time estimate:** 1–2 days  
**Deliverable:** Proxy authenticates to Gmail using real OAuth2 tokens, not hardcoded credentials. Token refresh works automatically.

This is a targeted replacement of the hardcoded auth shim from 0.1. Short milestone — keep it isolated.

#### Tasks

**Token store:**
- [ ] `gateway/token_store.py`
- [ ] Load/save tokens from `~/.nuvrail/tokens.json` (Phase 0: single user only)
- [ ] `get_access_token()`: return current token or refresh if expired
- [ ] `refresh_token()`: call Google token endpoint, update stored tokens
- [ ] Handle refresh failure: raise `AuthenticationError` (proxy sends `* BYE Authentication failed`)

**XOAUTH2 string generation:**
- [ ] `build_xoauth2_string(user_email, access_token) → base64_string`
- [ ] Format: `user=<email>\x01auth=Bearer <token>\x01\x01`
- [ ] Unit test: output matches Google's documented XOAUTH2 format exactly

**Proxy auth integration:**
- [ ] On client `LOGIN` (or `AUTHENTICATE PLAIN`): extract username (ignored for now), call `get_access_token()`
- [ ] Issue `AUTHENTICATE XOAUTH2` to upstream with generated string
- [ ] On upstream `OK`: return `{tag} OK Logged in` to client
- [ ] On upstream auth failure: call `refresh_token()`, retry once; if still fails, return `{tag} NO Authentication failed`
- [ ] On subsequent upstream connections: use same token store

**Testing:**
- [ ] Thunderbird connects with any username/password (proxy ignores, uses real OAuth2)
- [ ] Force token expiry (delete access_token from file, keep refresh_token); verify auto-refresh works
- [ ] Force refresh token expiry; verify proxy returns auth error cleanly (no crash)

**Exit criteria:** Proxy authenticates to Gmail via real OAuth2. Token refresh is automatic and transparent. No credentials in code or config files (only in `~/.nuvrail/tokens.json`, gitignored).

---

### Sub-milestone 0.4 — Local State DB

**Time estimate:** 2–3 days  
**Deliverable:** Proxy maintains a SQLite mirror of mailbox state (folders, message headers, flags). DB is kept in sync as the AI reads.

The local state DB is what makes write interception possible. When the AI issues a write, the proxy updates the DB optimistically (AI sees the change). On rejection, the DB is reverted. The DB is the source of truth for what the AI believes the mailbox looks like.

#### Schema

```sql
-- Folders / labels
CREATE TABLE folders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,     -- e.g. "INBOX", "[Gmail]/Sent Mail"
    uidvalidity  INTEGER,
    uidnext      INTEGER,
    exists_count INTEGER,
    recent_count INTEGER,
    unseen_count INTEGER,
    last_synced  INTEGER                   -- unix timestamp
);

-- Messages (header-level only; bodies not cached)
CREATE TABLE messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id    INTEGER REFERENCES folders(id),
    uid          INTEGER NOT NULL,
    sequence_num INTEGER,                  -- may change; uid is stable
    message_id   TEXT,                     -- Message-ID header
    subject      TEXT,
    sender       TEXT,
    date_sent    INTEGER,
    size         INTEGER,
    flags        TEXT NOT NULL DEFAULT '[]',  -- JSON array: ["\\Seen", "\\Flagged"]
    last_updated INTEGER,
    UNIQUE(folder_id, uid)
);

-- Staged operations (Phase 1; schema defined here for reference)
CREATE TABLE staged_operations (
    id           TEXT PRIMARY KEY,         -- op_a3f9c2
    created_at   INTEGER NOT NULL,
    status       TEXT NOT NULL,            -- pending|approved|rejected|executed|failed
    op_type      TEXT NOT NULL,
    imap_command TEXT NOT NULL,
    description  TEXT NOT NULL,
    message_ids  TEXT,                     -- JSON array of UIDs
    folder_from  TEXT,
    folder_to    TEXT,
    flags_add    TEXT,                     -- JSON array
    flags_remove TEXT,                     -- JSON array
    snapshot     TEXT,                     -- JSON: pre-op state for rollback
    batch_id     TEXT,                     -- groups related operations
    decided_at   INTEGER,
    decided_by   TEXT,
    executed_at  INTEGER,
    error        TEXT
);

-- Audit log (Phase 1; schema defined here for reference)
CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    INTEGER NOT NULL,
    operation_id TEXT REFERENCES staged_operations(id),
    event        TEXT NOT NULL,            -- staged|approved|rejected|executed|undone|auto_approved
    actor        TEXT,
    detail       TEXT                      -- JSON: before/after, errors
);
```

#### Tasks

**DB initialization:**
- [ ] `gateway/state_db.py`
- [ ] `init_db(path)`: create tables if not exist; run migrations if schema changes
- [ ] DB path: `~/.nuvrail/state.db` (Phase 0: single user)
- [ ] `aiosqlite` throughout — no blocking DB calls in async context

**Folder sync:**
- [ ] On `SELECT mailbox` response: parse `* N EXISTS`, `* N RECENT`, `[UIDVALIDITY N]`, `[UIDNEXT N]`; upsert into `folders`
- [ ] On `LIST` response: upsert all returned folders
- [ ] `get_or_create_folder(name) → folder_id`

**Message sync:**
- [ ] On `FETCH` response: parse UIDs, sequence numbers, FLAGS, ENVELOPE (subject, sender, date)
- [ ] Upsert into `messages` table
- [ ] `update_flags(folder_id, uid, flags)` — used by both sync and optimistic write updates
- [ ] `get_message(folder_id, uid) → message_row`
- [ ] `get_messages_by_uid_set(folder_id, uid_set) → [message_row]`

**UID ↔ sequence number tracking:**  
IMAP has two addressing modes. UIDs are stable across sessions. Sequence numbers (1, 2, 3...) change when messages are added/removed. The local DB must maintain both. Write operations from AI agents may use either.
- [ ] `resolve_sequence_to_uid(folder_id, seq_num) → uid` — look up in `messages` table by `sequence_num`
- [ ] Sequence numbers are re-assigned on every `SELECT` response; rebuild on each `SELECT`

**Testing:**
- [ ] After connecting + doing `SELECT INBOX`, verify DB has correct folder row with EXISTS count
- [ ] After `FETCH 1:10 (UID FLAGS ENVELOPE)`, verify 10 message rows in DB
- [ ] Force a flag change (via direct Gmail → Thunderbird); on next `FETCH`, verify DB row updated
- [ ] Verify `get_messages_by_uid_set` handles UID ranges (`1:100`), UID lists (`1,3,5`), and `*` (last UID)

**Exit criteria:** After any read session, the local DB accurately reflects the mailbox state seen by the AI. No write operations yet.

---

### Sub-milestone 1.1 — Write Interception + Staging

**Time estimate:** 3 days  
**Deliverable:** Write commands are intercepted, parsed into structured operations, stored in `staged_operations`, and local state is updated optimistically. AI agent sees the proposed state. Nothing executes against Gmail.

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
│ Return OK        │  "{tag} OK [STAGED] Operation queued — ID: op_xxx"
│ to AI agent      │
└──────────────────┘
```

#### Tasks

**Operation parser:**
- [ ] `gateway/operation_parser.py`
- [ ] `parse_store(tag, uid_mode, uid_set, flags_op, flags)` → `{op_type, message_ids, flags_add, flags_remove, description}`
  - `+FLAGS (\Deleted)` → `op_type: "trash"`, description: `"Mark N messages for deletion"`
  - `+FLAGS (\Seen)` → `op_type: "mark_read"`
  - `+FLAGS (\Flagged)` → `op_type: "star"`
  - `-FLAGS (\Seen)` → `op_type: "mark_unread"`
  - `-FLAGS (\Flagged)` → `op_type: "unstar"`
- [ ] `parse_copy(tag, uid_set, destination_folder)` → `{op_type: "copy", ...}`
- [ ] `parse_move(tag, uid_set, destination_folder)` → `{op_type: "move", ...}`
  - Special case: MOVE to `[Gmail]/Trash` is `op_type: "trash"`
  - MOVE to `[Gmail]/Archive` is `op_type: "archive"`
- [ ] `parse_append(tag, folder, flags, date, message_size)` → `{op_type: "append", ...}`
- [ ] `parse_create(tag, folder_name)` → `{op_type: "create_folder", ...}`
- [ ] `parse_rename(tag, old_name, new_name)` → `{op_type: "rename_folder", ...}`
- [ ] `parse_expunge(tag)` → special handling: look up messages with `\Deleted` flag in current folder, create `op_type: "trash"` operations for each
- [ ] Human-readable description generator for each op type

**Staging engine:**
- [ ] `gateway/staging.py`
- [ ] `stage_operation(parsed_op, session_context) → operation_id`
  - Generate short random ID: `op_` + 6 hex chars
  - Take snapshot: serialize affected `messages` rows to JSON, store in `snapshot` column
  - Insert `staged_operations` row with `status=pending`
  - Update local state optimistically (flags, folder membership)
  - Insert `audit_log` row: `event=staged, actor=ai_agent`
- [ ] Return `operation_id` to proxy
- [ ] Proxy returns `{tag} OK [STAGED] Operation queued — ID: {operation_id}`

**\Deleted + EXPUNGE handling detail:**  
When AI issues `STORE uid +FLAGS (\Deleted)`:
- Create `op_type: "trash"` staged operation for that message
- Update local state: remove `\Deleted` flag (don't show it; we're converting to a move)
- When AI then issues `EXPUNGE`:
  - Check for pending "trash" operations in current folder
  - If they exist: return `{tag} OK EXPUNGE completed` without forwarding upstream (the pending op covers it)
  - If none pending: return `{tag} OK EXPUNGE completed` (nothing to do)
  - Never forward EXPUNGE upstream, ever

**Testing:**
- [ ] AI agent issues `STORE 1 +FLAGS (\Seen)`: staging DB has one row, local state shows `\Seen`
- [ ] AI agent issues `UID COPY 1:5 [Gmail]/Trash`: staging DB has one row, no Gmail change
- [ ] AI agent issues `UID MOVE 10 INBOX`: staging DB has one row
- [ ] AI agent issues `STORE 3 +FLAGS (\Deleted)` then `EXPUNGE`: single "trash" op in DB, nothing deleted on Gmail
- [ ] `staged_operations` row has correct snapshot (pre-op flags serialized)

**Exit criteria:** Every write command produces a `staged_operations` row with status=pending. Local state reflects proposed state. Zero Gmail writes.

---

### Sub-milestone 1.2 — Approval REST API

**Time estimate:** 2 days  
**Deliverable:** Running FastAPI server exposing all endpoints from the spec. Approve/reject work against the DB. (Execution against Gmail comes in 1.3.)

Keep this sub-milestone limited to the API surface and DB interaction. Don't connect execution yet — that's deliberately next.

#### Tasks

**FastAPI app:**
- [ ] `api/main.py`: FastAPI app, CORS for localhost:5173 (React dev server)
- [ ] `api/models.py`: Pydantic models for responses
- [ ] `api/routes/operations.py`: operations endpoints
- [ ] `api/routes/audit.py`: audit log endpoints
- [ ] Startup: connect to same `~/.nuvrail/state.db`

**Endpoints:**
- [ ] `GET /api/v1/operations` → list, params: `status=pending|approved|rejected|all`, `limit=50`, `offset=0`
  - Response includes: `id`, `status`, `op_type`, `description`, `created_at`, `message_ids`, `batch_id`
- [ ] `GET /api/v1/operations/:id` → full detail
  - Include message previews: for each UID in `message_ids`, look up `subject`, `sender`, `date_sent` from local DB
  - Include `snapshot` for context
- [ ] `POST /api/v1/operations/:id/approve` → set `status=approved`, `decided_at=now`, `decided_by=human`
  - Response: `{ok: true, operation_id: ...}` (execution happens in 1.3; for now just DB update)
- [ ] `POST /api/v1/operations/:id/reject` → set `status=rejected`, `decided_at=now`
  - Revert local state from snapshot (implemented here)
  - Set `needs_revert_notification=true` flag in session (proxy will pick this up in 1.3)
  - Append to `audit_log`
- [ ] `POST /api/v1/operations/batch/approve` → body: `{ids: [...]}`, approve all
- [ ] `POST /api/v1/operations/batch/reject` → body: `{ids: [...]}`, reject all
- [ ] `GET /api/v1/audit` → params: `from`, `to`, `op_type`, `status`, `limit=100`, `offset=0`
- [ ] `GET /api/v1/audit/:id` → full audit record

**State revert logic:**
- [ ] `api/revert.py`
- [ ] `revert_operation(operation_id)`:
  - Load `snapshot` JSON from `staged_operations`
  - For each message in snapshot: restore flags to pre-op values in `messages` table
  - For folder operations (create, rename): reverse the folder change in `folders` table
  - Mark operation `status=rejected` in DB
  - Write `audit_log` row: `event=rejected`
- [ ] This function is also called by the proxy in 1.3

**Testing:**
- [ ] Stage 3 operations via proxy (connect AI agent, issue write commands)
- [ ] `GET /api/v1/operations?status=pending` returns all 3
- [ ] `GET /api/v1/operations/:id` returns message preview (sender, subject) for affected messages
- [ ] `POST /api/v1/operations/:id/reject` → check `messages` table: flags reverted to snapshot values
- [ ] `GET /api/v1/audit` shows `staged` + `rejected` events for the operation

**Exit criteria:** Full REST API running. Approve/reject work against the DB. Reverts restore local state correctly. No Gmail execution yet.

---

### Sub-milestone 1.3 — Execution + Rejection Notification

**Time estimate:** 3 days  
**Deliverable:** Approved operations execute against Gmail. Rejected operations cause the AI agent to re-sync and see the reverted state on its next command.

This is the trickiest sub-milestone. The rejection notification path requires sending unsolicited IMAP responses to the AI's live session.

#### Execution path

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
│ Re-issue IMAP command│   Use the stored `imap_command` field
│ against upstream     │   via the per-user upstream IMAP connection
└──────────┬───────────┘
           │
     ┌─────┴─────┐
     │ success   │ failure
     ▼           ▼
Update DB:    Update DB:
status=       status=failed
executed      error=<msg>
     │
     ▼
audit_log:
event=executed
```

**Upstream connection manager:**
- [ ] `gateway/upstream.py`
- [ ] `UpstreamManager`: singleton (Phase 0: one user, one connection)
- [ ] `execute_imap_command(imap_command_str) → (ok: bool, response: str)`
  - Connect if not already connected
  - Issue the raw IMAP command
  - Return upstream response
  - Handle token refresh if needed
- [ ] On execution failure: log error, mark operation `status=failed`, do NOT revert (human must decide)
- [ ] Thread/task safety: API calls can trigger execution from a different async task than the proxy session

**API execution integration:**
- [ ] In `POST /operations/:id/approve`:
  - Call `execute_imap_command(operation.imap_command)`
  - On success: set `status=executed`, log to audit
  - On failure: set `status=failed`, return 500 with error detail
  - Update local state DB to reflect actual server state post-execution
- [ ] In `POST /operations/batch/approve`: execute sequentially (not parallel) to avoid IMAP ordering issues

#### Rejection notification path

When an operation is rejected, the AI agent's local view is now stale. The proxy must correct this on the agent's next IMAP command.

```
Rejection via API
       │
       ▼
┌─────────────────────┐
│ Revert local DB     │  (already done in 1.2)
│ state from snapshot │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Queue revert        │  per-session pending_notifications list
│ notifications for   │
│ the AI's session    │
└──────────┬──────────┘
           │
           ▼   (on next AI IMAP command)
┌─────────────────────┐
│ Proxy sends          │  Before forwarding the AI's command response,
│ unsolicited FETCH   │  inject these:
│ responses           │
└─────────────────────┘
```

**Unsolicited FETCH responses:**
- [ ] `gateway/notifications.py`
- [ ] `PendingNotifications`: per-session queue of unsolicited IMAP responses to inject
- [ ] `queue_revert(operation_id, session_id)`: build unsolicited responses from snapshot
  - For each reverted message: `* {seq} FETCH (UID {uid} FLAGS ({original_flags}))`
  - If messages were "moved" (copy+expunge staged): `* {seq} EXPUNGE` to remove from AI's view
- [ ] `flush_pending(session_id) → [str]`: return queued responses and clear queue
- [ ] In proxy: after forwarding upstream response to client, check `flush_pending(session_id)`; send each queued line to client

**The IMAP unsolicited response rules:**
- Unsolicited responses can only be sent when the client is not waiting for a response (i.e., between command completions), OR as part of the final response to a command
- Safe places to inject: before the tagged response to any command (`SELECT`, `NOOP`, `FETCH`, `SEARCH`)
- Format: `* {seq} FETCH (FLAGS (\\Seen))` — the `*` (untagged) prefix is the unsolicited marker
- The AI agent, being a compliant IMAP client, must handle these gracefully (this is standard IMAP)

**Testing (this is the most important test in Phase 0):**
- [ ] Setup: AI agent issues `STORE uid +FLAGS (\Seen)`; operation staged, local state = Seen
- [ ] Verify Gmail: message is NOT marked read (not executed yet)
- [ ] Reject via API
- [ ] AI agent issues `NOOP`
- [ ] Verify: proxy sends `* {seq} FETCH (FLAGS ())` before `A001 OK` response
- [ ] AI agent's next `FETCH FLAGS` shows original (unread) state
- [ ] Verify Gmail: message is still unread (correct)
- [ ] Separately: AI agent issues write → approve → verify Gmail reflects the change

**Exit criteria:** Approved operations execute correctly against Gmail. Rejected operations cause the AI to re-sync to pre-op state within one round-trip. Both paths logged to audit.

---

### Sub-milestone 2.1 — React PWA (Core UI)

**Time estimate:** 2–3 days  
**Deliverable:** A running React app where Martin can see pending operations and approve/reject them via a browser.

Keep it ugly. It needs to work, not look good yet.

#### Tasks

**App scaffold:**
- [ ] `web/` directory: `npm create vite@latest -- --template react-ts`
- [ ] TailwindCSS installed
- [ ] React Router v6: routes for `/`, `/audit`
- [ ] PWA plugin (`vite-plugin-pwa`): add manifest (name, icons, theme-color, display: standalone)
- [ ] Proxy dev server: `vite.config.ts` proxies `/api` to `localhost:8080`

**API client:**
- [ ] `web/src/api.ts`: typed fetch wrappers for all endpoints
- [ ] Types from API responses: `Operation`, `AuditEntry`
- [ ] Error handling: show error toast on failed API calls

**Pending view (`/`):**
- [ ] Fetch `GET /api/v1/operations?status=pending` on mount + every 10s (polling for now, push in 2.2)
- [ ] Operation card component:
  - Icon for op_type
  - Human-readable summary (`description` field from API)
  - Time queued (relative: "2 minutes ago")
  - [Approve] and [Reject] buttons
  - [Expand] toggle to show affected messages
- [ ] Expanded message list: sender, subject, date, proposed action per message
- [ ] Bulk select + [Approve All] / [Reject All] for batch operations
- [ ] Loading state; empty state ("No pending operations")

**Audit view (`/audit`):**
- [ ] Fetch `GET /api/v1/audit?limit=100`
- [ ] Timeline list: timestamp, operation summary, status badge (approved/rejected/auto/failed)
- [ ] [Undo] button placeholder (disabled until 3.2)

**Testing:**
- [ ] Stage 5 operations via proxy
- [ ] All 5 visible in pending view with correct descriptions
- [ ] Approve 2: verify via direct Gmail check that changes applied
- [ ] Reject 3: verify AI agent reverts (via manual NOOP trigger)
- [ ] Audit view shows all 5 with correct statuses

**Exit criteria:** Martin can open `localhost:5173`, see pending operations with message context, and approve or reject them. Changes execute correctly or revert correctly.

---

### Sub-milestone 2.2 — Web Push Notifications + Batching

**Time estimate:** 3 days  
**Deliverable:** Martin's browser gets a push notification when an operation is staged. Notification has [Approve] and [Reject] action buttons. Multiple related operations within 30 seconds are grouped into a batch.

**Note on Web Push:** Service workers require HTTPS in production. In development, `localhost` is an exempt origin — push works on `localhost:5173`. This does not require a cert for Phase 0. Don't spend time on HTTPS for local dev.

#### Batching

```
Write command arrives
         │
         ▼
┌──────────────────────┐
│ Look up recent       │  Any pending operations in same folder
│ operations (< 30s)   │  within the batch window?
└──────────┬───────────┘
      ┌────┴────┐
      │ yes     │ no
      ▼         ▼
Append to    Create new
existing     batch_id,
batch        create op
      └────┬────┘
           ▼
    Notify (batch-level)
```

**Tasks:**

**Batching logic:**
- [ ] `gateway/batching.py`
- [ ] `get_or_create_batch(folder, window_seconds=30) → batch_id`
  - Look for a recent pending batch in the same folder within `window_seconds`
  - Return existing `batch_id` or generate new one
- [ ] `staging.py`: pass `batch_id` when inserting `staged_operations`
- [ ] Batch description generator: "Archive 12 messages from 3 senders" (use sender fields from DB)
- [ ] API: `GET /api/v1/operations` groups by `batch_id` in response; returns batch-level summary + individual ops

**Web Push setup:**
- [ ] `gateway/push.py`
- [ ] Generate VAPID key pair on first run; store in `~/.nuvrail/vapid_keys.json`
- [ ] `POST /api/v1/push/subscribe` endpoint: save browser `PushSubscription` object to DB
  - Table: `push_subscriptions (id, endpoint, p256dh, auth, created_at)`
- [ ] `send_push(subscription, payload)`: call `pywebpush.webpush()`
- [ ] `notify_staged(operation_id)`: called from staging engine after inserting operation; sends push to all subscriptions

**Service worker:**
- [ ] `web/public/sw.js`
- [ ] `push` event listener: display notification using `self.registration.showNotification()`
  - Title: operation description
  - Body: "N messages affected"
  - Actions: `[{action: "approve", title: "Approve"}, {action: "reject", title: "Reject"}]`
  - Data: `{operation_id: "op_xxx"}`
- [ ] `notificationclick` event listener:
  - If action = "approve": `fetch('/api/v1/operations/{id}/approve', {method: 'POST'})`
  - If action = "reject": `fetch('/api/v1/operations/{id}/reject', {method: 'POST'})`
  - If click on notification body: `clients.openWindow('/')`
- [ ] PWA registration: `web/src/push.ts` — `navigator.serviceWorker.register('/sw.js')`; `subscribeToPush(vapidPublicKey)`; `POST /api/v1/push/subscribe`
- [ ] Subscribe on app load (request permission if not already granted)

**Pending view update:**
- [ ] Replace 10s polling with SSE or websocket for real-time updates (or keep polling — polling is fine for Phase 0)
- [ ] On push approve/reject: refetch pending list

**Testing:**
- [ ] AI agent issues 5 rapid STORE commands to INBOX within 10 seconds
- [ ] Verify: 1 notification appears (batched), not 5
- [ ] Click [Approve] in notification: operations execute, Gmail reflects changes
- [ ] Click [Reject]: operations revert, AI re-syncs
- [ ] AI agent issues commands to two different folders: 2 separate notifications

**Exit criteria:** Martin's browser receives push notifications with action buttons. Notification approve/reject works without opening the app. Related operations are batched sensibly.

---

### Sub-milestone 3.1 — Docker Compose + Single-Command Deployment

**Time estimate:** 1–2 days  
**Deliverable:** `docker-compose up` starts the entire system. Martin can deploy on a fresh machine by following a one-page README.

#### Tasks

**Dockerfiles:**
- [ ] `Dockerfile.gateway`: Python image, installs deps, runs `gateway/proxy.py` + `api/main.py` (same process via `asyncio.gather`, or two processes)
- [ ] `Dockerfile.web`: `node:alpine` build stage → `nginx:alpine` serve stage; nginx config proxies `/api` to gateway container
- [ ] Both images build cleanly from scratch

**docker-compose.yml:**
```yaml
services:
  gateway:
    build: {context: ., dockerfile: Dockerfile.gateway}
    ports:
      - "1143:1143"   # IMAP proxy
      - "8080:8080"   # REST API
    volumes:
      - nuvrail_data:/data      # SQLite DB + tokens + VAPID keys
    env_file: .env

  web:
    build: {context: web, dockerfile: Dockerfile.web}
    ports:
      - "3000:80"
    depends_on:
      - gateway
```

**Environment variables (.env.example):**
- [ ] `NUVRAIL_DATA_DIR=/data`
- [ ] `GOOGLE_CLIENT_ID=...`
- [ ] `GOOGLE_CLIENT_SECRET=...`
- [ ] `NUVRAIL_HOST_EMAIL=mmodahl@gmail.com`
- [ ] `LOG_LEVEL=info`

**Setup flow:**
- [ ] `scripts/setup.py`: first-run wizard
  - Checks `.env` for required vars
  - Opens browser for Google OAuth2 consent
  - Stores tokens to data volume
  - Generates VAPID keys
  - Prints "Setup complete. Run docker-compose up."
- [ ] README.md: 10-step getting started guide (clone → configure .env → setup.py → docker-compose up → configure AI agent)

**Testing:**
- [ ] `docker-compose build` succeeds
- [ ] `docker-compose up` starts both services
- [ ] Fresh machine: clone repo, run setup.py, run compose, connect AI agent, push notification fires

**Exit criteria:** One-command deployment. A developer with the `.env` file configured can have a running system in under 5 minutes.

---

### Sub-milestone 3.2 — Undo + Audit Export

**Time estimate:** 1–2 days  
**Deliverable:** Martin can undo a recently-approved operation from the audit log. Audit log is exportable to JSON.

#### Tasks

**Undo logic:**
- [ ] `api/undo.py`
- [ ] `undo_operation(operation_id)`:
  - Check: `status == executed` and `decided_at > now - 24h` and `op_type not in ["create_folder"]`
  - For `mark_read` / `mark_unread` / `star` / `unstar`: issue reverse STORE command upstream
  - For `copy`: issue UID `STORE +FLAGS (\Deleted)` + `EXPUNGE` for the copied message (find by Message-ID)
  - For `move` / `archive`: issue reverse MOVE command upstream
  - Update local state DB to reflect undo
  - Append `audit_log` row: `event=undone`
- [ ] `POST /api/v1/operations/:id/undo` endpoint
  - Returns 400 if operation not undoable (e.g., too old, not executed, no-undo op type)
- [ ] Undo window: configurable via env var `UNDO_WINDOW_HOURS=24`

**Audit UI:**
- [ ] Enable [Undo] button on audit cards where `can_undo=true`
- [ ] Confirmation modal: "Undo: Archive 3 messages? This will move them back to Inbox."
- [ ] On success: refresh audit list, show "Undone" badge on operation card

**Audit export:**
- [ ] `GET /api/v1/audit/export` → JSON file download
  - Content-Type: `application/json`
  - Content-Disposition: `attachment; filename="nuvrail-audit-{date}.json"`
  - Full audit log with operation details
- [ ] [Export] button in audit view

**Testing:**
- [ ] Approve a mark-read operation → verify Seen on Gmail → click Undo → verify Unseen on Gmail
- [ ] Approve a move-to-archive → Undo → verify message back in INBOX
- [ ] Attempt undo on a 25-hour-old operation: 400 error
- [ ] Export audit log: valid JSON, contains all events

**Exit criteria:** [Undo] works for reversible operations within the window. Audit log exports to JSON.

---

### Sub-milestone 3.3 — Auto-Approval Rules + Quiet Hours

**Time estimate:** 2–3 days  
**Deliverable:** Martin can define rules that auto-approve low-risk operations without notification. Notifications respect quiet hours.

**Graham check:** This is the first "nice to have" sub-milestone. If we're behind schedule, 3.3 can slip to Phase 1. Phase 0 is shippable after 3.1 + 3.2.

#### Tasks

**Rules schema:**
```sql
CREATE TABLE auto_approval_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 0,     -- lower = higher priority
    op_type     TEXT,                           -- null = any
    sender_pattern TEXT,                        -- regex, null = any
    folder_from TEXT,                           -- null = any
    action      TEXT NOT NULL,                  -- 'approve' | 'reject' | 'require_approval'
    description TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
```

**Rules engine:**
- [ ] `gateway/rules.py`
- [ ] `evaluate_rules(operation) → "approve" | "reject" | "require_approval" | None`
  - Test operation against enabled rules in priority order
  - First match wins
  - `None` = no rule matched = require approval (default)
- [ ] Called from staging engine before queuing notification
- [ ] On `"approve"`: execute immediately, log `event=auto_approved`, no push notification
- [ ] On `"reject"`: reject immediately, log `event=auto_rejected`, no push notification
- [ ] On `"require_approval"` or `None`: normal flow (queue notification)

**Rules API:**
- [ ] `GET /api/v1/rules` → list all rules, ordered by priority
- [ ] `POST /api/v1/rules` → create rule
- [ ] `PATCH /api/v1/rules/:id` → update (toggle enabled, change priority)
- [ ] `DELETE /api/v1/rules/:id` → delete

**Rules UI (web app):**
- [ ] `/rules` route: list of rules with toggle switches
- [ ] Add rule form: op_type selector, sender pattern input, action selector, description
- [ ] Drag-to-reorder for priority (or manual priority input)

**Quiet hours:**
- [ ] Config: `QUIET_HOURS_START=22` and `QUIET_HOURS_END=7` in `.env`
- [ ] `gateway/push.py`: before sending push, check current time against quiet hours
- [ ] During quiet hours: stage operation normally, but suppress push notification
- [ ] UI setting: toggle quiet hours on/off, set start/end times
- [ ] `GET /api/v1/config/quiet-hours`, `POST /api/v1/config/quiet-hours`

**Testing:**
- [ ] Create rule: "auto-approve mark-read where sender matches /newsletter/"
- [ ] AI marks a newsletter as read: auto-approved, no notification
- [ ] AI marks a personal email as read: notification fires normally
- [ ] Set quiet hours 22:00–07:00, trigger operation at 23:00: staged, no notification
- [ ] Rule priority: two matching rules → first match wins

**Exit criteria:** Auto-approval rules fire correctly. Quiet hours suppress notifications on schedule.

---

### Phase 0 Exit Criteria

All of the following must be true before Phase 0 is done:

- [ ] Martin registers Gmail via OAuth2 consent flow (one-time setup)
- [ ] AI agent (Python test script using `imaplib`) connects to proxy on localhost:1143 with any credentials
- [ ] AI agent issues `SELECT INBOX`, `SEARCH`, `FETCH`: all work correctly, no delay
- [ ] AI agent issues `STORE +FLAGS (\Seen)` on 5 messages: operation staged, local DB updated, no Gmail change
- [ ] Martin receives browser push notification within 3 seconds
- [ ] Martin clicks [Approve] in notification: Gmail messages marked read within 5 seconds
- [ ] Martin clicks [Reject] on a different operation: AI agent's next `NOOP` receives unsolicited FETCH; agent re-syncs to unread state
- [ ] AI agent issues `EXPUNGE`: no messages deleted, correct response returned
- [ ] All of the above is logged to the audit trail with correct events
- [ ] `docker-compose up` starts the full system from scratch
- [ ] Entire Phase 0 system is less than 3,000 lines of Python + JS combined

---

## Phase 1: SMTP Proxy + iOS App

**Duration:** Weeks 5–8  
**Goal:** Native iOS app with push notifications. SMTP proxy staging outbound sends for approval.

### Pre-conditions

Phase 0 fully complete and tested with real usage.

### Sub-milestone 1.0 — SMTP Proxy (Core)

**Time estimate:** 1 week  
**Goal:** AI agent's outbound emails are intercepted, staged, and require explicit approval before sending.

- [ ] `gateway/smtp_proxy.py`: asyncio SMTP server (aiosmtpd) on localhost:1025
- [ ] Intercept `MAIL FROM`, `RCPT TO`, `DATA` sequence
- [ ] Extract: from, to (all recipients), subject, body preview (first 500 chars)
- [ ] Stage as `op_type: "send"` with full headers + body stored (encrypted at rest)
- [ ] Return `250 OK Message staged for approval — ID: op_xxx` to AI agent
- [ ] On approval: relay to Gmail SMTP via XOAUTH2 (`smtp.gmail.com:587`)
- [ ] On rejection: do nothing (message never leaves)
- [ ] **Sends always require individual approval: no batching, no auto-approval rules for sends**
- [ ] Approval card shows: To, Subject, body preview, [Approve] [Reject]

### Sub-milestone 1.1 — iOS App

**Time estimate:** 2 weeks  
**Goal:** Native SwiftUI app with APNs push notifications. Approve/reject from notification.

- [ ] SwiftUI app: pending list, operation detail, audit log
- [ ] JWT auth (API key for Phase 1)
- [ ] APNs integration (requires Apple Developer account)
- [ ] Rich push notification: operation summary + [Approve] [Reject] action buttons
- [ ] Notification action handler: fires API call, updates badge count
- [ ] Quiet hours setting in app

### Sub-milestone 1.2 — Undo for Sends + Audit Polish

**Time estimate:** 3 days

- [ ] Approved sends: [Undo] visible for 5 minutes (not 24h — email is different)
- [ ] Undo send: if within window, notify recipient with a follow-up "please disregard" (can't unsend via SMTP; best effort)
- [ ] Audit log polish: filtering by op_type, date range
- [ ] iOS audit view

---

## Phase 2: Multi-Provider + Rules Engine

**Duration:** Weeks 9–14  
**Goal:** Support Outlook/Microsoft. More powerful rules. Multi-agent per user.

### Sub-milestone 2.0 — Provider Abstraction Layer

Before adding Outlook, refactor the Google-specific XOAUTH2 code into a provider interface:

```
ProviderConnection (interface)
    ├── GmailProvider (XOAUTH2, google-auth)
    └── OutlookProvider (MSAL, XOAUTH2 for Office365)
```

- [ ] `gateway/providers/base.py`: abstract `ProviderConnection`
- [ ] `gateway/providers/gmail.py`: extract existing Gmail code
- [ ] `gateway/providers/outlook.py`: MSAL Python + XOAUTH2 for `outlook.office365.com`
- [ ] All provider-specific OAuth flows isolated behind the interface

### Sub-milestone 2.1 — Microsoft / Outlook Support

- [ ] Azure AD app registration
- [ ] Microsoft OAuth2 flow (MSAL)
- [ ] IMAP XOAUTH2 against `outlook.office365.com:993`
- [ ] SMTP XOAUTH2 against `smtp.office365.com:587`
- [ ] Provider selection in setup flow

### Sub-milestone 2.2 — Yahoo + iCloud

- [ ] Yahoo OAuth2 → XOAUTH2
- [ ] iCloud app-specific password (guided setup UI)

### Sub-milestone 2.3 — Advanced Rules Engine

- [ ] Rule conditions: sender domain, thread participants, message size, time of day, folder
- [ ] Rule chaining: "if X and Y then Z"
- [ ] Regex + glob patterns for sender matching
- [ ] Per-rule dry-run mode: apply rule logic but still notify (testing before going live)
- [ ] Rule analytics: how many operations matched, auto-approved, auto-rejected

### Sub-milestone 2.4 — Multi-Agent + OAuth2 Server

- [ ] Multiple named AI agents per user (e.g., "LangChain agent", "OpenAI assistant")
- [ ] Each agent gets its own credentials + scope restrictions
- [ ] Nuvrail exposes `/oauth/authorize` + `/oauth/token` for agent auth
- [ ] Scopes: `email:read`, `email:write_staged`, `email:send_staged`
- [ ] Audit log shows which agent proposed each operation

---

## Phase 3: Platform + Public Launch

**Duration:** Weeks 15–20  
**Goal:** Multi-tenant, billing, developer API, App Store.

### Sub-milestone 3.0 — Multi-Tenancy + Postgres

The most important architectural change in the whole roadmap. Do this before billing.

- [ ] Replace per-user SQLite with Postgres (single instance, row-level tenant isolation via `user_id` FK on every table)
- [ ] Migration script: existing SQLite data → Postgres (needed for internal testing accounts)
- [ ] Per-user token encryption: derive user-specific AES key from master key + user_id (AWS KMS or env var for Phase 3)
- [ ] Connection pooling: `asyncpg` connection pool, not per-request connections

### Sub-milestone 3.1 — User Accounts + Registration

- [ ] User account model
- [ ] Registration flow: email + password (no OAuth2 for user accounts in Phase 3.1 — add later)
- [ ] Email account linking: each user adds Gmail/Outlook/etc. accounts
- [ ] JWT session management for web + iOS

### Sub-milestone 3.2 — Billing (Stripe)

- [ ] Stripe subscription integration
- [ ] Tier enforcement: account count, agent count, audit history retention
- [ ] Free / Pro / Team tier gating
- [ ] Billing portal (Stripe Customer Portal embed)

### Sub-milestone 3.3 — Developer API + Webhooks

- [ ] OpenAPI spec (auto-generated from FastAPI)
- [ ] Developer portal: register agent apps, view API keys
- [ ] Webhooks: POST to developer URL on staged, approved, rejected events
- [ ] SDK stubs: Python + TypeScript

### Sub-milestone 3.4 — App Store Submission

- [ ] Privacy policy + data handling disclosure
- [ ] App Store review preparation checklist
- [ ] TestFlight → production

### Sub-milestone 3.5 — Infrastructure Hardening

- [ ] Rate limiting on proxy connections (per-user, per-minute)
- [ ] Monitoring: uptime, proxy latency p95, staging queue depth (Grafana or similar)
- [ ] Alerting: PagerDuty or equivalent for proxy down, execution failures
- [ ] fly.io or Railway for Phase 3; evaluate AWS/GCP at 100 paying customers
- [ ] SOC2 Type I preparation: access logging, change management, vendor inventory

---

## Risk Register

These are the specific technical risks that are most likely to slip the schedule. Tracked here explicitly.

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | Google OAuth2 app review blocks production use | High | High | Use "Testing" mode (100 users max) for all of Phase 0–1; submit for verification in Phase 2 |
| R2 | IMAP state machine edge cases (UID vs seq, literals, partial fetch) | High | Medium | Build parser unit tests from real IMAP session captures before integration |
| R3 | Web Push browser compatibility (action buttons not supported in all browsers) | Medium | Low | Safari partial support is fine for Phase 0; Martin uses Chrome |
| R4 | Unsolicited FETCH rejection path breaks AI agent | Medium | High | Test with multiple IMAP clients (mutt, Thunderbird, Python imaplib); log all interactions |
| R5 | APPEND command with large literals (AI saving large drafts) | Medium | Medium | Test with 1MB+ APPEND early in 1.1; asyncio buffering can bite here |
| R6 | Postgres migration in Phase 3 requires downtime | Low | High | Design SQLite schema with migration in mind from day one; add `user_id` columns even in Phase 0 |
| R7 | XOAUTH2 token refresh race condition in async context | Medium | Medium | Token refresh must be serialized (asyncio Lock); test explicitly |
| R8 | `\Deleted` + `EXPUNGE` interplay mishandled | High | High | Dedicated test matrix for all EXPUNGE scenarios in 1.1 |

---

## Dependency Map

```
OAuth2 setup (0.0)
    │
    └── Raw proxy (0.1) ──── IMAP parser (0.2)
                                     │
                                     └── OAuth2 integration (0.3)
                                                  │
                                                  └── Local state DB (0.4)
                                                               │
                                                               └── Write interception (1.1)
                                                                            │
                                                               ┌────────────┴──────────────┐
                                                               │                           │
                                                        REST API (1.2)         Batching logic (for 2.2)
                                                               │
                                                        Execute + reject (1.3)
                                                               │
                                                    ┌──────────┴──────────┐
                                                    │                     │
                                               React PWA (2.1)    SMTP proxy (Phase 1)
                                                    │
                                             Web Push (2.2)
                                                    │
                                             Docker (3.1)
                                                    │
                                        ┌───────────┴───────────┐
                                        │                       │
                                   Undo (3.2)             Rules (3.3)
```

---

## Open Questions (Carry Forward)

These are unresolved decisions that will affect Phase 0 or 1 implementation. They need answers before the relevant sub-milestone starts.

- [ ] **APPEND staging:** Should AI APPEND to `[Gmail]/Sent Mail` (indicating it sent an email) be staged, or is it treated as a read-only shadow of a send that was already approved? Answer needed before 1.1.
- [ ] **Single vs multi-account:** Phase 0 is one Gmail account. Does Phase 1 need to support Martin switching between accounts, or is one account per running instance acceptable until Phase 3?
- [ ] **Self-hosted vs SaaS:** Affects Phase 3 architecture significantly. SQLite self-hosted path vs Postgres multi-tenant path. Decision should be made before Phase 2 starts.
- [ ] **Android app:** iOS is Phase 1. Android is... when? Needs a decision before Phase 2 is scoped.
