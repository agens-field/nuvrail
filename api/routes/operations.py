"""
/api/v1/operations endpoints — Milestone 1.0.

GET    /operations              — list (optional ?status= filter)
GET    /operations/{op_id}      — single operation detail
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
from fastapi import APIRouter, Depends, HTTPException

from api.models import ApproveResponse, OperationListResponse, OperationResponse, RejectResponse
from gateway.staging import get_operation, list_operations, update_operation_status
from gateway.state_db import DB_PATH, get_db

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


async def _execute_imap_upstream(row: dict) -> None:
    """
    Replay a staged IMAP operation against the upstream server.

    Opens a fresh IMAP4_SSL connection, authenticates with plain LOGIN
    using upstream credentials from env, executes the operation, logs out.

    Supported op_types: store, move, copy, create, rename, trash, mark_read,
    flag, unflag, mark_unread (all map to UID STORE or UID MOVE/COPY/etc).
    APPEND is skipped — the message body is not stored in the staging DB.

    Raises RuntimeError on any upstream error so the caller can set
    operation status → 'failed'.
    """
    imap_host = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
    imap_port = int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993"))
    imap_user = os.environ.get("NUVRAIL_TEST_IMAP_USER", "")
    imap_pass = os.environ.get("NUVRAIL_TEST_IMAP_PASS", "")

    op_type = row.get("op_type", "")
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

    # APPEND: body not stored — skip upstream execution
    if op_type == "append":
        logger.info(
            "[imap_execute] Skipping APPEND op %s — body not stored in staging DB", row["id"]
        )
        return

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
                op_type, row["id"],
            )

        logger.info("[imap_execute] Op %s (%s) executed successfully", row["id"], op_type)

    finally:
        try:
            await client.logout()
        except Exception:
            pass


def _row_to_response(row: dict) -> OperationResponse:
    """Convert a raw DB dict to an OperationResponse, deserializing JSON fields."""
    return OperationResponse(**row)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/operations", response_model=OperationListResponse)
async def list_ops(
    status: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> OperationListResponse:
    """List staged operations, optionally filtered by status."""
    rows = await list_operations(status=status, db_path=db_path)
    ops = [_row_to_response(r) for r in rows]
    return OperationListResponse(operations=ops, total=len(ops))


@router.get("/operations/{op_id}", response_model=OperationResponse)
async def get_op(
    op_id: str,
    db_path: Path = Depends(get_db_path),
) -> OperationResponse:
    """Retrieve a single operation by ID."""
    row = await get_operation(op_id, db_path=db_path)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    return _row_to_response(row)


@router.post("/operations/{op_id}/approve", response_model=ApproveResponse)
async def approve_op(
    op_id: str,
    db_path: Path = Depends(get_db_path),
) -> ApproveResponse:
    """Approve and execute an operation.

    - SMTP ops: relays the message to the upstream SMTP server via aiosmtplib.
    - IMAP ops: marks as executed (upstream execution deferred to milestone 1.1).
    """
    row = await get_operation(op_id, db_path=db_path)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Operation {op_id!r} not found")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Operation {op_id!r} is already in status '{row['status']}'",
        )

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

        smtp_host = os.environ.get("NUVRAIL_TEST_SMTP_HOST", "")
        smtp_port = int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587"))
        smtp_user = os.environ.get("NUVRAIL_TEST_SMTP_USER", "")
        smtp_pass = os.environ.get("NUVRAIL_TEST_SMTP_PASS", "")

        msg = MIMEText(body_preview or "(no body)", "plain")
        msg["From"] = sender or smtp_user
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
            # Insert execution_failed audit log entry
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
            await _execute_imap_upstream(row)
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


@router.post("/operations/{op_id}/reject", response_model=RejectResponse)
async def reject_op(
    op_id: str,
    db_path: Path = Depends(get_db_path),
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

    return RejectResponse(id=op_id, status="rejected")
