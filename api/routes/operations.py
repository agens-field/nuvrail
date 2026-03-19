"""
/api/v1/operations endpoints — Milestone 1.0.

GET    /operations              — list (optional ?status= filter)
GET    /operations/{op_id}      — single operation detail
POST   /operations/{op_id}/approve  — approve + execute against upstream
POST   /operations/{op_id}/reject   — reject

Approve logic:
  - SMTP ops: relay via aiosmtplib to upstream SMTP server
  - IMAP ops: mark executed (actual upstream execution deferred to 1.1)

Sub-milestone: 1.0
"""
from __future__ import annotations

import json
import logging
import os
import time
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

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
        # IMAP ops: mark as executed — actual upstream execution deferred to 1.1
        # TODO (1.1): replay IMAP command against upstream using stored imap_command
        logger.info(
            "[approve] IMAP op %s marked executed (upstream replay deferred to 1.1)", op_id
        )

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
