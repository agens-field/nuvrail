"""
Unit tests for the staging engine (Milestone 1.0).

Uses a tmp_path-isolated SQLite DB so tests never touch ~/.nuvrail.
"""
import re
from pathlib import Path

import pytest

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


async def test_create_operation_persists_intent(db_path: Path) -> None:
    """intent_label / intent_confidence are stored on the staged row."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Archive: 1",
        message_ids=["1"],
        folder_from="INBOX",
        folder_to="[Gmail]/All Mail",
        intent_label="archive",
        intent_confidence=1.0,
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row["intent_label"] == "archive"
    assert row["intent_confidence"] == 1.0


async def test_staged_audit_row_carries_intent(db_path: Path) -> None:
    """The 'staged' audit_log entry is denormalized with the intent label."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Archive: 1",
        intent_label="archive",
        intent_confidence=1.0,
        db_path=db_path,
    )
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT intent_label FROM audit_log WHERE operation_id = ? AND event = 'staged'",
            (op_id,),
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    assert row["intent_label"] == "archive"


async def test_delete_intent_defaults_urgent(db_path: Path) -> None:
    """A MOVE with delete intent is urgent, same as a STORE \\Deleted trash op."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move to Trash: 1",
        folder_to="[Gmail]/Trash",
        intent_label="delete",
        intent_confidence=1.0,
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row["is_urgent"] == 1


async def test_archive_intent_not_urgent(db_path: Path) -> None:
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Archive: 1",
        folder_to="[Gmail]/All Mail",
        intent_label="archive",
        intent_confidence=1.0,
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row["is_urgent"] == 0


async def test_no_intent_defaults_null(db_path: Path) -> None:
    op_id = await create_operation(
        op_type="mark_read",
        protocol="imap",
        description="Mark as read: 1",
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)
    assert row["intent_label"] is None
    assert row["intent_confidence"] is None


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


# NOTE: auto-approval staging tests (a matching rule executing/rejecting an
# operation, execution-failure non-fatality, and push suppression) moved to the
# nuvrail-enterprise package, since the auto-decision provider now lives there.
# Open core stages every operation as pending.
