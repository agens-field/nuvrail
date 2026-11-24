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
    async with get_db(db_path) as db, db.execute(
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
    async with get_db(db_path) as db, db.execute(
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



# ---------------------------------------------------------------------------
# Pending-op dedup (nuvrail-internal#45)
#
#   stage MOVE 9 -> Archive        -> op A (pending)
#   stage MOVE 9 -> Archive again  -> op A again, 'deduplicated' audit row
#   anything that differs (UIDs, folder, flags, agent, op_type) -> new op
#   op A decided or expired, then the same request -> new op
#   smtp_send / append / no message_ids -> never deduplicated
# ---------------------------------------------------------------------------


async def _seed_agents(db_path: Path, count: int = 2) -> list[int]:
    ids: list[int] = []
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO users (id, email, display_name, hashed_password, created_at) "
            "VALUES (1, 'dedup@example.com', 'T', 'x', 0)"
        )
        for n in range(count):
            cur = await db.execute(
                "INSERT INTO agent_credentials (user_id, label, agent_username, "
                "hashed_token, upstream_host, upstream_imap_port, upstream_smtp_port, "
                "upstream_user, upstream_password, created_at) "
                "VALUES (1, ?, ?, 'x', 'imap.example.com', 993, 587, 'u@example.com', 'x', 0)",
                (f"a{n}", f"nuvrail_dedup_{n}"),
            )
            ids.append(int(cur.lastrowid))
        await db.commit()
    return ids


async def _stage_move(db_path: Path, **overrides) -> str:
    kwargs = {
        "op_type": "move",
        "protocol": "imap",
        "description": "Move 9 to Archive",
        "message_ids": ["9"],
        "folder_from": "INBOX",
        "folder_to": "Archive",
        "db_path": db_path,
    }
    kwargs.update(overrides)
    return await create_operation(**kwargs)


async def _count_rows(db_path: Path) -> int:
    async with get_db(db_path) as db, db.execute(
        "SELECT COUNT(*) FROM staged_operations"
    ) as cur:
        return (await cur.fetchone())[0]


async def test_dedup_same_move_twice_returns_same_op(db_path: Path) -> None:
    (agent,) = await _seed_agents(db_path, 1)
    first = await _stage_move(db_path, agent_id=agent)
    second = await _stage_move(db_path, agent_id=agent, description="again")
    assert second == first
    assert await _count_rows(db_path) == 1
    async with get_db(db_path) as db, db.execute(
        "SELECT event, actor, detail FROM audit_log WHERE operation_id = ? ORDER BY id",
        (first,),
    ) as cur:
        events = [dict(r) for r in await cur.fetchall()]
    assert [e["event"] for e in events] == ["staged", "deduplicated"]
    assert events[1]["actor"] == "ai_agent"
    assert "again" in events[1]["detail"]
    # The original request text is kept; the duplicate does not overwrite it.
    assert (await get_operation(first, db_path=db_path))["description"] == "Move 9 to Archive"


async def test_dedup_ignores_uid_order_and_type(db_path: Path) -> None:
    (agent,) = await _seed_agents(db_path, 1)
    first = await _stage_move(db_path, agent_id=agent, message_ids=["9", "3"])
    second = await _stage_move(db_path, agent_id=agent, message_ids=[3, 9])
    assert second == first


async def test_dedup_without_agent_matches_null_agent(db_path: Path) -> None:
    first = await _stage_move(db_path)
    assert await _stage_move(db_path) == first


@pytest.mark.parametrize(
    "override",
    [
        {"message_ids": ["10"]},
        {"message_ids": ["9", "10"]},
        {"folder_to": "Trash"},
        {"folder_from": "Sent"},
        {"op_type": "copy"},
        {"flags_add": ["\\Seen"]},
    ],
)
async def test_dedup_different_request_creates_new_op(db_path: Path, override: dict) -> None:
    (agent,) = await _seed_agents(db_path, 1)
    first = await _stage_move(db_path, agent_id=agent)
    second = await _stage_move(db_path, agent_id=agent, **override)
    assert second != first
    assert await _count_rows(db_path) == 2


async def test_dedup_different_flags_on_same_message_creates_new_op(db_path: Path) -> None:
    (agent,) = await _seed_agents(db_path, 1)
    common = {"op_type": "store", "folder_to": None, "agent_id": agent}
    seen = await _stage_move(db_path, flags_add=["\\Seen"], **common)
    flagged = await _stage_move(db_path, flags_add=["\\Flagged"], **common)
    unseen = await _stage_move(db_path, flags_remove=["\\Seen"], **common)
    assert len({seen, flagged, unseen}) == 3
    assert await _stage_move(db_path, flags_add=["\\Seen"], **common) == seen


async def test_dedup_is_per_agent(db_path: Path) -> None:
    a, b = await _seed_agents(db_path, 2)
    assert await _stage_move(db_path, agent_id=a) != await _stage_move(db_path, agent_id=b)


@pytest.mark.parametrize("status", ["approved", "executed", "rejected", "failed", "expired"])
async def test_dedup_after_first_is_decided_creates_new_op(db_path: Path, status: str) -> None:
    (agent,) = await _seed_agents(db_path, 1)
    first = await _stage_move(db_path, agent_id=agent)
    await update_operation_status(first, status, db_path=db_path)
    second = await _stage_move(db_path, agent_id=agent)
    assert second != first
    assert (await get_operation(second, db_path=db_path))["status"] == "pending"


async def test_dedup_skips_pending_row_past_expiry(db_path: Path) -> None:
    """A pending row the expiry sweep has not reached yet is not reused."""
    (agent,) = await _seed_agents(db_path, 1)
    first = await _stage_move(db_path, agent_id=agent)
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET expires_at = 1 WHERE id = ?", (first,)
        )
        await db.commit()
    assert await _stage_move(db_path, agent_id=agent) != first


@pytest.mark.parametrize(
    "kwargs",
    [
        {"op_type": "smtp_send", "protocol": "smtp", "message_ids": ["9"]},
        {"op_type": "append", "message_ids": ["9"]},
        {"message_ids": None},
        {"message_ids": []},
    ],
)
async def test_dedup_never_applies(db_path: Path, kwargs: dict) -> None:
    first = await _stage_move(db_path, **kwargs)
    second = await _stage_move(db_path, **kwargs)
    assert first != second
    assert await _count_rows(db_path) == 2


async def test_dedup_does_not_rerun_auto_decision(db_path: Path, monkeypatch) -> None:
    """The duplicate must not re-evaluate auto-rules (the first call did)."""
    calls: list[str] = []

    async def _fake_decision(op, db_path=None, user_id=None):
        calls.append(op["id"])
        return None

    monkeypatch.setattr("gateway.staging.run_auto_decision", _fake_decision)
    (agent,) = await _seed_agents(db_path, 1)
    first = await _stage_move(db_path, agent_id=agent)
    assert await _stage_move(db_path, agent_id=agent) == first
    assert calls == [first]


async def test_dedup_concurrent_stagings_create_one_op(db_path: Path) -> None:
    import asyncio

    (agent,) = await _seed_agents(db_path, 1)
    ids = await asyncio.gather(*(_stage_move(db_path, agent_id=agent) for _ in range(5)))
    assert len(set(ids)) == 1
    assert await _count_rows(db_path) == 1

