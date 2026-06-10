"""
Undo of approved+executed operations — the inverse of gateway.execution.

Reverses an executed IMAP operation within the undo window (default 24h).
Lives next to the forward executor (gateway.execution) and shares its
credential-resolution and JSON-field helpers, so the forward and inverse
mailbox-mutating commands sit side by side in one layer.

Data flow:
  POST /api/v1/operations/{id}/undo
       │
       ├─ load operation row from staged_operations
       │   ├─ status must be 'executed'
       │   ├─ undo_expires_at must be > now
       │   └─ op_type must be in UNDOABLE_OP_TYPES
       │
       ├─ resolve_imap_credentials() (shared with the forward executor)
       │
       ├─ execute the inverse IMAP command
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

from gateway.audit import record_audit_event
from gateway.execution import resolve_imap_credentials
from gateway.staging import update_operation_status
from gateway.state_db import decode_json_list, get_db

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
        raise UndoError(
            f"Operation {operation_id!r} undo window has expired "
            f"(window: {UNDO_WINDOW_HOURS}h, expired at {undo_expires_at})."
        )

    # --- Resolve credentials (shared with the forward executor) ----------
    try:
        creds = await resolve_imap_credentials(row, db_path)
    except RuntimeError as exc:
        raise UndoError(
            f"Operation {operation_id!r} has no agent_id and no fallback "
            "IMAP env vars are set."
        ) from exc

    if not creds.password:
        # OAuth2 agents (no stored password) and env fallback without a
        # password cannot open the fresh password-auth connection undo needs.
        raise UndoError(
            "OAuth2 agents are not yet supported for undo — "
            "undo requires password auth to open a fresh IMAP connection."
        )

    # --- Deserialize fields ----------------------------------------------
    folder_from = row.get("folder_from") or "INBOX"
    folder_to = row.get("folder_to") or ""
    message_ids = decode_json_list(row.get("message_ids"))
    uid_set = ",".join(message_ids) if message_ids else "1"
    flags_add = decode_json_list(row.get("flags_add"))
    flags_remove = decode_json_list(row.get("flags_remove"))

    # --- Execute inverse -------------------------------------------------
    description = await _execute_undo_imap(
        op_type=op_type,
        uid_set=uid_set,
        folder_from=folder_from,
        folder_to=folder_to,
        flags_add=flags_add,
        flags_remove=flags_remove,
        imap_host=creds.host,
        imap_port=creds.port,
        imap_user=creds.user,
        imap_pass=creds.password,
        operation_id=operation_id,
    )

    # --- Mark reverted in DB ---------------------------------------------
    await update_operation_status(operation_id, "reverted", db_path=db_path)
    agent_id = row.get("agent_id")
    await record_audit_event(
        db_path, timestamp=now, event='reverted', actor='human',
        operation_id=operation_id,
        agent_id=str(agent_id) if agent_id else None,
        op_type=op_type,
        intent_label=row.get('intent_label'),
        detail=json.dumps({'description': description}),
    )

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
