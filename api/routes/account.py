"""
Account self-service endpoints.

GET  /api/v1/account/token        — view current token metadata (issue #28)
POST /api/v1/account/token/rotate — revoke old token, issue new one (issue #28)
GET  /api/v1/account/export       — GDPR data portability export (issue #27)
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from api.auth import generate_token, get_current_user, hash_token_for_storage
from api.models import (
    DataExportResponse,
    ExportAccount,
    ExportAgent,
    ExportAuditEntry,
    ExportAutoApprovalRule,
    ExportOperation,
    TokenInfoResponse,
    TokenRotateResponse,
)
from api.routes.operations import get_db_path
from gateway.state_db import get_db

router = APIRouter()


# ---------------------------------------------------------------------------
# Issue #28 — Token info + rotate
# ---------------------------------------------------------------------------


@router.get("/account/token", response_model=TokenInfoResponse)
async def get_token_info(
    current_user: dict = Depends(get_current_user),
) -> TokenInfoResponse:
    """Return metadata about the current API token.

    Returns created_at and last_used_at timestamps (both nullable).
    Does not reveal the token itself.
    """
    return TokenInfoResponse(
        created_at=current_user.get("api_token_created_at"),
        last_used_at=current_user.get("api_token_last_used_at"),
    )


@router.post("/account/token/rotate", response_model=TokenRotateResponse)
async def rotate_token(
    current_user: dict = Depends(get_current_user),
    db_path: Path = Depends(get_db_path),
) -> TokenRotateResponse:
    """Revoke the current API token and issue a new one.

    The new plaintext token is returned ONCE in this response.
    It is never stored in plaintext — only its SHA-256 hash is persisted.
    """
    now = int(time.time())
    new_token = generate_token()
    async with get_db(db_path) as db:
        await db.execute(
            """
            UPDATE users
            SET api_token = ?, api_token_created_at = ?, api_token_last_used_at = NULL
            WHERE id = ?
            """,
            (hash_token_for_storage(new_token), now, current_user["id"]),
        )
        await db.commit()

    return TokenRotateResponse(
        token=new_token,
        token_type="bearer",
        user_id=current_user["id"],
        email=current_user["email"],
    )


# ---------------------------------------------------------------------------
# Issue #27 — GDPR data export
# ---------------------------------------------------------------------------


@router.get("/account/export")
async def export_account_data(
    current_user: dict = Depends(get_current_user),
    db_path: Path = Depends(get_db_path),
) -> Response:
    """Export all account data as a JSON attachment.

    Returns:
      - account: email, display_name, created_at
      - agents: label, upstream_host, upstream_user, created_at, revoked_at
        (NO credential values — passwords/tokens/keys are never exported)
      - operations: full staged_operations history for this user's agents
      - audit_log: all audit entries scoped to this user
      - auto_approval_rules: all rules for this user

    Writes an 'export_requested' audit event.
    Sets Content-Disposition: attachment for browser download.
    """
    now = int(time.time())
    user_id: int = current_user["id"]

    async with get_db(db_path) as db:
        # Fetch agents (no credentials)
        async with db.execute(
            """
            SELECT id, label, upstream_host, upstream_user, created_at, revoked_at
            FROM agent_credentials
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (user_id,),
        ) as cur:
            agent_rows = [dict(r) for r in await cur.fetchall()]

        agent_ids = [str(r["id"]) for r in agent_rows]

        # Fetch staged_operations for all this user's agents
        operations_list: list[dict] = []
        if agent_ids:
            placeholders = ",".join("?" * len(agent_ids))
            async with db.execute(
                f"""
                SELECT id, created_at, expires_at, status, op_type, protocol,
                       description, agent_id, decided_at, executed_at, error
                FROM staged_operations
                WHERE agent_id IN ({placeholders})
                ORDER BY created_at ASC
                """,  # noqa: S608
                agent_ids,
            ) as cur:
                operations_list = [dict(r) for r in await cur.fetchall()]

        # Fetch audit_log scoped to this user (via user_id column added in issue #5)
        async with db.execute(
            """
            SELECT id, timestamp, operation_id, event, actor, detail
            FROM audit_log
            WHERE user_id = ?
            ORDER BY timestamp ASC
            """,
            (user_id,),
        ) as cur:
            audit_rows = [dict(r) for r in await cur.fetchall()]

        # Fetch auto_approval_rules
        async with db.execute(
            """
            SELECT id, enabled, priority, op_type, sender_pattern,
                   folder_from, action, description, created_at
            FROM auto_approval_rules
            ORDER BY priority DESC, created_at ASC
            """,
        ) as cur:
            rule_rows = [dict(r) for r in await cur.fetchall()]

        # Write 'export_requested' audit event
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, operation_id, event, actor, user_id, detail)
            VALUES (?, NULL, 'export_requested', 'human', ?, NULL)
            """,
            (now, user_id),
        )
        await db.commit()

    export = DataExportResponse(
        exported_at=now,
        account=ExportAccount(
            email=current_user["email"],
            display_name=current_user.get("display_name"),
            created_at=current_user["created_at"],
        ),
        agents=[ExportAgent(**r) for r in agent_rows],
        operations=[ExportOperation(**r) for r in operations_list],
        audit_log=[ExportAuditEntry(**r) for r in audit_rows],
        auto_approval_rules=[
            ExportAutoApprovalRule(
                **{**r, "enabled": bool(r["enabled"])}
            )
            for r in rule_rows
        ],
    )

    timestamp_str = str(now)
    filename = f"nuvrail-export-{timestamp_str}.json"
    return Response(
        content=export.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



