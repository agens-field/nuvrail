"""
Staging engine — Milestone 1.0.

Takes structured operation parameters and:
  1. Generates a unique operation ID (op_XXXXXX)
  2. Inserts a staged_operations row (status=pending)
  3. Inserts a 'staged' audit_log row
  4. Returns the operation ID

Sub-milestone: 1.0
"""
from __future__ import annotations

import json
import logging
import secrets
import string
import time
from pathlib import Path
from typing import Optional

import asyncio

from gateway.rules import evaluate_rules, get_matching_rule
from gateway.state_db import DB_PATH, get_db
from gateway.state_db import insert_pending_reverts, restore_from_snapshot

OP_ID_PREFIX = "op_"
_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LENGTH = 6


def _generate_op_id() -> str:
    """Generate op_XXXXXX — 6 random alphanumeric chars."""
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))
    return f"{OP_ID_PREFIX}{suffix}"


_URGENT_OP_TYPES = {"smtp_send", "trash"}
_logger = logging.getLogger(__name__)


def _decision_to_status(action: str) -> Optional[str]:
    if action == "approve":
        return "approved"
    if action == "reject":
        return "rejected"
    return None


async def _apply_auto_rule_decision(
    op_id: str,
    action: str,
    rule: dict,
    db_path: Path,
) -> None:
    """Apply a matched auto-rule decision and write audit trail."""
    status = _decision_to_status(action)
    if status is None:
        return

    await update_operation_status(op_id, status, decided_by="auto_rule", db_path=db_path)

    now = int(time.time())
    detail = json.dumps(
        {
            "rule_id": rule.get("id"),
            "rule_description": rule.get("description"),
        }
    )
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, detail)
            VALUES (?, ?, ?, 'auto_rule', NULL, ?)
            """,
            (now, op_id, status, detail),
        )
        await db.commit()

    # Rejected operations must roll back optimistic local state immediately.
    if status == "rejected":
        try:
            reverts = await restore_from_snapshot(op_id, db_path=db_path)
            await insert_pending_reverts(op_id, reverts, db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[auto_rule] Snapshot revert failed for op %s (non-fatal): %s",
                op_id,
                exc,
            )


async def create_operation(
    *,
    op_type: str,
    protocol: str,
    description: str,
    agent_id: Optional[int] = None,
    imap_command: Optional[str] = None,
    smtp_envelope: Optional[dict] = None,
    message_ids: Optional[list] = None,
    folder_from: Optional[str] = None,
    folder_to: Optional[str] = None,
    flags_add: Optional[list] = None,
    flags_remove: Optional[list] = None,
    snapshot: Optional[dict] = None,
    is_urgent: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> str:
    """Insert a staged_operations row + audit_log entry. Returns operation ID.

    snapshot:   pre-op state dict for rejection revert (milestone 1.2).
    is_urgent:  1 = show at top of Pending view with urgent styling.
                Defaults to 1 for smtp_send and trash ops, 0 otherwise.
                Pass explicitly to override.
    """
    op_id = _generate_op_id()
    now = int(time.time())
    expires_at = now + 48 * 3600
    if is_urgent is None:
        is_urgent = 1 if op_type in _URGENT_OP_TYPES else 0

    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO staged_operations (
                id, created_at, expires_at, status, op_type, protocol,
                imap_command, smtp_envelope, description, agent_id,
                message_ids, folder_from, folder_to, flags_add, flags_remove,
                snapshot, is_urgent
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op_id,
                now,
                expires_at,
                op_type,
                protocol,
                imap_command,
                json.dumps(smtp_envelope) if smtp_envelope is not None else None,
                description,
                agent_id,
                json.dumps(message_ids) if message_ids is not None else None,
                folder_from,
                folder_to,
                json.dumps(flags_add) if flags_add is not None else None,
                json.dumps(flags_remove) if flags_remove is not None else None,
                json.dumps(snapshot) if snapshot is not None else None,
                is_urgent,
            ),
        )
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, detail)
            VALUES (?, ?, 'staged', 'ai_agent', NULL, NULL)
            """,
            (now, op_id),
        )
        await db.commit()

    # Evaluate auto-rules before push notifications. Auto-approved/rejected
    # operations should not create pending-review push noise.
    auto_action = await evaluate_rules(
        {
            "id": op_id,
            "op_type": op_type,
            "sender": (smtp_envelope or {}).get("from") if smtp_envelope else None,
            "smtp_envelope": smtp_envelope,
            "folder_from": folder_from,
            "snapshot": snapshot,
        },
        db_path=db_path,
    )
    if auto_action in {"approve", "reject"}:
        matched_rule = await get_matching_rule(
            {
                "id": op_id,
                "op_type": op_type,
                "sender": (smtp_envelope or {}).get("from") if smtp_envelope else None,
                "smtp_envelope": smtp_envelope,
                "folder_from": folder_from,
                "snapshot": snapshot,
            },
            db_path=db_path,
        )
        if matched_rule is not None:
            await _apply_auto_rule_decision(op_id, auto_action, matched_rule, db_path)

    if auto_action is None:
        # Fire-and-forget Web Push notification. Non-fatal — staging always succeeds.
        try:
            from gateway.push import notify_staged as _notify
            asyncio.create_task(
                _notify(op_id, description, is_urgent=bool(is_urgent), db_path=db_path),
                name=f"push-notify-{op_id}",
            )
        except Exception as _exc:
            _logger.debug("push notify skipped: %s", _exc)

    return op_id


async def get_operation(op_id: str, db_path: Path = DB_PATH) -> Optional[dict]:
    """Fetch operation by ID. Returns dict or None."""
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM staged_operations WHERE id = ?", (op_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)


async def list_operations(
    status: Optional[str] = None,
    agent_id: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> list:
    """List operations, optionally filtered by status."""
    async with get_db(db_path) as db:
        if status is not None and agent_id is not None:
            async with db.execute(
                """
                SELECT * FROM staged_operations
                WHERE status = ? AND agent_id = ?
                ORDER BY created_at DESC
                """,
                (status, agent_id),
            ) as cursor:
                rows = await cursor.fetchall()
        elif status is not None:
            async with db.execute(
                "SELECT * FROM staged_operations WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ) as cursor:
                rows = await cursor.fetchall()
        elif agent_id is not None:
            async with db.execute(
                "SELECT * FROM staged_operations WHERE agent_id = ? ORDER BY created_at DESC",
                (agent_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM staged_operations ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def update_operation_status(
    op_id: str,
    status: str,
    decided_by: str = "human",
    error: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> None:
    """Update status + decided_at (and executed_at if status='executed')."""
    now = int(time.time())
    async with get_db(db_path) as db:
        if status == "executed":
            await db.execute(
                """
                UPDATE staged_operations
                SET status = ?, decided_at = ?, decided_by = ?, executed_at = ?, error = ?
                WHERE id = ?
                """,
                (status, now, decided_by, now, error, op_id),
            )
        else:
            await db.execute(
                """
                UPDATE staged_operations
                SET status = ?, decided_at = ?, decided_by = ?, error = ?
                WHERE id = ?
                """,
                (status, now, decided_by, error, op_id),
            )
        await db.commit()
