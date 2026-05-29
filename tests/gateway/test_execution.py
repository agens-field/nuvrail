"""
Tests for gateway/execution.py — upstream replay of staged operations.

Focus: APPEND operations now carry the raw message body (base64 in
append_message) and are actually replayed upstream on approval, instead of
being silent no-ops. The upstream IMAP client is mocked — no network.
"""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway.credentials import encrypt_credential
from gateway.execution import _execute_imap_upstream
from gateway.staging import create_operation, get_operation
from gateway.state_db import get_db, init_db


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "exec_test.db"
    await init_db(path)
    return path


async def _seed_agent(db_path: Path) -> int:
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (1, 'test', 'nuvrail_exec', 'x', 'imap.example.com', 993, 587,
                       'u@example.com', ?, 0)""",
            (encrypt_credential("hunter2"),),
        )
        await db.commit()
        return int(cur.lastrowid)


class _FakeIMAP:
    """Minimal aioimaplib.IMAP4_SSL stand-in recording append() calls."""

    def __init__(self, *args, **kwargs) -> None:
        self.appends: list[tuple[bytes, object, object]] = []
        self.logged_in = False

    async def wait_hello_from_server(self) -> None:
        return None

    async def login(self, user: str, password: str):
        self.logged_in = True
        return "OK", [b"LOGIN completed"]

    async def append(self, message_bytes, mailbox=None, flags=None):
        self.appends.append((message_bytes, mailbox, flags))
        return "OK", [b"APPEND completed"]

    async def logout(self):
        return "OK", [b"BYE"]


async def test_append_op_replayed_upstream(db_path: Path) -> None:
    """An APPEND op with a stored body is appended to the target folder."""
    agent_id = await _seed_agent(db_path)
    raw = b"From: a@b.com\r\nSubject: hi\r\n\r\nbody"
    op_id = await create_operation(
        op_type="append",
        protocol="imap",
        description="Save to Sent",
        agent_id=agent_id,
        folder_to="Sent",
        flags_add=["\\Seen"],
        append_message=base64.b64encode(raw).decode("ascii"),
        db_path=db_path,
    )

    # Round-trip: the body is persisted.
    row = await get_operation(op_id, db_path=db_path)
    assert row["append_message"] == base64.b64encode(raw).decode("ascii")

    fake = _FakeIMAP()
    with patch("gateway.execution.aioimaplib.IMAP4_SSL", return_value=fake):
        await _execute_imap_upstream(row, db_path)

    assert len(fake.appends) == 1
    sent_bytes, mailbox, flags = fake.appends[0]
    assert sent_bytes == raw, "exact original bytes must be appended"
    assert "Sent" in str(mailbox)
    assert flags == "(\\Seen)"


async def test_append_op_without_body_is_skipped(db_path: Path) -> None:
    """A legacy APPEND op with no stored body is a no-op — no upstream connection."""
    agent_id = await _seed_agent(db_path)
    op_id = await create_operation(
        op_type="append",
        protocol="imap",
        description="Legacy append (no body)",
        agent_id=agent_id,
        folder_to="Sent",
        append_message=None,
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)

    with patch("gateway.execution.aioimaplib.IMAP4_SSL") as ctor:
        await _execute_imap_upstream(row, db_path)
        ctor.assert_not_called()


async def test_append_upstream_failure_raises(db_path: Path) -> None:
    """A non-OK APPEND response raises so the caller can mark the op failed."""
    agent_id = await _seed_agent(db_path)
    op_id = await create_operation(
        op_type="append",
        protocol="imap",
        description="Save to Sent",
        agent_id=agent_id,
        folder_to="Sent",
        append_message=base64.b64encode(b"raw").decode("ascii"),
        db_path=db_path,
    )
    row = await get_operation(op_id, db_path=db_path)

    class _FailingIMAP(_FakeIMAP):
        async def append(self, message_bytes, mailbox=None, flags=None):
            return "NO", [b"over quota"]

    with patch("gateway.execution.aioimaplib.IMAP4_SSL", return_value=_FailingIMAP()):
        with pytest.raises(RuntimeError, match="APPEND"):
            await _execute_imap_upstream(row, db_path)
