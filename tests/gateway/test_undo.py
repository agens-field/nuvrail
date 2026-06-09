"""
Tests for gateway/undo.py — reversing an executed operation.

Validation paths run without a network; the inverse IMAP replay is exercised
with a mocked client (no live server).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from gateway.credentials import encrypt_credential
from gateway.staging import create_operation
from gateway.state_db import get_db, init_db
from gateway.undo import UndoError, undo_operation


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "undo_test.db"
    await init_db(path)
    return path


async def _seed_agent(db_path: Path, *, oauth2: bool = False, with_password: bool = True) -> int:
    async with get_db(db_path) as db:
        # Seed the owning user (active) so the user-state JOIN in
        # get_agent_credential (issue #65) resolves on the execution path.
        await db.execute(
            """INSERT INTO users (id, email, display_name, hashed_password, created_at)
               VALUES (1, 'undo@example.com', 'T', 'x', 0)"""
        )
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, oauth2_provider, created_at)
               VALUES (1, 'l', 'nuvrail_u', 'x', 'imap.example.com', 993, 587,
                       'u@example.com', ?, ?, 0)""",
            (
                encrypt_credential("pw") if with_password else None,
                "google" if oauth2 else None,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def _seed_executed_op(
    db_path: Path, *, agent_id: int, op_type: str = "move",
    folder_from: str = "INBOX", folder_to: str = "Archive",
    message_ids: list | None = None, undo_offset: int = 3600,
) -> str:
    op_id = await create_operation(
        op_type=op_type,
        protocol="imap",
        description=f"{op_type} op",
        agent_id=agent_id,
        folder_from=folder_from,
        folder_to=folder_to,
        message_ids=message_ids if message_ids is not None else ["42"],
        db_path=db_path,
    )
    now = int(time.time())
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE staged_operations SET status = 'executed', undo_expires_at = ? WHERE id = ?",
            (now + undo_offset, op_id),
        )
        await db.commit()
    return op_id


class _FakeIMAP:
    """Records the IMAP commands undo issues; always replies OK."""

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple] = []

    async def wait_hello_from_server(self) -> None:
        return None

    async def login(self, user, password):
        self.calls.append(("login", user))
        return "OK", [b"ok"]

    async def select(self, folder):
        self.calls.append(("select", folder))
        return "OK", [b"ok"]

    async def uid(self, *args):
        self.calls.append(("uid", *args))
        return "OK", [b"ok"]

    async def logout(self):
        return "OK", [b"bye"]


# ---------------------------------------------------------------------------
# Inverse replay
# ---------------------------------------------------------------------------


async def test_undo_move_reverses_direction(db_path: Path) -> None:
    """Undoing a move issues UID MOVE back from folder_to to folder_from."""
    from unittest.mock import patch

    agent_id = await _seed_agent(db_path)
    op_id = await _seed_executed_op(
        db_path, agent_id=agent_id, op_type="move",
        folder_from="INBOX", folder_to="Archive", message_ids=["42"],
    )

    fake = _FakeIMAP()
    with patch("gateway.undo.aioimaplib.IMAP4_SSL", return_value=fake):
        result = await undo_operation(op_id, db_path)

    assert result["op_type"] == "move"
    assert ("select", "Archive") in fake.calls
    assert ("uid", "move", "42", "INBOX") in fake.calls

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT status FROM staged_operations WHERE id = ?", (op_id,)
        ) as cur:
            assert (await cur.fetchone())["status"] == "reverted"
        async with db.execute(
            "SELECT event, actor FROM audit_log WHERE operation_id = ? AND event = 'reverted'",
            (op_id,),
        ) as cur:
            audit = await cur.fetchone()
    assert audit is not None and audit["actor"] == "human"


# ---------------------------------------------------------------------------
# Validation (no network)
# ---------------------------------------------------------------------------


async def test_undo_unknown_op_raises(db_path: Path) -> None:
    with pytest.raises(UndoError, match="not found"):
        await undo_operation("op_missing", db_path)


async def test_undo_non_executed_raises(db_path: Path) -> None:
    agent_id = await _seed_agent(db_path)
    op_id = await create_operation(
        op_type="move", protocol="imap", description="m", agent_id=agent_id,
        folder_to="Archive", message_ids=["1"], db_path=db_path,
    )  # still 'pending'
    with pytest.raises(UndoError, match="cannot be undone"):
        await undo_operation(op_id, db_path)


async def test_undo_non_undoable_op_type_raises(db_path: Path) -> None:
    agent_id = await _seed_agent(db_path)
    op_id = await _seed_executed_op(db_path, agent_id=agent_id, op_type="copy")
    with pytest.raises(UndoError, match="not undoable"):
        await undo_operation(op_id, db_path)


async def test_undo_expired_window_raises(db_path: Path) -> None:
    agent_id = await _seed_agent(db_path)
    op_id = await _seed_executed_op(db_path, agent_id=agent_id, undo_offset=-60)
    with pytest.raises(UndoError, match="window has expired"):
        await undo_operation(op_id, db_path)


async def test_undo_oauth2_agent_raises(db_path: Path) -> None:
    """An OAuth2 agent (no stored password) cannot be undone yet."""
    agent_id = await _seed_agent(db_path, oauth2=True, with_password=False)
    op_id = await _seed_executed_op(db_path, agent_id=agent_id)
    with pytest.raises(UndoError, match="OAuth2 agents are not yet supported"):
        await undo_operation(op_id, db_path)
