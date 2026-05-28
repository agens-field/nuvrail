"""
Unit tests for auto-approval rules engine matching.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from gateway.rules import evaluate_rules
from gateway.state_db import get_db, init_db

# Rules are tenant-scoped; tests insert and evaluate against this owning user.
_TEST_USER_ID = 1


async def _eval(op: dict, db_path: Path, user_id: int = _TEST_USER_ID) -> str | None:
    """Evaluate rules for the test owner user (rules are user-scoped)."""
    return await evaluate_rules(op, db_path=db_path, user_id=user_id)


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "rules.db"
    await init_db(path)
    return path


async def _insert_rule(
    db_path: Path,
    *,
    user_id: int = _TEST_USER_ID,
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
                (user_id, enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (user_id, enabled, priority, op_type, sender_pattern, folder_from, action, description),
        )
        await db.commit()
        return int(cur.lastrowid)


async def test_evaluate_rules_no_match_returns_none(db_path: Path) -> None:
    await _insert_rule(db_path, op_type="move", action="approve")
    result = await _eval({"op_type": "mark_read", "folder_from": "INBOX"}, db_path=db_path)
    assert result is None


async def test_evaluate_rules_op_type_wildcard(db_path: Path) -> None:
    await _insert_rule(db_path, op_type=None, action="reject")
    result = await _eval({"op_type": "mark_read", "folder_from": "INBOX"}, db_path=db_path)
    assert result == "reject"


async def test_evaluate_rules_sender_glob(db_path: Path) -> None:
    await _insert_rule(
        db_path,
        op_type="mark_read",
        sender_pattern="*@newsletter.com",
        action="approve",
    )
    result = await _eval(
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
    result = await _eval(
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
    result = await _eval(
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
    result = await _eval(
        {
            "op_type": "mark_read",
            "smtp_envelope": {"from": "writer@substack.com"},
            "folder_from": "INBOX",
        },
        db_path=db_path,
    )
    assert result == "approve"


async def test_evaluate_rules_are_tenant_scoped(db_path: Path) -> None:
    """A rule owned by one user must never act on another user's operation."""
    op = {
        "op_type": "mark_read",
        "smtp_envelope": {"from": "writer@substack.com"},
        "folder_from": "INBOX",
    }
    # Rule belongs to user 1.
    await _insert_rule(
        db_path,
        user_id=1,
        op_type="mark_read",
        sender_pattern="*@substack.com",
        action="approve",
    )
    # User 1 sees the match...
    assert await _eval(op, db_path=db_path, user_id=1) == "approve"
    # ...but user 2 does not — the rule is invisible to them.
    assert await _eval(op, db_path=db_path, user_id=2) is None


async def test_evaluate_rules_no_user_matches_nothing(db_path: Path) -> None:
    """With no owning user (e.g. ownerless operation), no rule is applied."""
    await _insert_rule(db_path, user_id=1, op_type=None, action="approve")
    result = await evaluate_rules(
        {"op_type": "mark_read", "folder_from": "INBOX"},
        db_path=db_path,
        user_id=None,
    )
    assert result is None
