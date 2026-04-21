"""
/api/v1/rules endpoints — auto-approval rule management.

GET    /rules         — list all rules
POST   /rules         — create rule
PATCH  /rules/{id}    — update rule
DELETE /rules/{id}    — delete rule
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

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
    )


@router.get("/rules", response_model=list[AutoApprovalRule])
async def list_rules(
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> list[AutoApprovalRule]:
    """List rules ordered by evaluation order (priority DESC, id ASC)."""
    del current_user
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT *
            FROM auto_approval_rules
            ORDER BY priority DESC, id ASC
            """
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return [_row_to_rule(r) for r in rows]


@router.post("/rules", response_model=AutoApprovalRule, status_code=201)
async def create_rule(
    body: AutoApprovalRuleCreateRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> AutoApprovalRule:
    """Create an auto-approval rule."""
    del current_user
    now = int(time.time())
    async with get_db(db_path) as db:
        cur = await db.execute(
            """
            INSERT INTO auto_approval_rules
                (enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
    """Update a rule's fields."""
    del current_user
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

    values.append(rule_id)
    async with get_db(db_path) as db:
        cur = await db.execute(
            f"UPDATE auto_approval_rules SET {', '.join(columns)} WHERE id = ?",  # noqa: S608
            values,
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Rule {rule_id} not found",
            )
        await db.commit()
        async with db.execute(
            "SELECT * FROM auto_approval_rules WHERE id = ?",
            (rule_id,),
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
    """Delete a rule by ID."""
    del current_user
    async with get_db(db_path) as db:
        cur = await db.execute(
            "DELETE FROM auto_approval_rules WHERE id = ?",
            (rule_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Rule {rule_id} not found",
            )
        await db.commit()
