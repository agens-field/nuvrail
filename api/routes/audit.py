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
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from api.auth import get_current_user
from api.models import AuditEntry, AuditListResponse
from api.routes.operations import get_db_path
from gateway.audit import (
    get_chain_head,
    last_verification_result,
    read_last_anchor,
    verify_against_anchor,
    verify_audit_chain,
)
from gateway.state_db import get_db

router = APIRouter()


def _row_to_entry(row: dict) -> AuditEntry:
    """Convert a raw DB row (audit_log LEFT JOIN staged_operations) to AuditEntry."""
    detail_raw = row.get("detail")
    detail_parsed: dict | None = None
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
        undo_expires_at=row.get("undo_expires_at"),
        intent_label=row.get("intent_label"),
    )


@router.get("/audit", response_model=AuditListResponse)
async def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    event: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    agent_id: int | None = Query(default=None, ge=1),
    op_type: str | None = Query(default=None, description="Filter by operation type (move, trash, smtp_send, …)"),
    intent: str | None = Query(default=None, description="Filter by semantic intent (archive, delete, mark_spam, …)"),
    since: int | None = Query(default=None, description="Unix timestamp — only entries at or after this time"),
    until: int | None = Query(default=None, description="Unix timestamp — only entries at or before this time"),
    search: str | None = Query(default=None, description="Case-insensitive substring match on operation description"),
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> AuditListResponse:
    """
    List audit log entries, newest first, with joined operation context.

    Paginates with limit/offset.
    Optional filters: event, actor, agent_id, op_type, since, until, search.
    """
    user_id = current_user["id"]
    conditions: list[str] = [
        # Scope to operations belonging to the current user's agents.
        # audit_log.agent_id → agent_credentials.id → agent_credentials.user_id
        # Also include system/expiry events where agent_id is NULL but the
        # operation itself belongs to this user.
        (
            "(ac.user_id = ? OR (a.agent_id IS NULL AND op.id IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM staged_operations op2"
            "  JOIN agent_credentials ac2 ON op2.agent_id = ac2.id"
            "  WHERE op2.id = a.operation_id AND ac2.user_id = ?"
            ")))"
        )
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
    if op_type is not None:
        conditions.append("COALESCE(a.op_type, op.op_type) = ?")
        params.append(op_type)
    if intent is not None:
        conditions.append("COALESCE(a.intent_label, op.intent_label) = ?")
        params.append(intent)
    if since is not None:
        conditions.append("a.timestamp >= ?")
        params.append(since)
    if until is not None:
        conditions.append("a.timestamp <= ?")
        params.append(until)
    if search is not None and search.strip():
        conditions.append("op.description LIKE ?")
        params.append(f"%{search.strip()}%")

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
            op.status        AS op_status,
            op.undo_expires_at AS undo_expires_at,
            COALESCE(a.intent_label, op.intent_label) AS intent_label
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


@router.get("/agents/{agent_id}/audit", response_model=AuditListResponse)
async def get_agent_audit(
    agent_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> AuditListResponse:
    """
    Paginated audit log for a specific agent.

    User-scoped: returns 404 if the agent does not belong to the authenticated user.
    Entries are ordered newest first.
    """
    user_id = current_user["id"]

    # Verify the agent belongs to the current user.
    async with get_db(db_path) as db, db.execute(
        "SELECT id FROM agent_credentials WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
        (agent_id, user_id),
    ) as cur:
        agent_row = await cur.fetchone()

    if agent_row is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    # Reuse the same join structure as list_audit, but scoped to this agent.
    join_clause = """
        FROM audit_log a
        LEFT JOIN staged_operations op ON a.operation_id = op.id
        LEFT JOIN agent_credentials ac ON a.agent_id = ac.id
        LEFT JOIN agent_credentials op_ac ON op.agent_id = op_ac.id
    """
    where = """
        WHERE (a.agent_id = ? OR (a.agent_id IS NULL AND op.agent_id = ?))
          AND (
                ac.user_id = ?
                OR op_ac.user_id = ?
                OR (a.agent_id IS NULL AND op.agent_id IS NULL)
          )
    """
    params: list[object] = [agent_id, agent_id, user_id, user_id]

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
            op.status        AS op_status,
            op.undo_expires_at AS undo_expires_at,
            COALESCE(a.intent_label, op.intent_label) AS intent_label
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
    agent_id: int | None = Query(default=None, ge=1),
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
        (
            "(ac.user_id = ? OR (a.agent_id IS NULL AND op.id IS NOT NULL AND EXISTS ("
            "    SELECT 1 FROM staged_operations op2"
            "    JOIN agent_credentials ac2 ON op2.agent_id = ac2.id"
            "    WHERE op2.id = a.operation_id AND ac2.user_id = ?"
            ")))"
        )
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
            op.status        AS op_status,
            op.undo_expires_at AS undo_expires_at,
            COALESCE(a.intent_label, op.intent_label) AS intent_label
        FROM audit_log a
        LEFT JOIN staged_operations op ON a.operation_id = op.id
        LEFT JOIN agent_credentials ac ON a.agent_id = ac.id
        LEFT JOIN agent_credentials op_ac ON op.agent_id = op_ac.id
        {where}
        ORDER BY a.id ASC
    """
    async with get_db(db_path) as db, db.execute(select_sql, params) as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    entries = [_row_to_entry(r).model_dump() for r in rows]
    return JSONResponse(
        content={"audit_log": entries, "total": len(entries)},
        headers={"Content-Disposition": "attachment; filename=nuvrail-audit.json"},
    )


@router.get("/audit/verify")
async def verify_audit(
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Verify the integrity of the append-only audit-log hash chain on demand.

    The chain is global (a single tamper-evident chain across all activity), so
    this returns a summary only — whether the chain is intact, how many rows are
    broken, and the current chain head — never another account's row contents.
    Per-row breakage detail is written to the server log, not returned here.

    Also reports ``last_background_check`` so callers can see the most recent
    result from the scheduled verification loop without forcing a fresh scan.

    When external chain-head anchoring is enabled (NUVRAIL_AUDIT_ANCHOR_PATH),
    the response also carries ``anchor_ok`` / ``anchor_reason`` — a
    tail-truncation check the in-DB chain walk structurally cannot perform —
    and top-level ``ok`` is the AND of the in-DB chain being intact and no tail
    truncation being detected. ``chain_ok`` reports the in-DB walk alone.
    """
    ok, errors = await verify_audit_chain(db_path)
    head = await get_chain_head(db_path)
    # Tail-truncation check against the external anchor (no-op / anchor_ok=True
    # when anchoring is disabled). Deleting the most-recent rows leaves an
    # internally-consistent shorter chain, so ``ok`` alone cannot catch it.
    anchor_ok, anchor_reason = await verify_against_anchor(db_path)
    last_anchor = read_last_anchor()
    return JSONResponse(
        content={
            "ok": ok and anchor_ok,
            "chain_ok": ok,
            "broken_count": len(errors),
            "chain_head": head,
            "anchor_ok": anchor_ok,
            "anchor_reason": anchor_reason,
            "anchoring_enabled": last_anchor is not None,
            "last_anchored_at": (last_anchor or {}).get("anchored_at"),
            "verified_at": int(time.time()),
            "last_background_check": last_verification_result(),
        }
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
            op.status        AS op_status,
            op.undo_expires_at AS undo_expires_at,
            COALESCE(a.intent_label, op.intent_label) AS intent_label
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
    async with (
        get_db(db_path) as db,
        db.execute(select_sql, (entry_id, user_id, user_id)) as cur,
    ):
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Audit entry {entry_id} not found")
    return _row_to_entry(dict(row))
