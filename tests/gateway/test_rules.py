"""
Unit tests for auto-approval rules engine matching.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from gateway.rules import evaluate_rules
from gateway.state_db import get_db, init_db


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "rules.db"
    await init_db(path)
    return path


async def _insert_rule(
    db_path: Path,
    *,
    enabled: int = 1,
    priority: int = 0,
    op_type: str | None = None,
    sender_pattern: str | None = None,
    folder_from: str | None = None,
    action: str = "approve",
    description: str = "test rule",
) -> int:
    async with get_db(db_path) as db:
        cur = await db.execute(
            """
            INSERT INTO auto_approval_rules
                (enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (enabled, priority, op_type, sender_pattern, folder_from, action, description),
        )
        await db.commit()
        return int(cur.lastrowid)


async def test_evaluate_rules_no_match_returns_none(db_path: Path) -> None:
    await _insert_rule(db_path, op_type="move", action="approve")
    result = await evaluate_rules({"op_type": "mark_read", "folder_from": "INBOX"}, db_path=db_path)
    assert result is None


async def test_evaluate_rules_op_type_wildcard(db_path: Path) -> None:
    await _insert_rule(db_path, op_type=None, action="reject")
    result = await evaluate_rules({"op_type": "mark_read", "folder_from": "INBOX"}, db_path=db_path)
    assert result == "reject"


async def test_evaluate_rules_sender_glob(db_path: Path) -> None:
    await _insert_rule(
        db_path,
        op_type="mark_read",
        sender_pattern="*@newsletter.com",
        action="approve",
    )
    result = await evaluate_rules(
        {
            "op_type": "mark_read",
            "folder_from": "INBOX",
            "smtp_envelope": {"from": "digest@newsletter.com"},
        },
        db_path=db_path,
    )
    assert result == "approve"


async def test_evaluate_rules_priority_and_first_match_wins(db_path: Path) -> None:
    # Lower priority rule inserted first.
    await _insert_rule(
        db_path,
        priority=5,
        op_type="mark_read",
        sender_pattern="*@substack.com",
        action="approve",
        description="lower priority",
    )
    # Higher priority conflicting rule should win.
    await _insert_rule(
        db_path,
        priority=10,
        op_type="mark_read",
        sender_pattern="*@substack.com",
        action="reject",
        description="higher priority",
    )
    result = await evaluate_rules(
        {
            "op_type": "mark_read",
            "smtp_envelope": {"from": "writer@substack.com"},
            "folder_from": "INBOX",
        },
        db_path=db_path,
    )
    assert result == "reject"


async def test_evaluate_rules_same_priority_uses_lowest_id_first(db_path: Path) -> None:
    await _insert_rule(
        db_path,
        priority=10,
        op_type="mark_read",
        sender_pattern="*@substack.com",
        action="approve",
        description="first rule",
    )
    await _insert_rule(
        db_path,
        priority=10,
        op_type="mark_read",
        sender_pattern="*@substack.com",
        action="reject",
        description="second rule",
    )
    result = await evaluate_rules(
        {
            "op_type": "mark_read",
            "smtp_envelope": {"from": "writer@substack.com"},
            "folder_from": "INBOX",
        },
        db_path=db_path,
    )
    assert result == "approve"


async def test_evaluate_rules_disabled_rule_ignored(db_path: Path) -> None:
    await _insert_rule(
        db_path,
        enabled=0,
        priority=100,
        op_type="mark_read",
        sender_pattern="*@substack.com",
        action="reject",
    )
    await _insert_rule(
        db_path,
        enabled=1,
        priority=1,
        op_type="mark_read",
        sender_pattern="*@substack.com",
        action="approve",
    )
    result = await evaluate_rules(
        {
            "op_type": "mark_read",
            "smtp_envelope": {"from": "writer@substack.com"},
            "folder_from": "INBOX",
        },
        db_path=db_path,
    )
    assert result == "approve"
