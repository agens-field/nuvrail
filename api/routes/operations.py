"""
/api/v1/operations endpoints — Milestone 1.0.

GET    /operations              — list (optional ?status= filter)
GET    /operations/{op_id}      — single operation detail
POST   /operations/batch/approve — approve multiple operations
POST   /operations/batch/reject  — reject multiple operations
POST   /operations/{op_id}/approve  — approve + execute against upstream
POST   /operations/{op_id}/reject   — reject

Approve logic:
  - SMTP ops: relay via aiosmtplib to upstream SMTP server (STARTTLS)
  - IMAP ops: open a fresh aioimaplib connection to upstream, replay the
    stored raw IMAP command, close connection. Supports: STORE, UID STORE,
    MOVE, UID MOVE, COPY, UID COPY, CREATE, RENAME. APPEND is skipped
    (message body not stored in staging DB — deferred to later milestone).

Execution flow for IMAP:

  approve request
      │
      ▼
  open fresh IMAP4_SSL → blizzard.mxrouting.net:993
      │  LOGIN (plain credentials from env)
      │
      ▼
  SELECT folder_from  (for STORE/MOVE/COPY — folder context required)
      │
      ▼
  replay command (uid store / uid move / uid copy / create / rename)
      │
      ▼
  LOGOUT → close connection
      │
      ▼
  update staged_operations status → 'executed'
  insert audit_log event='executed'

Route ordering note: batch routes (/operations/batch/approve and
/operations/batch/reject) are registered BEFORE the parameterised route
/operations/{op_id}/approve so FastAPI does not treat 'batch' as an op_id.

Sub-milestone: 1.0 (IMAP execution added)
"""
from __future__ import annotations

import json
import logging
import os
import time
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import aioimaplib
import aiosmtplib
from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.undo import UndoError, undo_operation
from api.models import (
    ApproveResponse,
    BatchApproveRequest,
    BatchApproveResponse,
    BatchApproveResult,
    BatchRejectRequest,
    BatchRejectResponse,
    BatchRejectResult,
    MessagePreview,
    OperationListResponse,
    OperationResponse,
    RejectResponse,
    UndoResponse,
)
from gateway.staging import get_operation, list_operations, update_operation_status
from gateway.state_db import DB_PATH, get_db, insert_pending_reverts, restore_from_snapshot

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# DB path dependency — overridable in tests via app.dependency_overrides
# ---------------------------------------------------------------------------


def get_db_path() -> Path:
    """Return the DB path. Override in tests via dependency injection."""
    return DB_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_agent_credential(agent_id: Optional[int], db_path: Path) -> Optional[dict]:
    """Look up agent_credentials row by id. Returns None if not found."""
    if agent_id is None:
        return None
    from gateway.state_db import get_db as _get_db  # noqa: PLC0415
    async with _get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM agent_credentials WHERE id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _execute_imap_upstream(row: dict, db_path: Path) -> None:
    """
    Replay a staged IMAP operation against the upstream server.

    Opens a fresh IMAP4_SSL connection using the agent's registered upstream
    credentials (looked up from agent_credentials by agent_id on the operation).
    Falls back to NUVRAIL_TEST_IMAP_* env vars if agent_id is not set
    (for backwards compatibility with older staged operations).

    Supported op_types: store, move, copy, create, rename, trash, mark_read,
    flag, unflag, mark_unread (all map to UID STORE or UID MOVE/COPY/etc).
    APPEND is skipped — the message body is not stored in the staging DB.

    Raises RuntimeError on any upstream error so the caller can set
    operation status → 'failed'.
    """
    from gateway.credentials import decrypt_credential  # noqa: PLC0415

    op_type = row.get("op_type", "")

    # APPEND: body not stored — skip upstream execution entirely (no credentials needed)
    if op_type == "append":
        logger.info(
            "[imap_execute] Skipping APPEND op %s — body not stored in staging DB", row["id"]
        )
        return

    agent_id = row.get("agent_id")
    cred = await _get_agent_credential(agent_id, db_path)
    if cred:
        imap_host = cred["upstream_host"]
        imap_port = int(cred["upstream_imap_port"])
        imap_user = cred["upstream_user"]
        raw_pass = cred.get("upstream_password")
        imap_pass = decrypt_credential(raw_pass) if raw_pass else None
    else:
        # Fallback for ops staged before agent_id was tracked
        imap_host = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
        imap_port = int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993"))
        imap_user = os.environ.get("NUVRAIL_TEST_IMAP_USER", "")
        imap_pass = os.environ.get("NUVRAIL_TEST_IMAP_PASS", "")
        if not imap_host:
            raise RuntimeError(
                f"Operation {row['id']} has no agent_id and no fallback env vars set"
            )

    imap_command = row.get("imap_command") or ""
    folder_from = row.get("folder_from") or "INBOX"

    # Deserialize JSON fields
    raw_ids = row.get("message_ids")
    message_ids: list[str] = json.loads(raw_ids) if isinstance(raw_ids, str) else (raw_ids or [])
    uid_set = ",".join(message_ids) if message_ids else "1"

    raw_flags_add = row.get("flags_add")
    flags_add: list[str] = (
        json.loads(raw_flags_add) if isinstance(raw_flags_add, str) else (raw_flags_add or [])
    )
    raw_flags_remove = row.get("flags_remove")
    flags_remove: list[str] = (
        json.loads(raw_flags_remove)
        if isinstance(raw_flags_remove, str)
        else (raw_flags_remove or [])
    )
    folder_to = row.get("folder_to") or ""

    client = aioimaplib.IMAP4_SSL(host=imap_host, port=imap_port)
    try:
        await client.wait_hello_from_server()
        status, data = await client.login(imap_user, imap_pass)
        if status != "OK":
            raise RuntimeError(f"IMAP LOGIN failed: {data}")

        # Most write ops require a folder context (SELECT folder_from first)
        if op_type in ("store", "trash", "mark_read", "flag", "unflag", "mark_unread"):
            status, data = await client.select(folder_from)
            if status != "OK":
                raise RuntimeError(f"IMAP SELECT {folder_from!r} failed: {data}")

            # Build the flag change string
            if flags_add:
                flag_str = " ".join(flags_add)
                status, data = await client.uid("store", uid_set, "+FLAGS", f"({flag_str})")
            elif flags_remove:
                flag_str = " ".join(flags_remove)
                status, data = await client.uid("store", uid_set, "-FLAGS", f"({flag_str})")
            else:
                raise RuntimeError(f"STORE op {row['id']} has no flags_add or flags_remove")
            if status != "OK":
                raise RuntimeError(f"IMAP UID STORE failed: {data}")

        elif op_type == "move":
            if not folder_to:
                raise RuntimeError(f"MOVE op {row['id']} missing folder_to")
            status, data = await client.select(folder_from)
            if status != "OK":
                raise RuntimeError(f"IMAP SELECT {folder_from!r} failed: {data}")
            status, data = await client.uid("move", uid_set, folder_to)
            if status != "OK":
                raise RuntimeError(f"IMAP UID MOVE failed: {data}")

        elif op_type == "copy":
            if not folder_to:
                raise RuntimeError(f"COPY op {row['id']} missing folder_to")
            status, data = await client.select(folder_from)
            if status != "OK":
                raise RuntimeError(f"IMAP SELECT {folder_from!r} failed: {data}")
            status, data = await client.uid("copy", uid_set, folder_to)
            if status != "OK":
                raise RuntimeError(f"IMAP UID COPY failed: {data}")

        elif op_type == "create":
            # folder_to holds the new folder name for CREATE
            folder_name = folder_to or imap_command.split()[-1] if imap_command else ""
            if not folder_name:
                raise RuntimeError(f"CREATE op {row['id']} missing folder name")
            status, data = await client.create(folder_name)
            if status != "OK":
                raise RuntimeError(f"IMAP CREATE failed: {data}")

        elif op_type == "rename":
            # folder_from = old name, folder_to = new name
            if not folder_to:
                raise RuntimeError(f"RENAME op {row['id']} missing folder_to")
            status, data = await client.rename(folder_from, folder_to)
            if status != "OK":
                raise RuntimeError(f"IMAP RENAME failed: {data}")

        else:
            # Unknown/unsupported op type — log and treat as no-op
            logger.warning(
                "[imap_execute] Unrecognised op_type %r for op %s — skipping upstream exec",
                op_type,
                row["id"],
            )

        logger.info("[imap_execute] Op %s (%s) executed successfully", row["id"], op_type)

    finally:
        try:
            await client.logout()
        except Exception:
            pass


def _previews_from_snapshot(row: dict, max_messages: int = 3) -> list[MessagePreview]:
    """Extract MessagePreview objects from the operation's snapshot field.

    Fallback used when the messages table has no rows (e.g. after MOVE staging
    deletes them, or when folder_from is not set on older operations).
    Returns [] if snapshot is absent or has no sender/subject data.
    """
    snap_raw = row.get("snapshot")
    if not snap_raw:
        return []
    try:
        snap = json.loads(snap_raw) if isinstance(snap_raw, str) else snap_raw
        previews: list[MessagePreview] = []
        for uid_str, state in (snap or {}).items():
            if len(previews) >= max_messages:
                break
            sender = state.get("sender")
            subject = state.get("subject")
            if sender or subject:  # only include if we have something to show
                previews.append(MessagePreview(
                    uid=int(uid_str),
                    sender=sender,
                    subject=subject,
                    date_sent=None,
                ))
        return previews
    except Exception:  # noqa: BLE001
        return []


async def _fetch_message_previews(
    row: dict, db_path: Path, max_messages: int = 3
) -> list[MessagePreview]:
    """Look up sender/subject for IMAP operation's affected messages.

    Primary path: joins folder_from -> folders table -> messages table.
    Fallback: reads sender/subject from the operation's snapshot field
    (used when rows were deleted during MOVE staging, or folder_from is null).
    SMTP ops return an empty list.
    """
    from gateway.state_db import get_db as _get_db  # noqa: PLC0415

    if row.get("protocol") != "imap":
        return []
    folder_name = row.get("folder_from")
    if not folder_name:
        # No folder context — go straight to snapshot fallback
        return _previews_from_snapshot(row, max_messages)
    raw_ids = row.get("message_ids")
    if not raw_ids:
        return []
    try:
        uid_list = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
    except Exception:
        return []
    if not uid_list:
        return []
    # message_ids is stored as ["<uid_set_str>"] — e.g. ["42"] or ["1:5"]
    uid_set_str = uid_list[0] if isinstance(uid_list[0], str) else str(uid_list[0])

    try:
        async with _get_db(db_path) as db:
            # Resolve folder name to folder_id
            async with db.execute(
                "SELECT id FROM folders WHERE name = ?", (folder_name,)
            ) as cur:
                folder_row = await cur.fetchone()
            if folder_row is None:
                return []
            folder_id = folder_row[0]

            # Expand the UID set and fetch sender/subject for up to max_messages
            # We use a simple expansion: if it's a plain UID or comma list, split;
            # if it's a range (e.g. 1:5), let the DB do the work via BETWEEN.
            previews: list[MessagePreview] = []
            if ":" in uid_set_str and uid_set_str != "1:*":
                # Range like "41:71" — fetch ordered by uid, limit to max_messages
                parts = uid_set_str.split(":")
                lo, hi = int(parts[0]), int(parts[1])
                async with db.execute(
                    "SELECT uid, sender, subject, date_sent FROM messages "
                    "WHERE folder_id = ? AND uid BETWEEN ? AND ? ORDER BY uid LIMIT ?",
                    (folder_id, lo, hi, max_messages),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                # Individual UIDs (comma-separated) or single UID
                uids = [int(u.strip()) for u in uid_set_str.split(",") if u.strip().isdigit()]
                if not uids:
                    return []
                uids = uids[:max_messages]
                placeholders = ",".join("?" for _ in uids)
                async with db.execute(
                    f"SELECT uid, sender, subject, date_sent FROM messages "
                    f"WHERE folder_id = ? AND uid IN ({placeholders}) ORDER BY uid",
                    [folder_id, *uids],
                ) as cur:
                    rows = await cur.fetchall()

            for r in rows:
                previews.append(MessagePreview(
                    uid=r[0],
                    sender=r[1],
                    subject=r[2],
                    date_sent=r[3],
                ))
            # If messages table has no rows (e.g. deleted during MOVE staging),
            # fall back to snapshot which captures sender/subject at staging time.
            if not previews:
                return _previews_from_snapshot(row, max_messages)

            return previews
    except Exception:  # noqa: BLE001
        # Non-fatal: fall back to snapshot rather than breaking the API
        return _previews_from_snapshot(row, max_messages)


def _row_to_response(row: dict) -> OperationResponse:
    """Convert a raw DB dict to an OperationResponse, deserializing JSON fields."""
    return OperationResponse(**row)


async def _do_approve(op_id: str, row: dict, db_path: Path) -> ApproveResponse:
    """Core approve logic for a single operation.

    Executes the operation against the upstream server (SMTP relay or IMAP replay),
    updates status to 'executed', inserts audit log entry.

    Args:
        op_id: The operation ID.
        row: The raw DB row dict (must have status='pending').
        db_path: Path to the SQLite DB.

    Returns:
        ApproveResponse on success.

    Raises:
        HTTPException(500) if upstream execution fails.
    """
    protocol = row.get("protocol", "imap")

    if protocol == "smtp":
        # Deserialize smtp_envelope
        envelope_raw = row.get("smtp_envelope")
        if isinstance(envelope_raw, str):
            envelope = json.loads(envelope_raw)
        elif isinstance(envelope_raw, dict):
            envelope = envelope_raw
        else:
            envelope = {}

        sender = envelope.get("from", "")
        recipients = envelope.get("to", [])
        subject = envelope.get("subject", "<no subject>")
        body_preview = envelope.get("body_preview", "")

        from gateway.credentials import decrypt_credential  # noqa: PLC0415
        agent_id = row.get("agent_id")
        cred = await _get_agent_credential(agent_id, db_path)
        if cred:
            smtp_host = cred["upstream_host"]
            smtp_port = int(cred["upstream_smtp_port"])
            smtp_user = cred["upstream_user"]
            smtp_pass = decrypt_credential(cred["upstream_password"])
        else:
            smtp_host = os.environ.get("NUVRAIL_TEST_SMTP_HOST", "")
            smtp_port = int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587"))
            smtp_user = os.environ.get("NUVRAIL_TEST_SMTP_USER", "")
            smtp_pass = os.environ.get("NUVRAIL_TEST_SMTP_PASS", "")
            if not smtp_host:
                raise HTTPException(
                    status_code=500,
                    detail=f"Operation {op_id} has no agent_id and no fallback env vars set",
                )

        msg = MIMEText(body_preview or "(no body)", "plain")
        msg["From"] = smtp_user  # always use real upstream address
        msg["To"] = ", ".join(recipients) if isinstance(recipients, list) else recipients
        msg["Subject"] = subject

        try:
            await aiosmtplib.send(
                msg,
                hostname=smtp_host,
                port=smtp_port,
                username=smtp_user,
                password=smtp_pass,
                start_tls=True,
            )
            logger.info("[approve] SMTP relay succeeded for %s", op_id)
        except Exception as exc:
            logger.error("[approve] SMTP relay failed for %s: %s", op_id, exc)
            await update_operation_status(op_id, "failed", error=str(exc), db_path=db_path)
            async with get_db(db_path) as db:
                await db.execute(
                    """
                    INSERT INTO audit_log (timestamp, operation_id, event, actor, detail)
                    VALUES (?, ?, 'execution_failed', 'system', ?)
                    """,
                    (int(time.time()), op_id, json.dumps({"error": str(exc)})),
                )
                await db.commit()
            raise HTTPException(status_code=500, detail=f"SMTP relay failed: {exc}") from exc

    else:
        # IMAP ops: replay the stored command against the upstream IMAP server.
        try:
            await _execute_imap_upstream(row, db_path)
        except Exception as exc:
            logger.error("[approve] IMAP execution failed for %s: %s", op_id, exc)
            await update_operation_status(op_id, "failed", error=str(exc), db_path=db_path)
            async with get_db(db_path) as db:
                await db.execute(
                    """
                    INSERT INTO audit_log (timestamp, operation_id, event, actor, detail)
                    VALUES (?, ?, 'execution_failed', 'system', ?)
                    """,
                    (int(time.time()), op_id, json.dumps({"error": str(exc)})),
                )
                await db.commit()
            raise HTTPException(
                status_code=500, detail=f"IMAP execution failed: {exc}"
            ) from exc

    # Mark operation as executed and insert audit log
    await update_operation_status(op_id, "executed", db_path=db_path)
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, operation_id, event, actor, detail)
            VALUES (?, ?, 'executed', 'human', NULL)
            """,
            (int(time.time()), op_id),
        )
        await db.commit()

    updated = await get_operation(op_id, db_path=db_path)
    return ApproveResponse(
        id=op_id,
        status="executed",
        executed_at=updated.get("executed_at") if updated else None,
    )


async def _do_reject(op_id: str, row: dict, db_path: Path) -> RejectResponse:  # noqa: ARG001
    """Core reject logic for a single operation.

    Updates status to 'rejected', inserts audit log entry, attempts snapshot
    revert (non-fatal).

    Args:
        op_id: The operation ID.
        row: The raw DB row dict (must have status='pending').
        db_path: Path to the SQLite DB.

    Returns:
        RejectResponse on success.
    """
    await update_operation_status(op_id, "rejected", db_path=db_path)
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, operation_id, event, actor, detail)
            VALUES (?, ?, 'rejected', 'human', NULL)
            """,
            (int(time.time()), op_id),
        )
        await db.commit()

    # Restore local state DB from snapshot and queue unsolicited FETCH responses.
    # Non-fatal: if revert fails (e.g. no snapshot), rejection still succeeds.
    try:
        reverts = await restore_from_snapshot(op_id, db_path=db_path)
        await insert_pending_reverts(op_id, reverts, db_path=db_path)
        if reverts:
            logger.info(
                "[reject] Restored snapshot and queued %d pending_reverts for op %s",
                len(reverts),
                op_id,
            )
    except Exception as exc:
        logger.warning("[reject] Snapshot revert failed for op %s (non-fatal): %s", op_id, exc)

    return RejectResponse(id=op_id, status="rejected")


# ---------------------------------------------------------------------------
# Endpoints — batch routes FIRST (before parameterised /{op_id} routes)
# ---------------------------------------------------------------------------


@router.get("/operations", response_model=OperationListResponse)
async def list_ops(
    status: Optional[str] = None,
    agent_id: Optional[int] = Query(default=None, ge=1),
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> OperationListResponse:
    """List staged operations, optionally filtered by status."""
    rows = await list_operations(status=status, agent_id=agent_id, db_path=db_path)
    ops = []
    for r in rows:
        op = _row_to_response(r)
        op.message_previews = await _fetch_message_previews(r, db_path)
        ops.append(op)
    return OperationListResponse(operations=ops, total=len(ops))


@router.post("/operations/batch/approve", response_model=BatchApproveResponse)
async def batch_approve(
    body: BatchApproveRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> BatchApproveResponse:
    """Approve multiple operations by ID.

    Processing rules:
    - Operations are approved in the order provided.
    - SMTP sends are approved individually in sequence (no special handling needed).
    - If an op is not found: added to skipped list.
    - If an op is not 'pending': added to skipped list (already decided).
    - If approve execution fails: added to failed list with error; processing continues.
    - Empty list: returns 400.
    - More than 100 IDs: returns 400.
    """
    if not body.operation_ids:
        raise HTTPException(status_code=400, detail="operation_ids must not be empty")
    if len(body.operation_ids) > 100:
        raise HTTPException(
            status_code=400,
            detail=f"Too many operation IDs: {len(body.operation_ids)} (max 100)",
        )

    approved: list[BatchApproveResult] = []
    failed: list[BatchApproveResult] = []
    skipped: list[BatchApproveResult] = []

    for op_id in body.operation_ids:
        row = await get_operation(op_id, db_path=db_path)
        if row is None:
            skipped.append(BatchApproveResult(id=op_id, status="skipped", error="not found"))
            continue
        if row["status"] != "pending":
            skipped.append(
                BatchApproveResult(
                    id=op_id,
                    status="skipped",
                    error=f"already in status '{row['status']}'",
                )
            )
            continue
        try:
            result = await _do_approve(op_id, row, db_path)
            approved.append(
                BatchApproveResult(
                    id=op_id,
                    status="executed",
                    executed_at=result.executed_at,
                )
            )
        except Exception as exc:
            error_msg = str(exc)
            logger.error("[batch_approve] Failed to approve op %s: %s", op_id, error_msg)
            failed.append(BatchApproveResult(id=op_id, status="failed", error=error_msg))

    return BatchApproveResponse(
        approved=approved,
        failed=failed,
        skipped=skipped,
        total=len(body.operation_ids),
    )


@router.post("/operations/batch/reject", response_model=BatchRejectResponse)
async def batch_reject(
    body: BatchRejectRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> BatchRejectResponse:
    """Reject multiple operations by ID.

    Processing rules:
    - Operations are rejected in the order provided.
    - If an op is not found: added to skipped list.
    - If an op is not 'pending': added to skipped list (already decided).
    - If reject execution fails: added to failed list with error; processing continues.
    - Empty list: returns 400.
    - More than 100 IDs: returns 400.
    """
    if not body.operation_ids:
        raise HTTPException(status_code=400, detail="operation_ids must not be empty")
    if len(body.operation_ids) > 100:
        raise HTTPException(
            status_code=400,
            detail=f"Too many operation IDs: {len(body.operation_ids)} (max 100)",
        )

    rejected: list[BatchRejectResult] = []
    failed: list[BatchRejectResult] = []
    skipped: list[BatchRejectResult] = []

    for op_id in body.operation_ids:
        row = await get_operation(op_id, db_path=db_path)
        if row is None:
            skipped.append(BatchRejectResult(id=op_id, status="skipped", error="not found"))
            continue
        if row["status"] != "pending":
            skipped.append(
                BatchRejectResult(
                    id=op_id,
                    status="skipped",
                    error=f"already in status '{row['status']}'",
                )
            )
            continue
        try:
            await _do_reject(op_id, row, db_path)
            rejected.append(BatchRejectResult(id=op_id, status="rejected"))
        except Exception as exc:
            error_msg = str(exc)
            logger.error("[batch_reject] Failed to reject op %s: %s", op_id, error_msg)
            failed.append(BatchRejectResult(id=op_id, status="failed", error=error_msg))

    return BatchRejectResponse(
        rejected=rejected,
        failed=failed,
        skipped=skipped,
        total=len(body.operation_ids),
    )


# ---------------------------------------------------------------------------
# Single-op endpoints — AFTER batch routes to avoid /batch matching as op_id
# ---------------------------------------------------------------------------


@router.get("/operations/{op_id}", response_model=OperationResponse)
async def get_op(
    op_id: str,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> OperationResponse:
    """Retrieve a single operation by ID."""
    row = await get_operation(op_id, db_path=db_path)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    op = _row_to_response(row)
    op.message_previews = await _fetch_message_previews(row, db_path)
    return op


@router.post("/operations/{op_id}/approve", response_model=ApproveResponse)
async def approve_op(
    op_id: str,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> ApproveResponse:
    """Approve and execute an operation.

    - SMTP ops: relays the message to the upstream SMTP server via aiosmtplib.
    - IMAP ops: replays the stored command against the upstream IMAP server.
    """
    row = await get_operation(op_id, db_path=db_path)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Operation {op_id!r} is already in status '{row['status']}'",
        )
    return await _do_approve(op_id, row, db_path)


@router.post("/operations/{op_id}/reject", response_model=RejectResponse)
async def reject_op(
    op_id: str,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> RejectResponse:
    """Reject a pending operation."""
    row = await get_operation(op_id, db_path=db_path)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Operation {op_id!r} is already in status '{row['status']}'",
        )
    return await _do_reject(op_id, row, db_path)


@router.post("/operations/{op_id}/undo", response_model=UndoResponse)
async def undo_op(
    op_id: str,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> UndoResponse:
    """Undo an executed operation within the undo window.

    Reverses the IMAP command that was applied when the operation was approved.
    Only operations with status 'executed' and op_type in the undoable set
    (move, trash, archive, mark_read, mark_unread, star, unstar) can be undone.
    The undo window defaults to 24h and is configurable via UNDO_WINDOW_HOURS.
    """
    try:
        result = await undo_operation(op_id, db_path=db_path)
    except UndoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UndoResponse(
        id=op_id,
        status="reverted",
        op_type=result["op_type"],
        reverted=result["reverted"],
    )
