"""
Local SQLite state DB.

Mirrors mailbox state (folders, messages, flags) from the upstream server.
This is the source of truth for what the AI agent believes the mailbox looks like.

State transitions:
  upstream FETCH response → upsert messages table
  write interception      → optimistic update (proposed state)
  rejection               → revert to snapshot

Sub-milestone: 0.4
"""
from pathlib import Path

DB_PATH = Path.home() / ".nuvrail" / "state.db"

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

CREATE TABLE IF NOT EXISTS staged_operations (
    id           TEXT PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    status       TEXT NOT NULL,
    op_type      TEXT NOT NULL,
    imap_command TEXT NOT NULL,
    description  TEXT NOT NULL,
    message_ids  TEXT,
    folder_from  TEXT,
    folder_to    TEXT,
    flags_add    TEXT,
    flags_remove TEXT,
    snapshot     TEXT,
    batch_id     TEXT,
    decided_at   INTEGER,
    decided_by   TEXT,
    executed_at  INTEGER,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    INTEGER NOT NULL,
    operation_id TEXT REFERENCES staged_operations(id),
    event        TEXT NOT NULL,
    actor        TEXT,
    detail       TEXT
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


async def init_db(path: Path = DB_PATH) -> None:
    """Initialize the database, creating tables if they don't exist."""
    # TODO: implement in sub-milestone 0.4
    raise NotImplementedError
