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

from gateway.audit import insert_audit_event, record_audit_event
# The auto-approval rules engine lives in the nuvrail-enterprise plugin, which
# registers the auto-decision provider via load_plugins(). With no plugin
# installed (open core), run_auto_decision() returns None and every operation
# follows the normal manual-approval path. See docs/REPO_SPLIT.md.
from gateway.extensions import run_auto_decision
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


async def _resolve_user_id_for_agent(
    agent_id: Optional[int], db_path: Path
) -> Optional[int]:
    """Return the user_id that owns ``agent_id``, or None if unresolvable.

    Used to scope auto-approval rule evaluation to the operation's owner.
    """
    if agent_id is None:
        return None
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT user_id FROM agent_credentials WHERE id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
    return row["user_id"] if row is not None else None


# Auto-actions that fully decide an op at staging time (no human review left).
# Used by create_operation to suppress the pending-review push notification.
_TERMINAL_AUTO_ACTIONS = {"approve", "reject"}


async def _set_schedule_and_urgency(
    op_id: str,
    db_path: Path,
    scheduled_execute_at: Optional[int] = None,
    is_urgent: Optional[int] = None,
) -> None:
    """Set scheduled_execute_at and/or is_urgent on a still-pending op.

    Only the provided fields are written, so a 'hold' can bump urgency without
    touching scheduling and a cool-down can schedule without forcing urgency.
    """
    sets: list[str] = []
    values: list[object] = []
    if scheduled_execute_at is not None:
        sets.append("scheduled_execute_at = ?")
        values.append(scheduled_execute_at)
    if is_urgent is not None:
        sets.append("is_urgent = ?")
        values.append(1 if is_urgent else 0)
    if not sets:
        return
    values.append(op_id)
    async with get_db(db_path) as db:
        await db.execute(
            f"UPDATE staged_operations SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            values,
        )
        await db.commit()


async def _apply_auto_rule_decision(
    op_id: str,
    decision: dict,
    db_path: Path,
    agent_id: Optional[int] = None,
    op_type: Optional[str] = None,
) -> None:
    """Apply a matched auto-rule decision.

    Always records the decision in the audit log (actor 'auto_rule', carrying
    the rule id — this is what the rule `hits` count is derived from). The
    ``decision`` dict carries ``action`` plus optional ``delay_seconds`` (for
    'approve_after') and ``is_urgent`` (an urgency override). The matching logic
    that produced it lives in the enterprise plugin; core only applies it.

    - reject: mark the operation 'rejected' and roll back optimistic local state.
    - approve: execute upstream via the shared executor — the SAME code path a
      human approval uses — so the op actually happens. The executor sets the
      final status ('executed'/'failed') and writes its own audit row.
    - approve_after: leave the op 'pending' but stamp ``scheduled_execute_at``;
      the scheduled-execution loop (gateway.scheduler) executes it when the
      cool-down elapses, unless a human approves ('send now') or rejects
      ('cancel') first. Gives a catch-it-before-it-sends window — the only
      safety net for outbound mail, which cannot be undone.
    - hold: leave the op 'pending' for manual review, optionally bumping urgency.
    """
    action = decision.get("action")
    rule = decision.get("rule") or {}
    delay_seconds = decision.get("delay_seconds")
    is_urgent = decision.get("is_urgent")
    now = int(time.time())
    detail_obj: dict = {
        "rule_id": rule.get("id"),
        "rule_description": rule.get("description"),
    }

    if action == "reject":
        await update_operation_status(op_id, "rejected", decided_by="auto_rule", db_path=db_path)
        await record_audit_event(
            db_path, timestamp=now, event="rejected", actor="auto_rule",
            operation_id=op_id, agent_id=agent_id, op_type=op_type,
            detail=json.dumps(detail_obj),
        )
        # Roll back optimistic local state immediately.
        try:
            reverts = await restore_from_snapshot(op_id, db_path=db_path)
            await insert_pending_reverts(op_id, reverts, db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[auto_rule] Snapshot revert failed for op %s (non-fatal): %s",
                op_id,
                exc,
            )
        return

    if action == "approve_after":
        # Defer execution. The op stays pending (cancelable) with a deadline.
        delay = int(delay_seconds) if delay_seconds else 0
        scheduled_at = now + max(delay, 0)
        detail_obj["delay_seconds"] = delay
        detail_obj["scheduled_execute_at"] = scheduled_at
        await _set_schedule_and_urgency(
            op_id, db_path, scheduled_execute_at=scheduled_at, is_urgent=is_urgent
        )
        await record_audit_event(
            db_path, timestamp=now, event="scheduled", actor="auto_rule",
            operation_id=op_id, agent_id=agent_id, op_type=op_type,
            detail=json.dumps(detail_obj),
        )
        return

    if action == "hold":
        # Keep for manual review; the only effect is optional urgency + audit
        # attribution so the user can see which rule flagged it.
        await _set_schedule_and_urgency(op_id, db_path, is_urgent=is_urgent)
        await record_audit_event(
            db_path, timestamp=now, event="held", actor="auto_rule",
            operation_id=op_id, agent_id=agent_id, op_type=op_type,
            detail=json.dumps(detail_obj),
        )
        return

    if action != "approve":
        return

    # action == "approve"
    # Record the auto-rule decision first (audit + rule-hit attribution), then
    # execute upstream. We do NOT set an intermediate 'approved' status — the
    # executor transitions the op straight to 'executed' or 'failed'.
    await record_audit_event(
        db_path, timestamp=now, event="approved", actor="auto_rule",
        operation_id=op_id, agent_id=agent_id, op_type=op_type,
        detail=json.dumps(detail_obj),
    )

    row = await get_operation(op_id, db_path=db_path)
    if row is None:
        return
    # Imported lazily to avoid a circular import (gateway.execution imports
    # gateway.staging for get_operation/update_operation_status).
    from gateway.execution import ExecutionError, execute_operation  # noqa: PLC0415
    try:
        await execute_operation(op_id, row, db_path, actor="auto_rule")
    except ExecutionError as exc:
        # Relay/replay failures leave the op marked 'failed' (the executor did
        # that) with an execution_failed audit row. Pre-flight failures (e.g.
        # missing credentials) leave it 'pending' for manual review. Either way
        # staging itself still succeeds.
        _logger.warning(
            "[auto_rule] Upstream execution failed for op %s (rule %s): %s",
            op_id, rule.get("id"), exc,
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
    batch_id: Optional[str] = None,
    append_message: Optional[str] = None,
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
                snapshot, append_message, is_urgent, batch_id
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                append_message,
                is_urgent,
                batch_id,
            ),
        )
        await insert_audit_event(
            db, timestamp=now, event='staged', actor='ai_agent',
            operation_id=op_id, agent_id=agent_id, op_type=op_type,
        )
        await db.commit()

    # Resolve the operation's owning user from its agent so auto-rules are
    # evaluated against that user's rules only — never another tenant's.
    # Operations with no resolvable owner get no auto-rule evaluation.
    rule_user_id = await _resolve_user_id_for_agent(agent_id, db_path)

    # Evaluate auto-rules before push notifications. Auto-approved/rejected
    # operations should not create pending-review push noise.
    #
    # This dict is the sole input an auto-decision provider sees, so it must
    # carry every field a rule might match on. ``smtp_envelope`` already holds
    # ``to``/``subject`` (extracted provider-side); the remaining fields below
    # let rules match a move's destination, the flags being changed, how many
    # messages an op touches, and which agent staged it.
    rule_op = {
        "id": op_id,
        "op_type": op_type,
        "sender": (smtp_envelope or {}).get("from") if smtp_envelope else None,
        "smtp_envelope": smtp_envelope,
        "folder_from": folder_from,
        "folder_to": folder_to,
        "flags_add": flags_add,
        "flags_remove": flags_remove,
        "message_ids": message_ids,
        "agent_id": agent_id,
        "snapshot": snapshot,
    }
    decision = await run_auto_decision(rule_op, db_path=db_path, user_id=rule_user_id)
    auto_action = decision["action"] if decision else None
    if decision is not None:
        await _apply_auto_rule_decision(
            op_id, decision, db_path,
            agent_id=agent_id, op_type=op_type,
        )

    # Notify unless the op was terminally decided. 'approve' executed it and
    # 'reject' reverted it — neither needs a review notification. Everything
    # else (no match, a cool-down 'approve_after', or a 'hold') leaves the op
    # actionable by the human, who must be told it is waiting.
    if auto_action not in _TERMINAL_AUTO_ACTIONS:
        # Fire-and-forget Web Push notification. Non-fatal — staging always succeeds.
        try:
            from gateway.push import notify_staged as _notify
            asyncio.create_task(
                _notify(
                    op_id, description, is_urgent=bool(is_urgent),
                    db_path=db_path, user_id=rule_user_id,
                ),
                name=f"push-notify-{op_id}",
            )
        except Exception as _exc:
            _logger.debug("push notify skipped: %s", _exc)

    return op_id


async def get_operation(
    op_id: str,
    db_path: Path = DB_PATH,
    user_id: Optional[int] = None,
) -> Optional[dict]:
    """Fetch operation by ID. Returns dict or None.

    When ``user_id`` is provided, the operation is only returned if it belongs
    to that user — i.e. its ``agent_id`` maps to an ``agent_credentials`` row
    owned by ``user_id``. Operations with a NULL ``agent_id`` (legacy/test rows
    with no owner) are never returned in user-scoped mode. API callers MUST
    pass ``user_id`` to enforce tenant isolation; internal callers (expiry,
    undo execution) may omit it.
    """
    async with get_db(db_path) as db:
        if user_id is not None:
            query = (
                "SELECT so.* FROM staged_operations so "
                "JOIN agent_credentials ac ON so.agent_id = ac.id "
                "WHERE so.id = ? AND ac.user_id = ?"
            )
            params: tuple = (op_id, user_id)
        else:
            query = "SELECT * FROM staged_operations WHERE id = ?"
            params = (op_id,)
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)


async def list_operations(
    status: Optional[str] = None,
    agent_id: Optional[int] = None,
    db_path: Path = DB_PATH,
    user_id: Optional[int] = None,
) -> list:
    """List operations, optionally filtered by status and/or agent_id.

    When ``user_id`` is provided, results are scoped to operations whose
    ``agent_id`` maps to an ``agent_credentials`` row owned by ``user_id``.
    Operations with a NULL ``agent_id`` are excluded in user-scoped mode.
    API callers MUST pass ``user_id`` to enforce tenant isolation.
    """
    conditions: list[str] = []
    params: list[object] = []

    if user_id is not None:
        join = "JOIN agent_credentials ac ON so.agent_id = ac.id"
        conditions.append("ac.user_id = ?")
        params.append(user_id)
    else:
        join = ""

    if status is not None:
        conditions.append("so.status = ?")
        params.append(status)
    if agent_id is not None:
        conditions.append("so.agent_id = ?")
        params.append(agent_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = (
        f"SELECT so.* FROM staged_operations so {join} {where} "  # noqa: S608 — fragments are literal; values parameterized
        "ORDER BY so.created_at DESC"
    )

    async with get_db(db_path) as db:
        async with db.execute(query, params) as cursor:
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
