"""
/api/v1/audit endpoints — Milestone 1.2.

GET  /audit               — query audit log (paginated, filterable)
GET  /audit/{entry_id}    — single audit entry with joined operation detail
GET  /audit/export        — full audit log as JSON download

Audit log is append-only and immutable. No write endpoints exist here.
Each entry is joined with staged_operations so callers get the operation
description, type, and protocol without a second request.

Query parameters for GET /audit:
  limit  int  default=50, max=500
  offset int  default=0
  event  str  filter by event name (staged/approved/rejected/executed/execution_failed)
  actor  str  filter by actor (ai_agent/human/system)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from api.auth import get_current_user
from api.models import AuditEntry, AuditListResponse
from api.routes.operations import get_db_path
from gateway.state_db import get_db

router = APIRouter()


def _row_to_entry(row: dict) -> AuditEntry:
    """Convert a raw DB row (audit_log LEFT JOIN staged_operations) to AuditEntry."""
    detail_raw = row.get("detail")
    detail_parsed: Optional[dict] = None
    if isinstance(detail_raw, str):
        try:
            detail_parsed = json.loads(detail_raw)
        except (json.JSONDecodeError, TypeError):
            detail_parsed = None
    elif isinstance(detail_raw, dict):
        detail_parsed = detail_raw

    return AuditEntry(
        id=row["id"],
        timestamp=row["timestamp"],
        operation_id=row.get("operation_id"),
        event=row["event"],
        actor=row.get("actor"),
        agent_id=row.get("agent_id"),
        agent_label=row.get("agent_label"),
        detail=detail_parsed,
        # Joined fields from staged_operations (may be None if op was deleted)
        op_description=row.get("op_description"),
        op_type=row.get("op_type"),
        op_protocol=row.get("op_protocol"),
        op_status=row.get("op_status"),
    )


@router.get("/audit", response_model=AuditListResponse)
async def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    event: Optional[str] = Query(default=None),
    actor: Optional[str] = Query(default=None),
    agent_id: Optional[int] = Query(default=None, ge=1),
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> AuditListResponse:
    """
    List audit log entries, newest first, with joined operation context.

    Paginates with limit/offset. Optional filters: event, actor.
    """
    user_id = current_user["id"]
    conditions: list[str] = [
        # Scope to operations belonging to the current user's agents.
        # audit_log.agent_id → agent_credentials.id → agent_credentials.user_id
        # Also include system/expiry events where agent_id is NULL but the
        # operation itself belongs to this user.
        "(ac.user_id = ? OR (a.agent_id IS NULL AND op.id IS NOT NULL AND EXISTS ("
        "  SELECT 1 FROM staged_operations op2"
        "  JOIN agent_credentials ac2 ON op2.agent_id = ac2.id"
        "  WHERE op2.id = a.operation_id AND ac2.user_id = ?"
        ")))"
    ]
    params: list[object] = [user_id, user_id]

    if event is not None:
        conditions.append("a.event = ?")
        params.append(event)
    if actor is not None:
        conditions.append("a.actor = ?")
        params.append(actor)
    if agent_id is not None:
        conditions.append("(a.agent_id = ? OR (a.agent_id IS NULL AND op.agent_id = ?))")
        params.extend([agent_id, agent_id])

    where = "WHERE " + " AND ".join(conditions)

    # Both queries join agent_credentials so we can filter by user_id.
    # LEFT JOIN keeps entries where agent_id is NULL (system events).
    join_clause = """
        FROM audit_log a
        LEFT JOIN staged_operations op ON a.operation_id = op.id
        LEFT JOIN agent_credentials ac ON a.agent_id = ac.id
        LEFT JOIN agent_credentials op_ac ON op.agent_id = op_ac.id
    """

    count_sql = f"SELECT COUNT(*) {join_clause} {where}"
    select_sql = f"""
        SELECT
            a.id,
            a.timestamp,
            a.operation_id,
            a.event,
            a.actor,
            COALESCE(a.agent_id, op.agent_id) AS agent_id,
            COALESCE(ac.label, op_ac.label) AS agent_label,
            a.detail,
            op.description   AS op_description,
            op.op_type       AS op_type,
            op.protocol      AS op_protocol,
            op.status        AS op_status
        {join_clause}
        {where}
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
    """

    async with get_db(db_path) as db:
        async with db.execute(count_sql, params) as cur:
            total_row = await cur.fetchone()
            total = total_row[0] if total_row else 0

        async with db.execute(select_sql, [*params, limit, offset]) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    entries = [_row_to_entry(r) for r in rows]
    return AuditListResponse(entries=entries, total=total, limit=limit, offset=offset)


@router.get("/audit/export")
async def export_audit(
    agent_id: Optional[int] = Query(default=None, ge=1),
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """
    Export the complete audit log as a JSON download.

    Returns all entries (no pagination limit) with full joined operation detail.
    Suitable for compliance archival or external analysis.
    """
    user_id = current_user["id"]
    conditions: list[str] = [
        "(ac.user_id = ? OR (a.agent_id IS NULL AND op.id IS NOT NULL AND EXISTS ("
        "    SELECT 1 FROM staged_operations op2"
        "    JOIN agent_credentials ac2 ON op2.agent_id = ac2.id"
        "    WHERE op2.id = a.operation_id AND ac2.user_id = ?"
        ")))"
    ]
    params: list[object] = [user_id, user_id]
    if agent_id is not None:
        conditions.append("(a.agent_id = ? OR (a.agent_id IS NULL AND op.agent_id = ?))")
        params.extend([agent_id, agent_id])

    where = "WHERE " + " AND ".join(conditions)
    select_sql = f"""
        SELECT
            a.id,
            a.timestamp,
            a.operation_id,
            a.event,
            a.actor,
            COALESCE(a.agent_id, op.agent_id) AS agent_id,
            COALESCE(ac.label, op_ac.label) AS agent_label,
            a.detail,
            op.description   AS op_description,
            op.op_type       AS op_type,
            op.protocol      AS op_protocol,
            op.status        AS op_status
        FROM audit_log a
        LEFT JOIN staged_operations op ON a.operation_id = op.id
        LEFT JOIN agent_credentials ac ON a.agent_id = ac.id
        LEFT JOIN agent_credentials op_ac ON op.agent_id = op_ac.id
        {where}
        ORDER BY a.id ASC
    """
    async with get_db(db_path) as db:
        async with db.execute(select_sql, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    entries = [_row_to_entry(r).model_dump() for r in rows]
    return JSONResponse(
        content={"audit_log": entries, "total": len(entries)},
        headers={"Content-Disposition": "attachment; filename=nuvrail-audit.json"},
    )


@router.get("/audit/{entry_id}", response_model=AuditEntry)
async def get_audit_entry(
    entry_id: int,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> AuditEntry:
    """Retrieve a single audit log entry by its integer ID."""
    user_id = current_user["id"]
    select_sql = """
        SELECT
            a.id,
            a.timestamp,
            a.operation_id,
            a.event,
            a.actor,
            COALESCE(a.agent_id, op.agent_id) AS agent_id,
            COALESCE(ac.label, op_ac.label) AS agent_label,
            a.detail,
            op.description   AS op_description,
            op.op_type       AS op_type,
            op.protocol      AS op_protocol,
            op.status        AS op_status
        FROM audit_log a
        LEFT JOIN staged_operations op ON a.operation_id = op.id
        LEFT JOIN agent_credentials ac ON a.agent_id = ac.id
        LEFT JOIN agent_credentials op_ac ON op.agent_id = op_ac.id
        WHERE a.id = ?
          AND (ac.user_id = ? OR (a.agent_id IS NULL AND op.id IS NOT NULL AND EXISTS (
              SELECT 1 FROM staged_operations op2
              JOIN agent_credentials ac2 ON op2.agent_id = ac2.id
              WHERE op2.id = a.operation_id AND ac2.user_id = ?
          )))
    """
    async with get_db(db_path) as db:
        async with db.execute(select_sql, (entry_id, user_id, user_id)) as cur:
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Audit entry {entry_id} not found")
    return _row_to_entry(dict(row))
