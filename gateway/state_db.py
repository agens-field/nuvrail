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
import os
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
"""


async def init_db(path: Path = DB_PATH) -> None:
    """Initialize the database, creating tables if they don't exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        await db.executescript(_STAGING_SCHEMA)
        await db.commit()


@asynccontextmanager
async def get_db(path: Path = DB_PATH) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager yielding an aiosqlite.Connection with row_factory set."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        yield db
