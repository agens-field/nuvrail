"""
Undo logic for approved+executed operations.

Reverses an executed IMAP operation within the undo window (default: 24h).
Not all op_types are undoable — see UNDOABLE_OP_TYPES and UNDO_STRATEGY below.

Data flow:
  POST /api/v1/operations/{id}/undo
       │
       ├─ load operation row from staged_operations
       │   ├─ status must be 'executed'
       │   ├─ undo_expires_at must be > now
       │   └─ op_type must be in UNDOABLE_OP_TYPES
       │
       ├─ look up agent_credentials for IMAP connection
       │
       ├─ execute the inverse IMAP command (UNDO_STRATEGY map)
       │
       └─ mark operation status → 'reverted', insert audit_log event

Inverse strategies:
  move      → UID MOVE folder_to → folder_from
  trash     → UID MOVE folder_to (Trash) → folder_from
  mark_read → UID STORE -FLAGS (\\Seen)   [was +FLAGS]
  mark_unread→UID STORE +FLAGS (\\Seen)   [was -FLAGS]
  star      → UID STORE -FLAGS (\\Flagged)[was +FLAGS]
  unstar    → UID STORE +FLAGS (\\Flagged)[was -FLAGS]
  archive   → UID MOVE folder_to → INBOX

Operations NOT undoable in Phase 2:
  smtp_send — message already sent (or not yet; reverting is a cancel, not an undo)
  copy      — we don't track which UID was created in folder_to
  append    — body not stored in DB
  create    — folder DELETE is destructive; Phase 3
  rename    — requires re-rename; Phase 3
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import aioimaplib

from gateway.credentials import decrypt_credential
from gateway.state_db import get_db
from gateway.staging import update_operation_status

UNDO_WINDOW_HOURS = int(os.getenv("UNDO_WINDOW_HOURS", "24"))

UNDOABLE_OP_TYPES = frozenset({
    "move",
    "trash",
    "mark_read",
    "mark_unread",
    "star",
    "unstar",
    "archive",
})


class UndoError(Exception):
    """Raised when an undo cannot proceed. Message is safe to surface to the client."""


async def undo_operation(operation_id: str, db_path: Path) -> dict[str, Any]:
    """Reverse an executed operation and mark it reverted.

    Returns a summary dict with op_id, op_type, and what was reversed.
    Raises UndoError with a human-readable message on any validation or
    execution failure.
    """
    now = int(time.time())

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM staged_operations WHERE id = ?", (operation_id,)
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        raise UndoError(f"Operation {operation_id!r} not found.")

    row = dict(row)
    status = row.get("status")
    op_type = row.get("op_type", "")
    undo_expires_at = row.get("undo_expires_at") or 0

    if status != "executed":
        raise UndoError(
            f"Operation {operation_id!r} cannot be undone: status is {status!r}, "
            f"expected 'executed'."
        )
    if op_type not in UNDOABLE_OP_TYPES:
        raise UndoError(
            f"op_type {op_type!r} is not undoable. "
            f"Undoable types: {', '.join(sorted(UNDOABLE_OP_TYPES))}."
        )
    if undo_expires_at and now > undo_expires_at:
        window_hours = UNDO_WINDOW_HOURS
        raise UndoError(
            f"Operation {operation_id!r} undo window has expired "
            f"(window: {window_hours}h, expired at {undo_expires_at})."
        )

    # --- Load credentials ------------------------------------------------
    agent_id = row.get("agent_id")
    async with get_db(db_path) as db:
        if agent_id:
            async with db.execute(
                "SELECT * FROM agent_credentials WHERE id = ?", (agent_id,)
            ) as cur:
                cred_row = await cur.fetchone()
            cred = dict(cred_row) if cred_row else None
        else:
            cred = None

    if cred:
        imap_host = cred["upstream_host"]
        imap_port = int(cred["upstream_imap_port"])
        imap_user = cred["upstream_user"]
        raw_pass = cred.get("upstream_password")
        imap_pass = decrypt_credential(raw_pass) if raw_pass else None
    else:
        imap_host = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
        imap_port = int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993"))
        imap_user = os.environ.get("NUVRAIL_TEST_IMAP_USER", "")
        imap_pass = os.environ.get("NUVRAIL_TEST_IMAP_PASS", "")
        if not imap_host:
            raise UndoError(
                f"Operation {operation_id!r} has no agent_id and no fallback "
                "IMAP env vars are set."
            )

    if not imap_pass:
        raise UndoError(
            "OAuth2 agents are not yet supported for undo — "
            "undo requires password auth to open a fresh IMAP connection."
        )

    # --- Deserialize fields ----------------------------------------------
    folder_from = row.get("folder_from") or "INBOX"
    folder_to = row.get("folder_to") or ""
    raw_ids = row.get("message_ids")
    message_ids: list[str] = json.loads(raw_ids) if isinstance(raw_ids, str) else (raw_ids or [])
    uid_set = ",".join(message_ids) if message_ids else "1"

    raw_flags_add = row.get("flags_add")
    flags_add: list[str] = (
        json.loads(raw_flags_add) if isinstance(raw_flags_add, str) else (raw_flags_add or [])
    )
    raw_flags_remove = row.get("flags_remove")
    flags_remove: list[str] = (
        json.loads(raw_flags_remove) if isinstance(raw_flags_remove, str) else (raw_flags_remove or [])
    )

    # --- Execute inverse -------------------------------------------------
    description = await _execute_undo_imap(
        op_type=op_type,
        uid_set=uid_set,
        folder_from=folder_from,
        folder_to=folder_to,
        flags_add=flags_add,
        flags_remove=flags_remove,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_user=imap_user,
        imap_pass=imap_pass,
        operation_id=operation_id,
    )

    # --- Mark reverted in DB ---------------------------------------------
    await update_operation_status(operation_id, "reverted", db_path=db_path)
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, op_type, detail)
            VALUES (?, ?, 'reverted', 'human', ?, ?, ?)
            """,
            (now, operation_id, str(agent_id) if agent_id else None, op_type,
             json.dumps({"description": description})),
        )
        await db.commit()

    return {
        "operation_id": operation_id,
        "op_type": op_type,
        "reverted": description,
    }


async def _execute_undo_imap(
    *,
    op_type: str,
    uid_set: str,
    folder_from: str,
    folder_to: str,
    flags_add: list[str],
    flags_remove: list[str],
    imap_host: str,
    imap_port: int,
    imap_user: str,
    imap_pass: str,
    operation_id: str,
) -> str:
    """Execute the inverse IMAP command. Returns a human-readable description."""
    client = aioimaplib.IMAP4_SSL(host=imap_host, port=imap_port)
    try:
        await client.wait_hello_from_server()
        status, data = await client.login(imap_user, imap_pass)
        if status != "OK":
            raise UndoError(f"IMAP LOGIN failed while attempting undo: {data}")

        if op_type in ("move", "trash", "archive"):
            # Original: moved messages from folder_from → folder_to
            # Reverse:  move them back folder_to → folder_from
            if not folder_to:
                raise UndoError(f"Cannot undo {op_type!r}: original folder_to not recorded.")
            status, data = await client.select(folder_to)
            if status != "OK":
                raise UndoError(f"IMAP SELECT {folder_to!r} failed during undo: {data}")
            status, data = await client.uid("move", uid_set, folder_from)
            if status != "OK":
                raise UndoError(f"IMAP UID MOVE (undo) failed: {data}")
            return f"Moved UIDs {uid_set} back from {folder_to!r} to {folder_from!r}"

        elif op_type in ("mark_read", "star", "flag"):
            # Original added flags — reverse by removing them
            if not flags_add:
                raise UndoError(f"Cannot undo {op_type!r}: no flags_add recorded.")
            status, data = await client.select(folder_from)
            if status != "OK":
                raise UndoError(f"IMAP SELECT {folder_from!r} failed during undo: {data}")
            flag_str = " ".join(flags_add)
            status, data = await client.uid("store", uid_set, "-FLAGS", f"({flag_str})")
            if status != "OK":
                raise UndoError(f"IMAP UID STORE -FLAGS (undo) failed: {data}")
            return f"Removed flags {flags_add} from UIDs {uid_set} in {folder_from!r}"

        elif op_type in ("mark_unread", "unstar", "unflag"):
            # Original removed flags — reverse by adding them back
            if not flags_remove:
                raise UndoError(f"Cannot undo {op_type!r}: no flags_remove recorded.")
            status, data = await client.select(folder_from)
            if status != "OK":
                raise UndoError(f"IMAP SELECT {folder_from!r} failed during undo: {data}")
            flag_str = " ".join(flags_remove)
            status, data = await client.uid("store", uid_set, "+FLAGS", f"({flag_str})")
            if status != "OK":
                raise UndoError(f"IMAP UID STORE +FLAGS (undo) failed: {data}")
            return f"Restored flags {flags_remove} on UIDs {uid_set} in {folder_from!r}"

        else:
            raise UndoError(f"No undo strategy defined for op_type {op_type!r}.")

    finally:
        try:
            await client.logout()
        except Exception:
            pass
