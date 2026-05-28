"""
/api/v1/rules endpoints — auto-approval rule management.

GET    /rules         — list all rules (includes per-rule hit count)
POST   /rules         — create rule
POST   /rules/test    — dry-run: which rule fires for a sample operation?
PATCH  /rules/{id}    — update rule
DELETE /rules/{id}    — delete rule
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import get_current_user
from api.models import (
    AutoApprovalRule,
    AutoApprovalRuleCreateRequest,
    AutoApprovalRuleUpdateRequest,
)
from api.routes.operations import get_db_path
from gateway.state_db import get_db

router = APIRouter()


def _row_to_rule(row: dict) -> AutoApprovalRule:
    return AutoApprovalRule(
        id=row["id"],
        enabled=bool(row["enabled"]),
        priority=row["priority"],
        op_type=row["op_type"],
        sender_pattern=row["sender_pattern"],
        folder_from=row["folder_from"],
        action=row["action"],
        description=row["description"],
        created_at=row["created_at"],
        hits=row.get("hits", 0) or 0,
    )


@router.get("/rules", response_model=list[AutoApprovalRule])
async def list_rules(
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> list[AutoApprovalRule]:
    """List rules ordered by evaluation order (priority DESC, id ASC).

    Each rule includes a `hits` count — the number of operations that were
    auto-approved or auto-rejected by that rule, derived from the audit log.

    Scoped to the authenticated user's own rules.
    """
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT r.*,
                   COALESCE((
                       SELECT COUNT(*)
                       FROM audit_log a
                       WHERE a.actor = 'auto_rule'
                         AND json_valid(a.detail)
                         AND CAST(json_extract(a.detail, '$.rule_id') AS INTEGER) = r.id
                   ), 0) AS hits
            FROM auto_approval_rules r
            WHERE r.user_id = ?
            ORDER BY r.priority DESC, r.id ASC
            """,
            (current_user["id"],),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return [_row_to_rule(r) for r in rows]


class RuleTestRequest(BaseModel):
    """Sample operation fields to dry-run against the current ruleset."""
    op_type: str
    sender: Optional[str] = None
    folder_from: Optional[str] = None


class RuleTestResponse(BaseModel):
    matched: bool
    action: Optional[str] = None       # 'approve' | 'reject' | None
    rule_id: Optional[int] = None
    rule_description: Optional[str] = None


@router.post("/rules/test", response_model=RuleTestResponse)
async def test_rule(
    body: RuleTestRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> RuleTestResponse:
    """Dry-run the ruleset against a sample operation.

    Returns the first matching rule and what action it would take.
    No operation is staged or executed. Scoped to the user's own rules.
    """
    from gateway.rules import get_matching_rule  # noqa: PLC0415

    op = {
        "op_type": body.op_type,
        "sender": body.sender,
        "folder_from": body.folder_from,
    }
    matched_rule = await get_matching_rule(
        op, db_path=db_path, user_id=current_user["id"]
    )
    if matched_rule is None:
        return RuleTestResponse(matched=False)
    return RuleTestResponse(
        matched=True,
        action=matched_rule.get("action"),
        rule_id=matched_rule.get("id"),
        rule_description=matched_rule.get("description"),
    )


@router.post("/rules", response_model=AutoApprovalRule, status_code=201)
async def create_rule(
    body: AutoApprovalRuleCreateRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> AutoApprovalRule:
    """Create an auto-approval rule owned by the authenticated user."""
    now = int(time.time())
    async with get_db(db_path) as db:
        cur = await db.execute(
            """
            INSERT INTO auto_approval_rules
                (user_id, enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current_user["id"],
                1 if body.enabled else 0,
                body.priority,
                body.op_type,
                body.sender_pattern,
                body.folder_from,
                body.action,
                body.description,
                now,
            ),
        )
        await db.commit()
        rule_id = cur.lastrowid

        async with db.execute(
            "SELECT * FROM auto_approval_rules WHERE id = ?",
            (rule_id,),
        ) as cur2:
            row = await cur2.fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Rule creation failed")
    return _row_to_rule(dict(row))


@router.patch("/rules/{rule_id}", response_model=AutoApprovalRule)
async def update_rule(
    rule_id: int,
    body: AutoApprovalRuleUpdateRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> AutoApprovalRule:
    """Update a rule's fields (only if it belongs to the authenticated user)."""
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update")

    columns: list[str] = []
    values: list[object] = []
    for key, value in update_data.items():
        columns.append(f"{key} = ?")
        if key == "enabled" and isinstance(value, bool):
            values.append(1 if value else 0)
        else:
            values.append(value)

    values.extend([rule_id, current_user["id"]])
    async with get_db(db_path) as db:
        cur = await db.execute(
            f"UPDATE auto_approval_rules SET {', '.join(columns)} WHERE id = ? AND user_id = ?",  # noqa: S608
            values,
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Rule {rule_id} not found",
            )
        await db.commit()
        async with db.execute(
            "SELECT * FROM auto_approval_rules WHERE id = ? AND user_id = ?",
            (rule_id, current_user["id"]),
        ) as cur2:
            row = await cur2.fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="Rule update failed")
    return _row_to_rule(dict(row))


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete a rule by ID (only if it belongs to the authenticated user)."""
    async with get_db(db_path) as db:
        cur = await db.execute(
            "DELETE FROM auto_approval_rules WHERE id = ? AND user_id = ?",
            (rule_id, current_user["id"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Rule {rule_id} not found",
            )
        await db.commit()
