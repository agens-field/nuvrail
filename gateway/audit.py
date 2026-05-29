"""
Cryptographically chained audit log insertion (issue: hash chaining).

Every audit_log row carries two new columns:
  prev_hash   — entry_hash of the immediately preceding row
  entry_hash  — SHA-256(prev_hash‖id‖timestamp‖operation_id‖event‖actor‖
                         agent_id‖op_type‖detail‖user_id)

Fields are serialised as UTF-8 strings joined by ASCII unit separator \\x1f.
None values → empty string. Integer values → decimal string.

The genesis row (first ever inserted) uses prev_hash = "0" * 64.

An asyncio.Lock serialises all insertions so two concurrent coroutines
can never read the same prev_hash.

Chain integrity:
  ┌──────────┐    prev_hash        ┌──────────┐    prev_hash        ┌──────────┐
  │  row 1   │ ─────────────────▶  │  row 2   │ ─────────────────▶  │  row 3   │
  │ prev="0…"│                     │ prev=H1  │                     │ prev=H2  │
  │ hash=H1  │                     │ hash=H2  │                     │ hash=H3  │
  └──────────┘                     └──────────┘                     └──────────┘
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Optional

import aiosqlite

# Sentinel prev_hash for the very first row in the chain.
GENESIS_HASH: str = "0" * 64

# Module-level singleton — serialises all audit insertions across callers.
_AUDIT_LOCK: asyncio.Lock = asyncio.Lock()

# ASCII unit separator used to join fields for hashing.
_SEP = "\x1f"


def _compute_entry_hash(
    prev_hash: str,
    row_id: int,
    timestamp: int,
    operation_id: Optional[str],
    event: str,
    actor: Optional[str],
    agent_id: Optional[str],
    op_type: Optional[str],
    detail: Optional[str],
    user_id: Optional[int],
) -> str:
    """Return SHA-256 hex digest of the canonicalised row fields.

    Field order (joined by \\x1f, None → ""):
      prev_hash, id, timestamp, operation_id, event, actor,
      agent_id, op_type, detail, user_id
    """
    def _s(v: object) -> str:
        if v is None:
            return ""
        return str(v)

    payload = _SEP.join([
        _s(prev_hash),
        _s(row_id),
        _s(timestamp),
        _s(operation_id),
        _s(event),
        _s(actor),
        _s(agent_id),
        _s(op_type),
        _s(detail),
        _s(user_id),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def insert_audit_event(
    db: aiosqlite.Connection,
    *,
    timestamp: int,
    event: str,
    actor: Optional[str] = None,
    operation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    op_type: Optional[str] = None,
    detail: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    """Insert a single audit_log row with cryptographic hash chaining.

    Acquires _AUDIT_LOCK to ensure sequential prev_hash reads.  Does NOT
    call db.commit() — the caller commits as part of its own transaction.

    Args:
        db:           An open aiosqlite.Connection (row_factory=aiosqlite.Row).
        timestamp:    Unix timestamp for the event.
        event:        Event type string (e.g. 'staged', 'approved').
        actor:        Who triggered the event ('ai_agent'|'human'|'system'|'auto_rule').
        operation_id: FK to staged_operations.id (or None).
        agent_id:     Agent identifier string (or None).
        op_type:      Operation type string (or None).
        detail:       JSON string with extra context (or None).
        user_id:      FK to users.id (or None).
    """
    async with _AUDIT_LOCK:
        # Read the entry_hash of the most recently inserted hashed row.
        async with db.execute(
            """
            SELECT entry_hash FROM audit_log
            WHERE entry_hash IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ) as cur:
            last_row = await cur.fetchone()

        prev_hash: str = last_row["entry_hash"] if last_row else GENESIS_HASH

        # Insert the row first to obtain its auto-assigned id.
        cursor = await db.execute(
            """
            INSERT INTO audit_log
                (timestamp, operation_id, event, actor, agent_id, op_type, detail,
                 user_id, prev_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (timestamp, operation_id, event, actor, agent_id, op_type, detail,
             user_id, prev_hash),
        )
        row_id: int = cursor.lastrowid  # type: ignore[assignment]

        # Now compute the entry_hash with the real id baked in.
        entry_hash = _compute_entry_hash(
            prev_hash=prev_hash,
            row_id=row_id,
            timestamp=timestamp,
            operation_id=operation_id,
            event=event,
            actor=actor,
            agent_id=agent_id,
            op_type=op_type,
            detail=detail,
            user_id=user_id,
        )

        await db.execute(
            "UPDATE audit_log SET entry_hash = ? WHERE id = ?",
            (entry_hash, row_id),
        )
        # Caller commits.


async def record_audit_event(
    db_path: Path,
    *,
    timestamp: int,
    event: str,
    actor: Optional[str] = None,
    operation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    op_type: Optional[str] = None,
    detail: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    """Open a connection, append one audit_log row, and commit.

    Convenience wrapper around insert_audit_event for the common case where the
    audit write stands alone (the only write in its transaction). When the
    audit must commit atomically with other writes — e.g. inserting the staged
    operation, or marking an account deleted — use insert_audit_event(db, …)
    inside that existing transaction instead.
    """
    from gateway.state_db import get_db  # noqa: PLC0415 — avoid import cycle

    async with get_db(db_path) as db:
        await insert_audit_event(
            db,
            timestamp=timestamp,
            event=event,
            actor=actor,
            operation_id=operation_id,
            agent_id=agent_id,
            op_type=op_type,
            detail=detail,
            user_id=user_id,
        )
        await db.commit()


async def verify_audit_chain(db_path: Path) -> tuple[bool, list[str]]:
    """Walk all hashed audit_log rows and verify the chain integrity.

    Skips rows where entry_hash IS NULL (pre-migration rows inserted before
    hash chaining was introduced).  Starts the chain from the first non-NULL
    row (its prev_hash is taken as-is — we accept whatever prev_hash was
    recorded; we only verify that the entry_hash is correct given that
    prev_hash and the row's own fields).

    Returns:
        (True, [])               — chain is intact
        (False, [error, ...])    — one or more rows are tampered or broken
    """
    errors: list[str] = []

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, timestamp, operation_id, event, actor, agent_id,
                   op_type, detail, user_id, prev_hash, entry_hash
            FROM audit_log
            WHERE entry_hash IS NOT NULL
            ORDER BY id ASC
            """
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return True, []

    expected_prev = rows[0]["prev_hash"]

    for row in rows:
        rid = row["id"]

        # Verify that prev_hash matches what we expect in the chain.
        if row["prev_hash"] != expected_prev:
            errors.append(
                f"row {rid}: prev_hash mismatch "
                f"(expected {expected_prev!r}, got {row['prev_hash']!r})"
            )
            # Continue checking the remaining rows using the stored prev_hash
            # so we report all broken links, not just the first.
            expected_prev = row["entry_hash"]
            continue

        # Recompute entry_hash and compare.
        computed = _compute_entry_hash(
            prev_hash=row["prev_hash"],
            row_id=rid,
            timestamp=row["timestamp"],
            operation_id=row["operation_id"],
            event=row["event"],
            actor=row["actor"],
            agent_id=row["agent_id"],
            op_type=row["op_type"],
            detail=row["detail"],
            user_id=row["user_id"],
        )
        if computed != row["entry_hash"]:
            errors.append(
                f"row {rid}: entry_hash mismatch "
                f"(stored {row['entry_hash']!r}, computed {computed!r})"
            )

        expected_prev = row["entry_hash"]

    return (len(errors) == 0), errors
