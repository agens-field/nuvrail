"""
Tests for gateway/scrubber.py — body_preview scrubbing logic (issue #29).

All tests use tmp_path-isolated SQLite DBs.
NUVRAIL_BODY_SCRUB_DAYS is effectively bypassed by manipulating decided_at
directly in the DB so tests don't need to wait real time.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gateway.scrubber import find_scrubable_operations, scrub_expired_body_previews
from gateway.staging import create_operation
from gateway.state_db import get_db, init_db


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "scrubber_test.db"
    await init_db(path)
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENVELOPE = json.dumps({"from": "a@b.com", "to": ["c@d.com"], "subject": "Hi",
                        "body_preview": "Secret content", "body": "Full body text"})
_ENVELOPE_NO_PREVIEW = json.dumps({"from": "a@b.com", "to": ["c@d.com"], "subject": "Hi",
                                    "body_preview": None, "body": None})


async def _make_terminal_op(
    db_path: Path,
    status: str = "executed",
    smtp_envelope: str | None = _ENVELOPE,
    decided_at_offset: int = -8 * 86400,  # 8 days ago = past scrub window
) -> str:
    """Create a terminal op with a smtp_envelope for scrubbing tests."""
    op_id = await create_operation(
        op_type="smtp_send",
        protocol="smtp",
        description="Test smtp op",
        db_path=db_path,
    )
    now = int(time.time())
    async with get_db(db_path) as db:
        await db.execute(
            """
            UPDATE staged_operations
            SET status = ?, smtp_envelope = ?, decided_at = ?
            WHERE id = ?
            """,
            (status, smtp_envelope, now + decided_at_offset, op_id),
        )
        await db.commit()
    return op_id


# ---------------------------------------------------------------------------
# find_scrubable_operations
# ---------------------------------------------------------------------------


async def _make_terminal_append_op(
    db_path: Path,
    status: str = "executed",
    decided_at_offset: int = -8 * 86400,
) -> str:
    """Create a terminal APPEND op with a stored append_message for scrubbing tests."""
    op_id = await create_operation(
        op_type="append",
        protocol="imap",
        description="Test append op",
        folder_to="Sent",
        append_message="SGVsbG8gd29ybGQ=",  # base64 of "Hello world"
        db_path=db_path,
    )
    now = int(time.time())
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET status = ?, decided_at = ? WHERE id = ?",
            (status, now + decided_at_offset, op_id),
        )
        await db.commit()
    return op_id


async def test_find_returns_eligible_op(db_path: Path) -> None:
    """find_scrubable_operations returns terminal ops with old body_preview."""
    op_id = await _make_terminal_op(db_path)
    found = await find_scrubable_operations(db_path=db_path)
    assert any(r["id"] == op_id for r in found)


async def test_scrub_nulls_append_message(db_path: Path) -> None:
    """APPEND bodies (append_message) are scrubbed once terminal and past the window."""
    op_id = await _make_terminal_append_op(db_path)

    # Eligible even though smtp_envelope is NULL.
    found = await find_scrubable_operations(db_path=db_path)
    assert any(r["id"] == op_id for r in found)

    scrubbed = await scrub_expired_body_previews(db_path=db_path)
    assert scrubbed >= 1

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT append_message, body_scrubbed_at FROM staged_operations WHERE id = ?",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row["append_message"] is None
    assert row["body_scrubbed_at"] is not None


async def test_recent_append_op_not_scrubbed(db_path: Path) -> None:
    """A recently-decided APPEND op keeps its body until the window passes."""
    op_id = await _make_terminal_append_op(db_path, decided_at_offset=-60)  # 1 min ago
    found = await find_scrubable_operations(db_path=db_path)
    assert all(r["id"] != op_id for r in found)


async def test_find_excludes_recent_terminal(db_path: Path) -> None:
    """Ops that just reached terminal state (< 7 days ago) are not returned."""
    op_id = await _make_terminal_op(db_path, decided_at_offset=-1 * 86400)  # 1 day ago
    found = await find_scrubable_operations(db_path=db_path)
    assert not any(r["id"] == op_id for r in found)


async def test_find_excludes_pending(db_path: Path) -> None:
    """Pending ops are not scrubbed, even if old."""
    op_id = await create_operation(
        op_type="smtp_send",
        protocol="smtp",
        description="pending smtp",
        db_path=db_path,
    )
    # Force old created_at but keep status=pending
    now = int(time.time())
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET smtp_envelope = ?, created_at = ? WHERE id = ?",
            (_ENVELOPE, now - 10 * 86400, op_id),
        )
        await db.commit()
    found = await find_scrubable_operations(db_path=db_path)
    assert not any(r["id"] == op_id for r in found)


async def test_find_excludes_already_scrubbed(db_path: Path) -> None:
    """Ops with body_scrubbed_at already set are not returned again."""
    op_id = await _make_terminal_op(db_path)
    now = int(time.time())
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET body_scrubbed_at = ? WHERE id = ?",
            (now - 1, op_id),
        )
        await db.commit()
    found = await find_scrubable_operations(db_path=db_path)
    assert not any(r["id"] == op_id for r in found)


async def test_find_excludes_null_envelope(db_path: Path) -> None:
    """IMAP ops without smtp_envelope are not returned."""
    op_id = await _make_terminal_op(db_path, smtp_envelope=None)
    found = await find_scrubable_operations(db_path=db_path)
    assert not any(r["id"] == op_id for r in found)


# ---------------------------------------------------------------------------
# scrub_expired_body_previews
# ---------------------------------------------------------------------------


async def test_scrub_nulls_body_preview(db_path: Path) -> None:
    """Scrubbing sets body_preview to null in smtp_envelope."""
    op_id = await _make_terminal_op(db_path)
    count = await scrub_expired_body_previews(db_path=db_path)
    assert count == 1

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT smtp_envelope, body_scrubbed_at FROM staged_operations WHERE id = ?",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    envelope = json.loads(row["smtp_envelope"])
    assert envelope["body_preview"] is None
    assert envelope["body"] is None          # full RFC 2822 body scrubbed too
    # Metadata must be preserved
    assert envelope["from"] == "a@b.com"
    assert row["body_scrubbed_at"] is not None


async def test_scrub_writes_audit_event(db_path: Path) -> None:
    """Scrubbing writes a 'body_scrubbed' audit_log entry."""
    op_id = await _make_terminal_op(db_path)
    await scrub_expired_body_previews(db_path=db_path)

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT event, actor FROM audit_log WHERE operation_id = ? AND event = 'body_scrubbed'",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row["event"] == "body_scrubbed"
    assert row["actor"] == "system"


async def test_scrub_idempotent(db_path: Path) -> None:
    """Running scrubber twice does not double-scrub or double-audit."""
    op_id = await _make_terminal_op(db_path)
    count1 = await scrub_expired_body_previews(db_path=db_path)
    count2 = await scrub_expired_body_previews(db_path=db_path)
    assert count1 == 1
    assert count2 == 0  # nothing eligible on second run

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE operation_id = ? AND event = 'body_scrubbed'",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row["n"] == 1


async def test_scrub_returns_zero_when_nothing_eligible(db_path: Path) -> None:
    """scrub_expired_body_previews returns 0 when no ops are eligible."""
    count = await scrub_expired_body_previews(db_path=db_path)
    assert count == 0


async def test_scrub_all_terminal_statuses(db_path: Path) -> None:
    """All terminal statuses (approved, rejected, executed, failed, expired) are scrubbed."""
    statuses = ["approved", "rejected", "executed", "failed", "expired"]
    op_ids = []
    for s in statuses:
        op_id = await _make_terminal_op(db_path, status=s)
        op_ids.append(op_id)

    count = await scrub_expired_body_previews(db_path=db_path)
    assert count == len(statuses)

    async with get_db(db_path) as db:
        for op_id in op_ids:
            async with db.execute(
                "SELECT body_scrubbed_at FROM staged_operations WHERE id = ?",
                (op_id,),
            ) as cur:
                row = await cur.fetchone()
            assert row["body_scrubbed_at"] is not None
