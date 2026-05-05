"""
Local SQLite state DB.

Mirrors mailbox state (folders, messages, flags) from the upstream server.
This is the source of truth for what the AI agent believes the mailbox looks like.

State transitions:
  upstream FETCH response → upsert messages table
  write interception      → optimistic update (proposed state)
  rejection               → revert to snapshot

Sub-milestone: 0.4 (mailbox mirror tables)
Sub-milestone: 1.0 (staged_operations + audit_log tables)
"""
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

_DATA_DIR = os.environ.get("NUVRAIL_DATA_DIR", str(Path.home() / ".nuvrail"))
DB_PATH = Path(_DATA_DIR).expanduser() / "nuvrail.db"

# Mailbox mirror schema — implemented in sub-milestone 0.4 (left intact)
SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    uidvalidity  INTEGER,
    uidnext      INTEGER,
    exists_count INTEGER,
    recent_count INTEGER,
    unseen_count INTEGER,
    last_synced  INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id    INTEGER REFERENCES folders(id),
    uid          INTEGER NOT NULL,
    sequence_num INTEGER,
    message_id   TEXT,
    subject      TEXT,
    sender       TEXT,
    date_sent    INTEGER,
    size         INTEGER,
    flags        TEXT NOT NULL DEFAULT '[]',
    last_updated INTEGER,
    UNIQUE(folder_id, uid)
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auto_approval_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    enabled        INTEGER NOT NULL DEFAULT 1,
    priority       INTEGER NOT NULL DEFAULT 0,
    op_type        TEXT,
    sender_pattern TEXT,
    folder_from    TEXT,
    action         TEXT NOT NULL,
    description    TEXT NOT NULL,
    created_at     INTEGER NOT NULL
);
"""

# Staging + audit schema — implemented in sub-milestone 1.0
_STAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS staged_operations (
    id              TEXT PRIMARY KEY,       -- op_XXXXXX (6 random alphanum chars)
    created_at      INTEGER NOT NULL,       -- unix timestamp
    expires_at      INTEGER NOT NULL,       -- created_at + 48h
    status          TEXT NOT NULL,          -- 'pending'|'approved'|'rejected'|'executed'|'failed'|'expired'
    op_type         TEXT NOT NULL,          -- 'store'|'move'|'copy'|'append'|'create'|'rename'|'smtp_send'
    protocol        TEXT NOT NULL,          -- 'imap'|'smtp'
    imap_command    TEXT,                   -- raw IMAP command (null for smtp ops)
    smtp_envelope   TEXT,                   -- JSON {from, to, subject, body_preview} (null for imap)
    description     TEXT NOT NULL,          -- human-readable
    agent_id        TEXT,                   -- null for Phase 0
    message_ids     TEXT,                   -- JSON array of affected UIDs
    folder_from     TEXT,
    folder_to       TEXT,
    flags_add       TEXT,                   -- JSON array
    flags_remove    TEXT,                   -- JSON array
    snapshot        TEXT,                   -- JSON {uid: {flags, seq_num, folder_id}} pre-op state
    is_urgent       INTEGER NOT NULL DEFAULT 0,  -- 1 for smtp_send and trash ops (always show at top)
    decided_at      INTEGER,
    decided_by      TEXT,
    executed_at     INTEGER,
    undo_expires_at INTEGER,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    INTEGER NOT NULL,
    operation_id TEXT REFERENCES staged_operations(id),
    event        TEXT NOT NULL,             -- 'staged'|'approved'|'rejected'|'executed'|'execution_failed'
    actor        TEXT,                      -- 'ai_agent'|'human'|'system'
    agent_id     TEXT,
    detail       TEXT                       -- JSON
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    hashed_password TEXT NOT NULL,       -- bcrypt hash of human password
    api_token       TEXT UNIQUE,         -- SHA-256 hex digest of bearer token (never plaintext)
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_credentials (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id),
    label              TEXT NOT NULL DEFAULT 'default',
    agent_username     TEXT NOT NULL UNIQUE,  -- e.g. "nuvrail_<hex8>"
    hashed_token       TEXT NOT NULL,          -- bcrypt hash of the agent token
    upstream_host      TEXT NOT NULL,
    upstream_imap_port INTEGER NOT NULL DEFAULT 993,
    upstream_smtp_port INTEGER NOT NULL DEFAULT 587,
    upstream_user      TEXT NOT NULL,
    upstream_password  TEXT,                   -- AES-256-GCM encrypted; NULL for OAuth2 agents
    -- OAuth2 fields (NULL for password-auth agents)
    oauth2_provider              TEXT,         -- 'google' | NULL
    oauth2_refresh_token         TEXT,         -- encrypted at rest (AES-256-GCM)
    oauth2_client_id             TEXT,         -- GCP OAuth2 client ID (not a secret)
    oauth2_client_secret         TEXT,         -- encrypted at rest (AES-256-GCM)
    oauth2_access_token          TEXT,         -- cached access token — encrypted
    oauth2_access_token_expires_at INTEGER,    -- unix timestamp; NULL = not yet cached
    created_at         INTEGER NOT NULL,
    revoked_at         INTEGER                 -- NULL = active
);

CREATE TABLE IF NOT EXISTS pending_reverts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES staged_operations(id),
    folder_id    INTEGER NOT NULL,
    uid          INTEGER NOT NULL,
    true_flags   TEXT NOT NULL,             -- JSON array: actual flags after revert
    created_at   INTEGER NOT NULL,
    delivered_at INTEGER                    -- NULL until proxy injects the unsolicited FETCH
);
"""


async def init_db(path: Path = DB_PATH) -> None:
    """Initialize the database, creating tables if they don't exist."""
    import hashlib  # noqa: PLC0415
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(SCHEMA)
        await db.executescript(_STAGING_SCHEMA)

        # Migration: re-hash any api_token values that are not already
        # SHA-256 hex digests (64 lowercase hex chars).  This handles
        # existing plaintext tokens from before the security hardening.
        async with db.execute(
            "SELECT id, api_token FROM users WHERE api_token IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            token = row["api_token"]
            if len(token) != 64 or not all(c in "0123456789abcdef" for c in token):
                hashed = hashlib.sha256(token.encode()).hexdigest()
                await db.execute(
                    "UPDATE users SET api_token = ? WHERE id = ?",
                    (hashed, row["id"]),
                )

        # Migration: add OAuth2 columns to agent_credentials if not present.
        # SQLite supports ADD COLUMN but not IF NOT EXISTS; we check the
        # schema directly to stay idempotent across repeated startups.
        async with db.execute(
            "PRAGMA table_info(agent_credentials)"
        ) as cur:
            existing_cols = {row["name"] for row in await cur.fetchall()}

        oauth2_columns = [
            ("oauth2_provider",              "TEXT"),
            ("oauth2_refresh_token",         "TEXT"),
            ("oauth2_client_id",             "TEXT"),
            ("oauth2_client_secret",         "TEXT"),
            ("oauth2_access_token",          "TEXT"),
            ("oauth2_access_token_expires_at", "INTEGER"),
        ]
        for col_name, col_type in oauth2_columns:
            if col_name not in existing_cols:
                await db.execute(
                    f"ALTER TABLE agent_credentials ADD COLUMN {col_name} {col_type}"  # noqa: S608
                )

        await db.commit()


@asynccontextmanager
async def get_db(path: Path = DB_PATH) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager yielding an aiosqlite.Connection with row_factory set."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ---------------------------------------------------------------------------
# Folder sync helpers
# ---------------------------------------------------------------------------


async def get_or_create_folder(name: str, db_path: Path = DB_PATH) -> int:
    """Return folder_id, creating a row if it doesn't exist."""
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT id FROM folders WHERE name = ?", (name,)
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            return int(row["id"])
        cur = await db.execute(
            "INSERT INTO folders (name, last_synced) VALUES (?, ?)",
            (name, int(time.time())),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def update_folder_stats(
    name: str,
    *,
    exists_count: "int | None" = None,
    recent_count: "int | None" = None,
    uidvalidity: "int | None" = None,
    uidnext: "int | None" = None,
    unseen_count: "int | None" = None,
    db_path: Path = DB_PATH,
) -> int:
    """Upsert folder stats. Returns folder_id."""
    folder_id = await get_or_create_folder(name, db_path=db_path)

    # Build UPDATE only for non-None fields
    updates: "list[tuple[str, object]]" = [("last_synced", int(time.time()))]
    if exists_count is not None:
        updates.append(("exists_count", exists_count))
    if recent_count is not None:
        updates.append(("recent_count", recent_count))
    if uidvalidity is not None:
        updates.append(("uidvalidity", uidvalidity))
    if uidnext is not None:
        updates.append(("uidnext", uidnext))
    if unseen_count is not None:
        updates.append(("unseen_count", unseen_count))

    set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
    values = [v for _, v in updates]
    values.append(folder_id)

    async with get_db(db_path) as db:
        await db.execute(
            f"UPDATE folders SET {set_clause} WHERE id = ?",  # noqa: S608
            values,
        )
        await db.commit()

    return folder_id


async def upsert_folders_from_list(
    folder_names: "list[str]",
    db_path: Path = DB_PATH,
) -> None:
    """Create folder rows for all names returned by LIST. Idempotent."""
    for name in folder_names:
        await get_or_create_folder(name, db_path=db_path)


# ---------------------------------------------------------------------------
# Message sync helpers
# ---------------------------------------------------------------------------


async def upsert_message(
    folder_id: int,
    uid: int,
    *,
    seq_num: "int | None" = None,
    flags: "list[str] | None" = None,
    subject: "str | None" = None,
    sender: "str | None" = None,
    date_sent: "int | None" = None,
    size: "int | None" = None,
    message_id: "str | None" = None,
    db_path: Path = DB_PATH,
) -> None:
    """Insert or update a message row. Only updates non-None fields."""
    now = int(time.time())
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT id FROM messages WHERE folder_id = ? AND uid = ?",
            (folder_id, uid),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            # INSERT with whatever we have
            await db.execute(
                """
                INSERT INTO messages
                    (folder_id, uid, sequence_num, flags, subject, sender,
                     date_sent, size, message_id, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    folder_id,
                    uid,
                    seq_num,
                    json.dumps(flags if flags is not None else []),
                    subject,
                    sender,
                    date_sent,
                    size,
                    message_id,
                    now,
                ),
            )
        else:
            # UPDATE only non-None fields
            updates: "list[tuple[str, object]]" = [("last_updated", now)]
            if seq_num is not None:
                updates.append(("sequence_num", seq_num))
            if flags is not None:
                updates.append(("flags", json.dumps(flags)))
            if subject is not None:
                updates.append(("subject", subject))
            if sender is not None:
                updates.append(("sender", sender))
            if date_sent is not None:
                updates.append(("date_sent", date_sent))
            if size is not None:
                updates.append(("size", size))
            if message_id is not None:
                updates.append(("message_id", message_id))

            set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
            values: "list[object]" = [v for _, v in updates]
            values.append(folder_id)
            values.append(uid)
            await db.execute(
                f"UPDATE messages SET {set_clause} WHERE folder_id = ? AND uid = ?",  # noqa: S608
                values,
            )

        await db.commit()


async def get_message(
    folder_id: int,
    uid: int,
    db_path: Path = DB_PATH,
) -> "dict | None":
    """Fetch a single message row by UID. Returns dict or None."""
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM messages WHERE folder_id = ? AND uid = ?",
            (folder_id, uid),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


def _expand_uid_set(uid_set_str: str, known_uids: "list[int]") -> "list[int]":
    """Expand an IMAP UID set string into a list of matching UIDs.

    UID set grammar (simplified):
      uid-set = uid-range *("," uid-range)
      uid-range = uid / uid ":" uid
      uid = nz-number / "*"

    "*" means the highest known UID in the folder.
    """
    if not known_uids:
        return []

    max_uid = max(known_uids)
    known_set = set(known_uids)
    result: "set[int]" = set()

    for part in uid_set_str.split(","):
        part = part.strip()
        if ":" in part:
            low_str, high_str = part.split(":", 1)
            low = max_uid if low_str == "*" else int(low_str)
            high = max_uid if high_str == "*" else int(high_str)
            if low > high:
                low, high = high, low
            result.update(u for u in known_set if low <= u <= high)
        elif part == "*":
            result.add(max_uid)
        else:
            uid = int(part)
            if uid in known_set:
                result.add(uid)

    return sorted(result)


async def get_messages_by_uid_set(
    folder_id: int,
    uid_set_str: str,
    db_path: Path = DB_PATH,
) -> "list[dict]":
    """Resolve a UID set string to message rows.

    uid_set_str formats:
      "42"          — single UID
      "1:10"        — range
      "1,3,5"       — comma list
      "1:5,10,20"   — mixed
      "*"           — highest known UID in folder
      "1:*"         — all from 1 to highest known UID

    Pulls all known UIDs for the folder from DB, then filters to the set.
    Returns [] if no messages match.
    """
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT uid FROM messages WHERE folder_id = ? ORDER BY uid",
            (folder_id,),
        ) as cur:
            rows = await cur.fetchall()

    known_uids = [int(r["uid"]) for r in rows]
    target_uids = _expand_uid_set(uid_set_str, known_uids)

    if not target_uids:
        return []

    placeholders = ",".join("?" * len(target_uids))
    async with get_db(db_path) as db:
        async with db.execute(
            f"SELECT * FROM messages WHERE folder_id = ? AND uid IN ({placeholders})"  # noqa: S608
            " ORDER BY uid",
            [folder_id, *target_uids],
        ) as cur:
            rows2 = await cur.fetchall()

    return [dict(r) for r in rows2]


async def get_message_metadata_by_uid_set(
    folder_id: int,
    uid_set_str: str,
    db_path: Path = DB_PATH,
) -> "list[dict]":
    """Return lightweight sender/subject rows for a UID set.

    Used at staging time to build human-readable operation descriptions
    without fetching full message rows. Returns a list of dicts with keys
    ``uid``, ``sender``, ``subject`` for each message found in the state DB.
    Messages not yet FETCH'd through the proxy will simply be absent.
    """
    rows = await get_messages_by_uid_set(folder_id, uid_set_str, db_path=db_path)
    return [
        {"uid": r["uid"], "sender": r.get("sender"), "subject": r.get("subject")}
        for r in rows
    ]


async def get_pending_move_uids_for_folder(
    folder_name: str,
    db_path: Path = DB_PATH,
) -> "set[int]":
    """Return UIDs that are pending MOVE out of the given folder.

    Used by the u2c pump to filter FETCH responses — if a UID is pending
    move from the current folder, the proxy suppresses that FETCH line so
    the agent sees a consistent view without waiting for human approval.
    """
    async with get_db(db_path) as db:
        async with db.execute(
            """SELECT message_ids FROM staged_operations
               WHERE status = 'pending' AND op_type = 'move' AND folder_from = ?""",
            (folder_name,),
        ) as cur:
            rows = await cur.fetchall()

    pending: set[int] = set()
    for row in rows:
        raw = row["message_ids"] if isinstance(row, dict) else row[0]
        if not raw:
            continue
        try:
            ids = json.loads(raw)
        except Exception:
            continue
        for uid_str in ids:
            # message_ids stores raw uid_set strings like "377" or "1:5"
            # For simple single UIDs, parse directly; ranges handled below
            s = str(uid_str).strip()
            if s.isdigit():
                pending.add(int(s))
            elif ":" in s:
                # Expand simple range N:M
                parts = s.split(":")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    lo, hi = int(parts[0]), int(parts[1])
                    pending.update(range(lo, hi + 1))
    return pending


async def remove_messages_from_folder(
    folder_id: int,
    uid_set_str: str,
    db_path: Path = DB_PATH,
) -> None:
    """Delete message rows for the given UID set from the local state mirror.

    Used when staging a MOVE operation — the message is optimistically removed
    from the source folder so the agent sees a consistent view without waiting
    for human approval. Rollback restores the row via restore_from_snapshot.
    """
    rows = await get_messages_by_uid_set(folder_id, uid_set_str, db_path=db_path)
    if not rows:
        return
    uids = [r["uid"] for r in rows]
    placeholders = ",".join("?" * len(uids))
    async with get_db(db_path) as db:
        await db.execute(
            f"DELETE FROM messages WHERE folder_id = ? AND uid IN ({placeholders})",  # noqa: S608
            [folder_id, *uids],
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Snapshot, optimistic update, and revert (milestone 1.2 — rejection revert)
# ---------------------------------------------------------------------------


async def snapshot_messages(
    folder_id: int,
    uid_set_str: str,
    db_path: Path = DB_PATH,
) -> dict:
    """Capture current state of messages matching uid_set_str.

    Returns a dict keyed by str(uid) containing the pre-operation state
    needed to revert the messages table on rejection:
      {"42": {"flags": ["\\\\Seen"], "seq_num": 5, "folder_id": 1}, ...}

    If no messages match (uid not yet in state DB — message hasn't been
    FETCH'd through the proxy yet), the snapshot will be empty for that
    uid. Revert will be a no-op for unknown UIDs.
    """
    rows = await get_messages_by_uid_set(folder_id, uid_set_str, db_path=db_path)
    snapshot: dict = {}
    for row in rows:
        uid_str = str(row["uid"])
        flags_raw = row.get("flags", "[]")
        flags = json.loads(flags_raw) if isinstance(flags_raw, str) else (flags_raw or [])
        snapshot[uid_str] = {
            "flags": flags,
            "seq_num": row.get("sequence_num"),
            "folder_id": folder_id,
            # sender/subject captured so MOVE rollback can fully re-insert the row
            "sender": row.get("sender"),
            "subject": row.get("subject"),
        }
    return snapshot


async def apply_optimistic_flag_update(
    folder_id: int,
    uid_set_str: str,
    flags_add: "list[str]",
    flags_remove: "list[str]",
    db_path: Path = DB_PATH,
) -> None:
    """Apply a proposed flag change to the messages table optimistically.

    Called immediately after staging a STORE operation so the AI sees
    the proposed state without waiting for approval. Does not touch upstream.

    Flag merge logic:
      - flags_add:    add each flag to existing set (dedup)
      - flags_remove: remove each flag from existing set
      Both lists can be non-empty simultaneously (e.g. FLAGS.SILENT with add/remove).

    Messages not yet in the state DB (uid unknown) are silently skipped —
    the snapshot will also be empty for them, so there is nothing to revert.
    """
    rows = await get_messages_by_uid_set(folder_id, uid_set_str, db_path=db_path)
    if not rows:
        return

    now = int(time.time())
    async with get_db(db_path) as db:
        for row in rows:
            uid = row["uid"]
            flags_raw = row.get("flags", "[]")
            current = set(json.loads(flags_raw) if isinstance(flags_raw, str) else (flags_raw or []))
            current.update(flags_add)
            current.difference_update(flags_remove)
            new_flags = json.dumps(sorted(current))
            await db.execute(
                "UPDATE messages SET flags = ?, last_updated = ? WHERE folder_id = ? AND uid = ?",
                (new_flags, now, folder_id, uid),
            )
        await db.commit()


async def restore_from_snapshot(
    operation_id: str,
    db_path: Path = DB_PATH,
) -> "list[dict]":
    """Read snapshot from staged_operations, restore messages table.

    For each UID in the snapshot:
      - Restores the flags column in the messages table to the pre-op value.
      - Returns a list of {operation_id, folder_id, uid, true_flags} dicts
        for insertion into pending_reverts.

    If snapshot is NULL or empty (operation had no snapshot — e.g. CREATE,
    RENAME, or APPEND), returns an empty list (no-op revert).
    """
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT snapshot FROM staged_operations WHERE id = ?",
            (operation_id,),
        ) as cur:
            row = await cur.fetchone()

    if row is None or row["snapshot"] is None:
        return []

    snapshot: dict = json.loads(row["snapshot"])
    if not snapshot:
        return []

    now = int(time.time())
    reverts: list[dict] = []

    async with get_db(db_path) as db:
        for uid_str, state in snapshot.items():
            uid = int(uid_str)
            folder_id: int = state["folder_id"]
            true_flags: list = state.get("flags", [])
            true_flags_json = json.dumps(true_flags)

            # Check if the row still exists (STORE revert) or was deleted (MOVE revert).
            async with db.execute(
                "SELECT id FROM messages WHERE folder_id = ? AND uid = ?",
                (folder_id, uid),
            ) as chk:
                exists = await chk.fetchone()

            if exists:
                # STORE revert: row exists, just restore flags
                await db.execute(
                    "UPDATE messages SET flags = ?, last_updated = ? WHERE folder_id = ? AND uid = ?",
                    (true_flags_json, now, folder_id, uid),
                )
            else:
                # MOVE revert: row was deleted at staging time — re-insert it
                seq_num = state.get("seq_num")
                sender = state.get("sender")
                subject = state.get("subject")
                await db.execute(
                    """INSERT OR IGNORE INTO messages
                       (folder_id, uid, sequence_num, flags, sender, subject, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (folder_id, uid, seq_num, true_flags_json, sender, subject, now),
                )
            reverts.append({
                "operation_id": operation_id,
                "folder_id": folder_id,
                "uid": uid,
                "true_flags": true_flags_json,
            })
        await db.commit()

    return reverts


async def insert_pending_reverts(
    operation_id: str,
    reverts: "list[dict]",
    db_path: Path = DB_PATH,
) -> None:
    """Insert pending_reverts rows (one per affected UID).

    Each row will be picked up by the proxy on the AI's next SELECT/NOOP/FETCH
    and injected as an unsolicited FETCH response so the AI re-syncs to true state.

    reverts: list of {operation_id, folder_id, uid, true_flags} dicts as
    returned by restore_from_snapshot().
    """
    if not reverts:
        return

    now = int(time.time())
    async with get_db(db_path) as db:
        for r in reverts:
            await db.execute(
                """
                INSERT INTO pending_reverts
                    (operation_id, folder_id, uid, true_flags, created_at, delivered_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (r["operation_id"], r["folder_id"], r["uid"], r["true_flags"], now),
            )
        await db.commit()


async def get_pending_reverts(
    folder_id: int,
    db_path: Path = DB_PATH,
) -> "list[dict]":
    """Return undelivered pending_reverts rows for a folder.

    Only returns rows where delivered_at IS NULL.
    Ordered by creation time so the proxy injects them in staging order.
    """
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT id, operation_id, folder_id, uid, true_flags, created_at, delivered_at
            FROM pending_reverts
            WHERE folder_id = ? AND delivered_at IS NULL
            ORDER BY created_at ASC
            """,
            (folder_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def mark_reverts_delivered(
    revert_ids: "list[int]",
    db_path: Path = DB_PATH,
) -> None:
    """Set delivered_at = now for the given pending_reverts ids."""
    if not revert_ids:
        return

    now = int(time.time())
    placeholders = ",".join("?" * len(revert_ids))
    async with get_db(db_path) as db:
        await db.execute(
            f"UPDATE pending_reverts SET delivered_at = ? WHERE id IN ({placeholders})",  # noqa: S608
            [now, *revert_ids],
        )
        await db.commit()
