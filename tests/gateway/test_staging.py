"""
Unit tests for the staging engine (Milestone 1.0).

Uses a tmp_path-isolated SQLite DB so tests never touch ~/.nuvrail.
"""
import re
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from gateway.staging import (
    create_operation,
    get_operation,
    list_operations,
    update_operation_status,
)
from gateway.state_db import get_db, init_db

OP_ID_PATTERN = re.compile(r"^op_[A-Za-z0-9]{6}$")


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    """Return a path to a freshly initialised test DB."""
    path = tmp_path / "test.db"
    await init_db(path)
    return path


async def _seed_agent(db_path: Path, user_id: int = 1) -> int:
    """Insert an agent_credentials row owned by ``user_id``; return its id.

    Auto-approval rules are tenant-scoped, so operations must carry an
    agent_id that resolves to the rule's owning user for the rule to apply.
    """
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, 'test', ?, 'x', 'imap.example.com', 993, 587,
                       'u@example.com', 'p', 0)""",
            (user_id, f"nuvrail_seed_{user_id}"),
        )
        await db.commit()
        return int(cur.lastrowid)


async def test_create_operation_returns_id(db_path: Path) -> None:
    """Operation ID must match op_XXXXXX pattern."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move 1 to Archive",
        db_path=db_path,
    )
    assert OP_ID_PATTERN.match(op_id), f"ID {op_id!r} does not match op_XXXXXX"


async def test_create_operation_inserts_db_row(db_path: Path) -> None:
    """Row is present in staged_operations with correct fields."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move 1 to Archive",
        imap_command="A001 MOVE 1 Archive",
        message_ids=["1"],
        folder_to="Archive",
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["id"] == op_id
    assert row["status"] == "pending"
    assert row["op_type"] == "move"
    assert row["protocol"] == "imap"
    assert row["description"] == "Move 1 to Archive"
    assert row["imap_command"] == "A001 MOVE 1 Archive"
    assert row["folder_to"] == "Archive"
    assert row["expires_at"] == row["created_at"] + 48 * 3600


async def test_create_operation_inserts_audit_log(db_path: Path) -> None:
    """A 'staged' audit_log row is inserted when an operation is created."""
    op_id = await create_operation(
        op_type="smtp_send",
        protocol="smtp",
        description="Send email",
        db_path=db_path,
    )
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM audit_log WHERE operation_id = ?", (op_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["event"] == "staged"
    assert row["actor"] == "ai_agent"
    assert row["operation_id"] == op_id


async def test_list_operations_filter_by_status(db_path: Path) -> None:
    """Pending ops returned; executed ops excluded when filtering by 'pending'."""
    pending_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Pending op",
        db_path=db_path,
    )
    executed_id = await create_operation(
        op_type="copy",
        protocol="imap",
        description="Executed op",
        db_path=db_path,
    )
    await update_operation_status(executed_id, "executed", db_path=db_path)

    pending_ops = await list_operations(status="pending", db_path=db_path)
    pending_ids = {op["id"] for op in pending_ops}
    assert pending_id in pending_ids
    assert executed_id not in pending_ids

    all_ops = await list_operations(db_path=db_path)
    all_ids = {op["id"] for op in all_ops}
    assert pending_id in all_ids
    assert executed_id in all_ids


async def test_get_operation_not_found(db_path: Path) -> None:
    """get_operation returns None for a non-existent ID."""
    result = await get_operation("op_000000", db_path=db_path)
    assert result is None


async def test_update_status(db_path: Path) -> None:
    """update_operation_status correctly transitions the status field."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Test op",
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "pending"

    await update_operation_status(op_id, "approved", db_path=db_path)
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "approved"
    assert row["decided_at"] is not None
    assert row["decided_by"] == "human"


async def test_update_status_executed_sets_executed_at(db_path: Path) -> None:
    """Transitioning to 'executed' also sets executed_at."""
    op_id = await create_operation(
        op_type="smtp_send",
        protocol="smtp",
        description="SMTP op",
        db_path=db_path,
    )
    await update_operation_status(op_id, "executed", db_path=db_path)
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "executed"
    assert row["executed_at"] is not None


async def test_update_status_with_error(db_path: Path) -> None:
    """Error string is stored when transitioning to 'failed'."""
    op_id = await create_operation(
        op_type="smtp_send",
        protocol="smtp",
        description="SMTP op",
        db_path=db_path,
    )
    await update_operation_status(
        op_id, "failed", error="Connection refused", db_path=db_path
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "Connection refused"


async def test_create_operation_with_snapshot(db_path: Path) -> None:
    """snapshot dict is stored as JSON in staged_operations.snapshot."""
    import json
    snap = {"42": {"flags": [r"\Seen"], "seq_num": 5, "folder_id": 1}}
    op_id = await create_operation(
        op_type="store",
        protocol="imap",
        description="Mark as read",
        imap_command="A001 UID STORE 42 +FLAGS (\\Seen)",
        snapshot=snap,
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    stored = json.loads(row["snapshot"])
    assert "42" in stored
    assert r"\Seen" in stored["42"]["flags"]


async def test_create_operation_without_snapshot_stores_null(db_path: Path) -> None:
    """Omitting snapshot stores NULL (not an empty string)."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move to Archive",
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["snapshot"] is None


async def test_create_operation_auto_approved_executes_upstream(db_path: Path) -> None:
    """A matching approve-rule must EXECUTE the operation upstream, not just mark it.

    Regression test: auto-approved operations used to be set to status
    'approved' and then never executed. They must now run through the same
    executor a human approval uses (gateway.execution.execute_operation),
    attributed to actor 'auto_rule'.
    """
    agent_id = await _seed_agent(db_path, user_id=1)
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO auto_approval_rules
                (user_id, enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (1, 1, 10, 'mark_read', '*@substack.com', NULL, 'approve', 'Substack mark-read', 0)
            """
        )
        await db.commit()

    async def _fake_execute(op_id, row, db_path, *, actor="human"):
        # Mimic the real executor's success side effect without touching upstream.
        await update_operation_status(op_id, "executed", decided_by=actor, db_path=db_path)
        return {"executed_at": 1234567890}

    with patch(
        "gateway.execution.execute_operation",
        new=AsyncMock(side_effect=_fake_execute),
    ) as mock_exec:
        op_id = await create_operation(
            op_type="mark_read",
            protocol="imap",
            description="Mark as read",
            agent_id=agent_id,
            smtp_envelope={"from": "digest@substack.com"},
            db_path=db_path,
        )

    # The executor must have been invoked exactly once, as the auto_rule actor.
    mock_exec.assert_awaited_once()
    _, kwargs = mock_exec.call_args
    assert kwargs.get("actor") == "auto_rule"
    assert mock_exec.call_args.args[0] == op_id

    # And the operation must reach the executed terminal state (not 'approved').
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "executed"

    # Audit trail: staged, then the auto_rule 'approved' decision (carries the
    # rule id for hit counting and op_type).
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT event, actor, op_type, detail FROM audit_log WHERE operation_id = ? ORDER BY id ASC",
            (op_id,),
        ) as cur:
            logs = await cur.fetchall()
    assert logs[0]["event"] == "staged"
    assert logs[1]["event"] == "approved"
    assert logs[1]["actor"] == "auto_rule"
    assert "Substack mark-read" in (logs[1]["detail"] or "")
    assert logs[1]["op_type"] == "mark_read", (
        f"auto_rule audit row must carry op_type; got: {logs[1]['op_type']!r}"
    )


async def test_create_operation_auto_approve_execution_failure_is_non_fatal(db_path: Path) -> None:
    """If upstream execution of an auto-approved op fails, staging still succeeds.

    The executor marks the op 'failed' itself; create_operation must not raise.
    """
    from gateway.execution import ExecutionError

    agent_id = await _seed_agent(db_path, user_id=1)
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO auto_approval_rules
                (user_id, enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (1, 1, 10, 'mark_read', '*@substack.com', NULL, 'approve', 'Substack mark-read', 0)
            """
        )
        await db.commit()

    with patch(
        "gateway.execution.execute_operation",
        new=AsyncMock(side_effect=ExecutionError("upstream boom")),
    ):
        # Must not raise despite the executor failing.
        op_id = await create_operation(
            op_type="mark_read",
            protocol="imap",
            description="Mark as read",
            agent_id=agent_id,
            smtp_envelope={"from": "digest@substack.com"},
            db_path=db_path,
        )

    assert OP_ID_PATTERN.match(op_id)


async def test_create_operation_auto_rejected_by_rule_restores_snapshot(db_path: Path) -> None:
    """Matching reject rule updates status and restores optimistic state."""
    from gateway.state_db import apply_optimistic_flag_update, get_message, get_or_create_folder, upsert_message
    import json

    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    await upsert_message(folder_id, 42, seq_num=1, flags=[], db_path=db_path)

    await apply_optimistic_flag_update(folder_id, "42", flags_add=[r"\Seen"], flags_remove=[], db_path=db_path)
    pre = await get_message(folder_id, 42, db_path=db_path)
    assert r"\Seen" in json.loads(pre["flags"])

    agent_id = await _seed_agent(db_path, user_id=1)
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO auto_approval_rules
                (user_id, enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (1, 1, 10, 'mark_read', '*@substack.com', 'INBOX', 'reject', 'Reject substack mark-read', 0)
            """
        )
        await db.commit()

    op_id = await create_operation(
        op_type="mark_read",
        protocol="imap",
        description="Mark as read",
        agent_id=agent_id,
        message_ids=["42"],
        folder_from="INBOX",
        smtp_envelope={"from": "digest@substack.com"},
        snapshot={"42": {"flags": [], "seq_num": 1, "folder_id": folder_id}},
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "rejected"
    assert row["decided_by"] == "auto_rule"

    post = await get_message(folder_id, 42, db_path=db_path)
    assert json.loads(post["flags"]) == []

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT event, actor FROM audit_log WHERE operation_id = ? ORDER BY id ASC",
            (op_id,),
        ) as cur:
            logs = await cur.fetchall()
    assert len(logs) == 2
    assert logs[1]["event"] == "rejected"
    assert logs[1]["actor"] == "auto_rule"
    # op_type must be populated in the auto-rule audit row (was NULL before fix)
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT op_type FROM audit_log WHERE operation_id = ? AND actor = 'auto_rule'",
            (op_id,),
        ) as cur:
            audit_row = await cur.fetchone()
    assert audit_row["op_type"] == "mark_read", (
        f"auto_rule audit row must carry op_type; got: {audit_row['op_type']!r}"
    )


async def test_create_operation_skips_push_for_auto_approved(db_path: Path) -> None:
    """Auto-decided operations should not trigger staged push notifications."""
    agent_id = await _seed_agent(db_path, user_id=1)
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO auto_approval_rules
                (user_id, enabled, priority, op_type, sender_pattern, folder_from, action, description, created_at)
            VALUES (1, 1, 10, 'mark_read', '*@substack.com', NULL, 'approve', 'Substack mark-read', 0)
            """
        )
        await db.commit()

    with patch("gateway.push.notify_staged") as notify_mock, \
         patch("asyncio.create_task") as task_mock, \
         patch("gateway.execution.execute_operation", new=AsyncMock(return_value={"executed_at": 1})):
        await create_operation(
            op_type="mark_read",
            protocol="imap",
            description="Mark as read",
            agent_id=agent_id,
            smtp_envelope={"from": "digest@substack.com"},
            db_path=db_path,
        )
        notify_mock.assert_not_called()
        task_mock.assert_not_called()
