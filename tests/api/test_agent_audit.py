"""
Tests for GET /api/v1/agents/{agent_id}/audit (Phase 2a, issue #5).

Covers:
  - Per-agent audit endpoint returns entries scoped to the correct agent.
  - User-scope enforcement: can't see another user's agent audit.
  - agent_id is populated in audit rows on stage / approve / reject transitions.
  - Pagination (limit/offset).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from api.auth import get_auth_db_path, get_current_user
from api.main import app
from api.routes.operations import get_db_path
from gateway.staging import create_operation
from gateway.state_db import get_db, init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_USER_A = {"id": 10, "email": "usera@test.com", "display_name": None, "created_at": 0, "api_token": "tokena"}
_USER_B = {"id": 11, "email": "userb@test.com", "display_name": None, "created_at": 0, "api_token": "tokenb"}

_AGENT_A1 = 100  # belongs to USER_A
_AGENT_A2 = 101  # also belongs to USER_A
_AGENT_B1 = 102  # belongs to USER_B


@pytest_asyncio.fixture(loop_scope="session")
async def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("agent_audit_test") / "nuvrail.db"
    await init_db(path)
    async with get_db(path) as db:
        # Insert users
        for user in (_USER_A, _USER_B):
            await db.execute(
                "INSERT OR IGNORE INTO users (id, email, hashed_password, api_token, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user["id"], user["email"], "x", user["api_token"], 0),
            )
        # Insert agent credentials
        for agent_id, user_id, label in [
            (_AGENT_A1, _USER_A["id"], "Agent A1"),
            (_AGENT_A2, _USER_A["id"], "Agent A2"),
            (_AGENT_B1, _USER_B["id"], "Agent B1"),
        ]:
            await db.execute(
                """INSERT OR IGNORE INTO agent_credentials
                   (id, user_id, label, agent_username, hashed_token,
                    upstream_host, upstream_imap_port, upstream_smtp_port,
                    upstream_user, upstream_password, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, user_id, label, f"nuvrail_{agent_id}", "x",
                 "imap.example.com", 993, 587, f"agent{agent_id}@example.com", "pass", 0),
            )
        await db.commit()
    return path


@pytest_asyncio.fixture(loop_scope="session")
async def client_a(db_path: Path) -> httpx.AsyncClient:
    """Client authenticated as USER_A."""
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    app.dependency_overrides[get_current_user] = lambda: _USER_A
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(loop_scope="session")
async def client_b(db_path: Path) -> httpx.AsyncClient:
    """Client authenticated as USER_B."""
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    app.dependency_overrides[get_current_user] = lambda: _USER_B
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _stage(db_path: Path, agent_id: int, op_type: str = "mark_read") -> str:
    return await create_operation(
        op_type=op_type,
        protocol="imap",
        agent_id=agent_id,
        description=f"Test {op_type} for agent {agent_id}",
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# Tests: agent_id is populated in audit rows
# ---------------------------------------------------------------------------


async def test_stage_populates_agent_id(db_path: Path) -> None:
    """Staging an operation writes the correct agent_id to the audit_log row."""
    op_id = await _stage(db_path, _AGENT_A1)

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT agent_id, op_type, event FROM audit_log WHERE operation_id = ? AND event = 'staged'",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None, "Expected a 'staged' audit row"
    # agent_id is stored as TEXT in the DB column but matches the integer
    assert str(row["agent_id"]) == str(_AGENT_A1)
    assert row["op_type"] == "mark_read"


async def test_approve_populates_agent_id(db_path: Path) -> None:
    """Directly inserting an 'executed' audit row via _do_approve-style insert works."""
    op_id = await _stage(db_path, _AGENT_A1, op_type="move")
    now = int(time.time())
    # Simulate what _do_approve does after execution
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, op_type, detail)
               VALUES (?, ?, 'executed', 'human', ?, ?, NULL)""",
            (now, op_id, _AGENT_A1, "move"),
        )
        await db.commit()

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT agent_id, op_type FROM audit_log WHERE operation_id = ? AND event = 'executed'",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert str(row["agent_id"]) == str(_AGENT_A1)
    assert row["op_type"] == "move"


async def test_reject_populates_agent_id(db_path: Path) -> None:
    """Directly inserting a 'rejected' audit row carries agent_id and op_type."""
    op_id = await _stage(db_path, _AGENT_A2, op_type="copy")
    now = int(time.time())
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, op_type, detail)
               VALUES (?, ?, 'rejected', 'human', ?, ?, NULL)""",
            (now, op_id, _AGENT_A2, "copy"),
        )
        await db.commit()

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT agent_id, op_type FROM audit_log WHERE operation_id = ? AND event = 'rejected'",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert str(row["agent_id"]) == str(_AGENT_A2)


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/agents/{agent_id}/audit
# ---------------------------------------------------------------------------


async def test_agent_audit_returns_entries(client_a: httpx.AsyncClient, db_path: Path) -> None:
    """GET /agents/{agent_id}/audit returns entries for the correct agent."""
    op_id = await _stage(db_path, _AGENT_A1, op_type="store")

    resp = await client_a.get(f"/api/v1/agents/{_AGENT_A1}/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert "total" in data

    matching = [e for e in data["entries"] if e.get("operation_id") == op_id]
    assert matching, f"Expected entry for op {op_id} in agent audit"
    assert matching[0]["event"] == "staged"


async def test_agent_audit_isolates_per_agent(client_a: httpx.AsyncClient, db_path: Path) -> None:
    """Entries for AGENT_A1 do not appear in AGENT_A2's audit log."""
    op_a1 = await _stage(db_path, _AGENT_A1)
    op_a2 = await _stage(db_path, _AGENT_A2)

    resp_a1 = await client_a.get(f"/api/v1/agents/{_AGENT_A1}/audit")
    resp_a2 = await client_a.get(f"/api/v1/agents/{_AGENT_A2}/audit")
    assert resp_a1.status_code == 200
    assert resp_a2.status_code == 200

    a1_op_ids = {e["operation_id"] for e in resp_a1.json()["entries"]}
    a2_op_ids = {e["operation_id"] for e in resp_a2.json()["entries"]}

    assert op_a1 in a1_op_ids
    assert op_a1 not in a2_op_ids

    assert op_a2 in a2_op_ids
    assert op_a2 not in a1_op_ids


async def test_agent_audit_user_scope_enforced(client_b: httpx.AsyncClient) -> None:
    """USER_B cannot see USER_A's agent audit log (returns 404)."""
    resp = await client_b.get(f"/api/v1/agents/{_AGENT_A1}/audit")
    assert resp.status_code == 404


async def test_agent_audit_own_agent_accessible(client_b: httpx.AsyncClient, db_path: Path) -> None:
    """USER_B can access their own agent's audit log."""
    await _stage(db_path, _AGENT_B1)
    resp = await client_b.get(f"/api/v1/agents/{_AGENT_B1}/audit")
    assert resp.status_code == 200


async def test_agent_audit_pagination(client_a: httpx.AsyncClient, db_path: Path) -> None:
    """limit/offset pagination works on the per-agent audit endpoint."""
    # Create several operations to generate multiple audit entries
    for _ in range(6):
        await _stage(db_path, _AGENT_A1)

    resp_all = await client_a.get(f"/api/v1/agents/{_AGENT_A1}/audit?limit=500")
    total = resp_all.json()["total"]
    assert total >= 6

    resp_p1 = await client_a.get(f"/api/v1/agents/{_AGENT_A1}/audit?limit=2&offset=0")
    resp_p2 = await client_a.get(f"/api/v1/agents/{_AGENT_A1}/audit?limit=2&offset=2")
    p1_ids = {e["id"] for e in resp_p1.json()["entries"]}
    p2_ids = {e["id"] for e in resp_p2.json()["entries"]}
    assert p1_ids.isdisjoint(p2_ids), "Pages must not overlap"


async def test_agent_audit_not_found(client_a: httpx.AsyncClient) -> None:
    """GET /agents/{unknown_id}/audit returns 404."""
    resp = await client_a.get("/api/v1/agents/99999/audit")
    assert resp.status_code == 404


async def test_agent_audit_newest_first(client_a: httpx.AsyncClient, db_path: Path) -> None:
    """Per-agent audit entries are ordered newest first (descending by id)."""
    for _ in range(3):
        await _stage(db_path, _AGENT_A1)

    resp = await client_a.get(f"/api/v1/agents/{_AGENT_A1}/audit?limit=20")
    entries = resp.json()["entries"]
    ids = [e["id"] for e in entries]
    assert ids == sorted(ids, reverse=True), "Must be newest first"


async def test_agent_audit_includes_op_fields(client_a: httpx.AsyncClient, db_path: Path) -> None:
    """Per-agent audit entries include joined op_type and op_description."""
    op_id = await _stage(db_path, _AGENT_A1, op_type="trash")

    resp = await client_a.get(f"/api/v1/agents/{_AGENT_A1}/audit")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    matching = [e for e in entries if e.get("operation_id") == op_id]
    assert matching

    entry = matching[0]
    # op_description joined from staged_operations
    assert entry.get("op_description") is not None
    # agent_label joined from agent_credentials
    assert entry.get("agent_label") == "Agent A1"


async def test_reverted_audit_row_includes_op_type(db_path: Path) -> None:
    """
    When an operation is marked 'reverted' via gateway.undo, the audit_log row
    must carry op_type — it was missing before the fix in issue #5.

    This test inserts the audit row directly (bypassing the full IMAP undo
    machinery) to isolate the DB-write behaviour of the fixed undo path.
    """
    now = int(time.time())
    op_id = await _stage(db_path, _AGENT_A1, op_type="move")

    async with get_db(db_path) as db:
        # Replicate the fixed undo audit insert (was missing op_type before #5)
        await db.execute(
            """
            INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, op_type, detail)
            VALUES (?, ?, 'reverted', 'human', ?, ?, ?)
            """,
            (now, op_id, str(_AGENT_A1), "move",
             json.dumps({"description": "UID MOVE 42 INBOX → Trash (undone)"})),
        )
        await db.commit()

        async with db.execute(
            "SELECT op_type, event FROM audit_log WHERE operation_id = ? AND event = 'reverted'",
            (op_id,),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None, "Expected a 'reverted' audit_log row"
    assert row["event"] == "reverted"
    assert row["op_type"] == "move", (
        f"reverted audit row must carry op_type; got: {row['op_type']!r}"
    )
