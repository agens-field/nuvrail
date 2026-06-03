"""
Unit tests for gateway/scheduler.py — cool-down (approve_after) execution.

A scheduled op is a pending op stamped with scheduled_execute_at. The scheduler
executes it once that deadline passes, claiming it atomically first so a manual
approve/reject can never double-execute. execute_operation is patched so no real
upstream connection is made.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import gateway.execution  # noqa: F401  (make the patch target importable)
from gateway.scheduler import execute_due_scheduled, find_due_scheduled
from gateway.staging import create_operation, get_operation, update_operation_status
from gateway.state_db import get_db, init_db


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "scheduler.db"
    await init_db(path)
    return path


async def _stage_scheduled(db_path: Path, *, scheduled_execute_at: int) -> str:
    """Stage a pending op and stamp it with a cool-down deadline."""
    op_id = await create_operation(
        op_type="smtp_send", protocol="smtp", description="Deferred send",
        smtp_envelope={"from": "me@x.com", "to": "out@external.com"}, db_path=db_path,
    )
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET scheduled_execute_at = ? WHERE id = ?",
            (scheduled_execute_at, op_id),
        )
        await db.commit()
    return op_id


def _fake_execute():
    async def _exec(op_id, row, db_path, *, actor="human"):
        await update_operation_status(op_id, "executed", decided_by=actor, db_path=db_path)
        return {"executed_at": 1}
    return AsyncMock(side_effect=_exec)


async def test_find_due_returns_only_past_deadline(db_path: Path) -> None:
    now = int(time.time())
    due = await _stage_scheduled(db_path, scheduled_execute_at=now - 10)
    not_due = await _stage_scheduled(db_path, scheduled_execute_at=now + 3600)
    ids = {op["id"] for op in await find_due_scheduled(db_path=db_path)}
    assert due in ids
    assert not_due not in ids


async def test_due_op_is_executed(db_path: Path) -> None:
    now = int(time.time())
    op_id = await _stage_scheduled(db_path, scheduled_execute_at=now - 1)
    with patch("gateway.execution.execute_operation", new=_fake_execute()) as mock_exec:
        count = await execute_due_scheduled(db_path=db_path)
    assert count == 1
    mock_exec.assert_awaited_once()
    assert mock_exec.call_args.kwargs.get("actor") == "auto_rule"
    row = await get_operation(op_id, db_path=db_path)
    assert row["status"] == "executed"


async def test_future_op_is_not_executed(db_path: Path) -> None:
    now = int(time.time())
    op_id = await _stage_scheduled(db_path, scheduled_execute_at=now + 3600)
    with patch("gateway.execution.execute_operation", new=_fake_execute()) as mock_exec:
        count = await execute_due_scheduled(db_path=db_path)
    assert count == 0
    mock_exec.assert_not_awaited()
    row = await get_operation(op_id, db_path=db_path)
    assert row["status"] == "pending"


async def test_already_decided_op_is_skipped(db_path: Path) -> None:
    """A human who rejected during the window wins — the loop must not run it."""
    now = int(time.time())
    op_id = await _stage_scheduled(db_path, scheduled_execute_at=now - 1)
    await update_operation_status(op_id, "rejected", db_path=db_path)
    with patch("gateway.execution.execute_operation", new=_fake_execute()) as mock_exec:
        count = await execute_due_scheduled(db_path=db_path)
    assert count == 0
    mock_exec.assert_not_awaited()
    row = await get_operation(op_id, db_path=db_path)
    assert row["status"] == "rejected"


async def test_unscheduled_pending_op_is_ignored(db_path: Path) -> None:
    """Ops with no scheduled_execute_at (the common case) are never touched."""
    op_id = await create_operation(
        op_type="move", protocol="imap", description="plain pending",
        folder_from="INBOX", folder_to="Archive", db_path=db_path,
    )
    with patch("gateway.execution.execute_operation", new=_fake_execute()) as mock_exec:
        count = await execute_due_scheduled(db_path=db_path)
    assert count == 0
    mock_exec.assert_not_awaited()
    row = await get_operation(op_id, db_path=db_path)
    assert row["status"] == "pending"
