"""
Unit tests for gateway/expiry.py — operation expiry logic.

All tests use tmp_path-isolated SQLite DBs.
NUVRAIL_EXPIRY_HOURS is effectively overridden by manipulating expires_at
directly in the DB so tests don't need to wait real time.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from gateway.expiry import (
    expire_stale_operations,
    find_expired_pending,
)
from gateway.staging import create_operation, get_operation
from gateway.state_db import (
    get_db,
    get_or_create_folder,
    get_pending_reverts,
    init_db,
    upsert_message,
)


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    await init_db(path)
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_expired_op(db_path: Path, op_type: str = "mark_read") -> str:
    """Create a pending op whose expires_at is already in the past."""
    op_id = await create_operation(
        op_type=op_type,
        protocol="imap",
        description=f"Test op ({op_type})",
        db_path=db_path,
    )
    # Backdate expires_at to 1 second in the past
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET expires_at = ? WHERE id = ?",
            (int(time.time()) - 1, op_id),
        )
        await db.commit()
    return op_id


async def _make_live_op(db_path: Path) -> str:
    """Create a pending op that is NOT yet expired (expires in 48h)."""
    return await create_operation(
        op_type="move",
        protocol="imap",
        description="Live op",
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# find_expired_pending
# ---------------------------------------------------------------------------


async def test_find_expired_pending_returns_overdue(db_path: Path) -> None:
    """find_expired_pending returns ops whose expires_at has passed."""
    op_id = await _make_expired_op(db_path)
    found = await find_expired_pending(db_path=db_path)
    ids = [r["id"] for r in found]
    assert op_id in ids


async def test_find_expired_pending_excludes_live(db_path: Path) -> None:
    """find_expired_pending does not return ops that haven't expired yet."""
    live_id = await _make_live_op(db_path)
    found = await find_expired_pending(db_path=db_path)
    ids = [r["id"] for r in found]
    assert live_id not in ids


async def test_find_expired_pending_excludes_non_pending(db_path: Path) -> None:
    """Already-decided ops (rejected, executed) are not returned."""
    op_id = await _make_expired_op(db_path)
    # Manually mark as rejected
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET status = 'rejected' WHERE id = ?", (op_id,)
        )
        await db.commit()
    found = await find_expired_pending(db_path=db_path)
    assert not any(r["id"] == op_id for r in found)


# ---------------------------------------------------------------------------
# expire_stale_operations
# ---------------------------------------------------------------------------


async def test_expire_stale_sets_status_expired(db_path: Path) -> None:
    """expire_stale_operations sets status='expired' on overdue ops."""
    op_id = await _make_expired_op(db_path)
    count = await expire_stale_operations(db_path=db_path)
    assert count >= 1

    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "expired"


async def test_expire_stale_writes_audit_entry(db_path: Path) -> None:
    """expire_stale_operations inserts an audit_log entry with event='expired'."""
    op_id = await _make_expired_op(db_path)
    await expire_stale_operations(db_path=db_path)

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT event, actor FROM audit_log WHERE operation_id = ? AND event = 'expired'",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row["event"] == "expired"
    assert row["actor"] == "system"


async def test_expire_stale_sets_decided_at(db_path: Path) -> None:
    """expire_stale_operations populates decided_at on expired ops."""
    op_id = await _make_expired_op(db_path)
    before = int(time.time())
    await expire_stale_operations(db_path=db_path)
    after = int(time.time())

    row = await get_operation(op_id, db_path=db_path)
    assert row is not None
    assert row["decided_at"] is not None
    assert before <= row["decided_at"] <= after
    assert row["decided_by"] == "system"


async def test_expire_stale_reverts_snapshot(db_path: Path) -> None:
    """expire_stale_operations restores flags and queues pending_reverts when snapshot exists."""

    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    await upsert_message(folder_id, 42, seq_num=1, flags=[], db_path=db_path)

    # Create op with snapshot capturing pre-op state (empty flags)
    snap = {"42": {"flags": [], "seq_num": 1, "folder_id": folder_id}}
    op_id = await create_operation(
        op_type="mark_read",
        protocol="imap",
        description="Mark 42 as read",
        snapshot=snap,
        db_path=db_path,
    )
    # Backdate expires_at
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET expires_at = ? WHERE id = ?",
            (int(time.time()) - 1, op_id),
        )
        await db.commit()

    await expire_stale_operations(db_path=db_path)

    # pending_reverts should be queued for this folder
    reverts = await get_pending_reverts(folder_id, db_path=db_path)
    assert any(r["uid"] == 42 for r in reverts), (
        "Expected a pending_revert for uid=42 after expiry"
    )


async def test_expire_stale_does_not_touch_live_ops(db_path: Path) -> None:
    """expire_stale_operations leaves live (non-expired) ops untouched."""
    live_id = await _make_live_op(db_path)
    await expire_stale_operations(db_path=db_path)

    row = await get_operation(live_id, db_path=db_path)
    assert row is not None
    assert row["status"] == "pending"


async def test_expire_stale_returns_count(db_path: Path) -> None:
    """expire_stale_operations returns the number of ops expired."""
    await _make_expired_op(db_path)
    await _make_expired_op(db_path)
    await _make_live_op(db_path)

    count = await expire_stale_operations(db_path=db_path)
    assert count == 2


async def test_expire_stale_idempotent(db_path: Path) -> None:
    """Calling expire_stale_operations twice doesn't double-expire the same op."""
    await _make_expired_op(db_path)
    count1 = await expire_stale_operations(db_path=db_path)
    count2 = await expire_stale_operations(db_path=db_path)
    assert count1 >= 1
    assert count2 == 0  # already expired, status no longer 'pending'


async def test_expire_stale_no_ops_returns_zero(db_path: Path) -> None:
    """expire_stale_operations returns 0 when nothing is expired."""
    count = await expire_stale_operations(db_path=db_path)
    assert count == 0
