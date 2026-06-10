"""
Tests for GET /api/v1/audit endpoints.

Uses httpx.AsyncClient against the FastAPI app with a tmp_path-isolated DB.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from api.auth import get_auth_db_path, get_current_user
from api.main import app
from api.routes.operations import get_db_path
from gateway.staging import create_operation, update_operation_status
from gateway.state_db import get_db, init_db

_FAKE_USER = {"id": 1, "email": "test@test.com", "display_name": None, "created_at": 0, "api_token": "testtoken"}
_FAKE_AGENT_ID = 1  # agent_credentials.id for the fake user


@pytest_asyncio.fixture(loop_scope="session")
async def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("audit_test") / "nuvrail.db"
    await init_db(path)
    # Insert the fake user and a fake agent credential so audit scoping works
    async with get_db(path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, email, hashed_password, api_token, created_at) VALUES (?, ?, ?, ?, ?)",
            (_FAKE_USER["id"], _FAKE_USER["email"], "x", _FAKE_USER["api_token"], 0),
        )
        await db.execute(
            """INSERT OR IGNORE INTO agent_credentials
               (id, user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_FAKE_AGENT_ID, _FAKE_USER["id"], "test", "nuvrail_test", "x",
             "imap.example.com", 993, 587, "test@example.com", "pass", 0),
        )
        await db.commit()
    return path


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_path: Path) -> httpx.AsyncClient:
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_op_with_audit(db_path: Path, op_type: str = "mark_read") -> str:
    """Create an operation and return its id (automatically inserts a 'staged' audit entry)."""
    return await create_operation(
        op_type=op_type,
        protocol="imap",
        agent_id=_FAKE_AGENT_ID,
        description=f"Test op ({op_type})",
        db_path=db_path,
    )


async def _insert_raw_audit(db_path: Path, event: str, actor: str = "system") -> int:
    """Insert a raw audit_log row without a linked operation."""
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO audit_log (timestamp, event, actor) VALUES (?, ?, ?)",
            (int(time.time()), event, actor),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests: GET /audit
# ---------------------------------------------------------------------------


async def test_audit_list_empty_returns_empty(client: httpx.AsyncClient) -> None:
    """Fresh DB returns empty entries list."""
    resp = await client.get("/api/v1/audit?limit=1&offset=9999999")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert "total" in data
    assert isinstance(data["entries"], list)


async def test_audit_intent_label_exposed_and_filterable(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """intent_label flows into audit entries and ?intent= filters on it."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        agent_id=_FAKE_AGENT_ID,
        description="Archive: 42",
        folder_to="[Gmail]/All Mail",
        intent_label="archive",
        intent_confidence=1.0,
        db_path=db_path,
    )

    resp = await client.get("/api/v1/audit?intent=archive")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    matching = [e for e in entries if e.get("operation_id") == op_id]
    assert matching, "Archive-intent entry should match ?intent=archive"
    assert matching[0]["intent_label"] == "archive"

    resp = await client.get("/api/v1/audit?intent=delete")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert not [e for e in entries if e.get("operation_id") == op_id], (
        "Archive-intent entry must not match ?intent=delete"
    )


async def test_audit_list_returns_staged_entry(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Creating an operation produces a 'staged' audit entry visible via GET /audit."""
    op_id = await _create_op_with_audit(db_path)

    resp = await client.get("/api/v1/audit")
    assert resp.status_code == 200
    data = resp.json()
    entries = data["entries"]
    assert len(entries) > 0

    # Find the entry for this op
    matching = [e for e in entries if e.get("operation_id") == op_id]
    assert matching, f"No audit entry found for op_id={op_id}"
    entry = matching[0]
    assert entry["event"] == "staged"
    assert entry["actor"] == "ai_agent"
    assert entry["op_description"] is not None  # joined from staged_operations


async def test_audit_list_filter_by_event(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """?event=staged returns only staged entries."""
    await _create_op_with_audit(db_path)

    resp = await client.get("/api/v1/audit?event=staged")
    assert resp.status_code == 200
    data = resp.json()
    assert all(e["event"] == "staged" for e in data["entries"])


async def test_audit_list_filter_by_actor(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """?actor=system returns only system-actor entries."""
    await _insert_raw_audit(db_path, event="custom", actor="system")

    resp = await client.get("/api/v1/audit?actor=system")
    assert resp.status_code == 200
    data = resp.json()
    assert all(e["actor"] == "system" for e in data["entries"])


async def test_audit_list_filter_by_agent_id_and_label(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """?agent_id filters entries and returns joined agent label."""
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "Agent Two", "nuvrail_agent_2", "x", "imap.example.com", 993, 587, "a2@example.com", "pass", 0),
        )
        await db.commit()
        agent_two = cur.lastrowid

    await _create_op_with_audit(db_path)
    op_two = await create_operation(
        op_type="move",
        protocol="imap",
        agent_id=agent_two,
        description="Agent two op",
        db_path=db_path,
    )

    resp = await client.get(f"/api/v1/audit?agent_id={agent_two}")
    assert resp.status_code == 200
    data = resp.json()
    matching = [e for e in data["entries"] if e.get("operation_id") == op_two]
    assert matching
    assert all(e.get("agent_id") == str(agent_two) for e in matching)
    assert all(e.get("agent_label") == "Agent Two" for e in matching)


async def test_audit_list_pagination(client: httpx.AsyncClient, db_path: Path) -> None:
    """limit/offset pagination works correctly."""
    # Create several ops to generate audit entries
    for _ in range(5):
        await _create_op_with_audit(db_path)

    resp_all = await client.get("/api/v1/audit?limit=500")
    total = resp_all.json()["total"]
    assert total >= 5

    resp_page1 = await client.get("/api/v1/audit?limit=2&offset=0")
    resp_page2 = await client.get("/api/v1/audit?limit=2&offset=2")
    page1_ids = {e["id"] for e in resp_page1.json()["entries"]}
    page2_ids = {e["id"] for e in resp_page2.json()["entries"]}
    assert page1_ids.isdisjoint(page2_ids), "Pages must not overlap"


async def test_audit_list_newest_first(client: httpx.AsyncClient, db_path: Path) -> None:
    """Entries are ordered newest first (descending by id)."""
    for _ in range(3):
        await _create_op_with_audit(db_path)

    resp = await client.get("/api/v1/audit?limit=10")
    entries = resp.json()["entries"]
    ids = [e["id"] for e in entries]
    assert ids == sorted(ids, reverse=True), "Entries must be newest first"


async def test_audit_list_joined_fields_after_approve(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """After approval, the audit entry shows the updated op_status."""
    op_id = await _create_op_with_audit(db_path, op_type="smtp_send")
    await update_operation_status(op_id, "executed", db_path=db_path)
    # Insert the executed audit entry manually (normally done by approve_op)
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO audit_log (timestamp, operation_id, event, actor) VALUES (?, ?, 'executed', 'human')",
            (int(time.time()), op_id),
        )
        await db.commit()

    resp = await client.get("/api/v1/audit?event=executed")
    assert resp.status_code == 200
    executed_entries = [e for e in resp.json()["entries"] if e.get("operation_id") == op_id]
    assert executed_entries, "Expected executed audit entry for op"
    assert executed_entries[0]["op_status"] == "executed"


# ---------------------------------------------------------------------------
# Tests: GET /audit/{entry_id}
# ---------------------------------------------------------------------------


async def test_audit_get_single_entry(client: httpx.AsyncClient, db_path: Path) -> None:
    """GET /audit/{id} returns the correct entry (scoped to current user)."""
    # Create an operation so there's a user-scoped audit entry to look up
    await _create_op_with_audit(db_path)
    # Get the latest entry id via the list endpoint
    list_resp = await client.get("/api/v1/audit?limit=1")
    assert list_resp.status_code == 200
    entries = list_resp.json()["entries"]
    assert entries, "Expected at least one audit entry"
    entry_id = entries[0]["id"]

    resp = await client.get(f"/api/v1/audit/{entry_id}")
    assert resp.status_code == 200
    entry = resp.json()
    assert entry["id"] == entry_id


async def test_audit_get_not_found(client: httpx.AsyncClient) -> None:
    """GET /audit/{id} returns 404 for unknown entry."""
    resp = await client.get("/api/v1/audit/999999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /audit/export
# ---------------------------------------------------------------------------


async def test_audit_export_returns_json(client: httpx.AsyncClient) -> None:
    """GET /audit/export returns valid JSON with all entries."""
    resp = await client.get("/api/v1/audit/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "audit_log" in data
    assert "total" in data
    assert isinstance(data["audit_log"], list)
    assert data["total"] == len(data["audit_log"])


async def test_audit_export_content_disposition(client: httpx.AsyncClient) -> None:
    """Export response includes Content-Disposition attachment header."""
    resp = await client.get("/api/v1/audit/export")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "nuvrail-audit.json" in cd


async def test_audit_export_respects_agent_filter(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Export endpoint accepts ?agent_id and returns only matching agent entries."""
    await _create_op_with_audit(db_path)  # fake agent 1

    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "Agent Three", "nuvrail_agent_3", "x", "imap.example.com", 993, 587, "a3@example.com", "pass", 0),
        )
        await db.commit()
        agent_three = cur.lastrowid

    op_three = await create_operation(
        op_type="copy",
        protocol="imap",
        agent_id=agent_three,
        description="Agent three op",
        db_path=db_path,
    )

    resp = await client.get(f"/api/v1/audit/export?agent_id={agent_three}")
    assert resp.status_code == 200
    entries = resp.json()["audit_log"]
    matching = [e for e in entries if e.get("operation_id") == op_three]
    assert matching
    assert all(e.get("agent_id") == str(agent_three) for e in matching)


async def test_audit_entry_includes_undo_expires_at(client: httpx.AsyncClient, db_path: Path) -> None:
    """GET /audit entries include undo_expires_at from staged_operations when present."""
    import time as _time
    from gateway.state_db import get_db as _get_db

    now = int(_time.time())
    undo_exp = now + 86400  # 24h window

    async with _get_db(db_path) as db:
        await db.execute(
            """INSERT INTO staged_operations
               (id, op_type, protocol, description, status, agent_id, created_at, expires_at,
                executed_at, undo_expires_at)
               VALUES ('op_undo_test', 'move', 'imap', 'UID MOVE 5 Archive',
                       'executed', ?, ?, ?, ?, ?)""",
            (_FAKE_AGENT_ID, now, now + 172800, now, undo_exp),
        )
        await db.execute(
            """INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, op_type)
               VALUES (?, 'op_undo_test', 'executed', 'human', ?, 'move')""",
            (now, _FAKE_AGENT_ID),
        )
        await db.commit()

    resp = await client.get("/api/v1/audit")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    matching = [e for e in entries if e.get("operation_id") == "op_undo_test"]
    assert matching, "Expected audit entry for op_undo_test"
    entry = matching[0]
    assert entry.get("undo_expires_at") == undo_exp, (
        f"Expected undo_expires_at={undo_exp}; got {entry.get('undo_expires_at')}"
    )
    assert entry.get("op_status") == "executed"


async def test_audit_filter_by_op_type(client: httpx.AsyncClient, db_path: Path) -> None:
    """?op_type filters entries to only those with a matching operation type."""
    import time as _time
    from gateway.state_db import get_db as _get_db

    now = int(_time.time())
    async with _get_db(db_path) as db:
        for ot in ('move', 'trash'):
            op_id = f'op_type_filter_{ot}'
            await db.execute(
                """INSERT OR IGNORE INTO staged_operations
                   (id, op_type, protocol, description, status, agent_id, created_at, expires_at)
                   VALUES (?, ?, 'imap', ?, 'pending', ?, ?, ?)""",
                (op_id, ot, f'test {ot}', _FAKE_AGENT_ID, now, now + 172800),
            )
            await db.execute(
                """INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, op_type)
                   VALUES (?, ?, 'staged', 'ai_agent', ?, ?)""",
                (now, op_id, _FAKE_AGENT_ID, ot),
            )
        await db.commit()

    resp = await client.get("/api/v1/audit?op_type=move")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert all(
        e.get("op_type") == "move" or e.get("op_type") is None
        for e in entries
        if e.get("operation_id", "").startswith("op_type_filter_")
    )
    op_types = {e["op_type"] for e in entries if e.get("operation_id", "").startswith("op_type_filter_")}
    assert "trash" not in op_types


async def test_audit_filter_by_since_until(client: httpx.AsyncClient, db_path: Path) -> None:
    """?since and ?until filter entries by timestamp."""
    import time as _time
    from gateway.state_db import get_db as _get_db

    base = int(_time.time())
    old_ts = base - 10000
    async with _get_db(db_path) as db:
        await db.execute(
            """INSERT INTO audit_log (timestamp, event, actor, agent_id)
               VALUES (?, 'staged', 'ai_agent', ?)""",
            (old_ts, _FAKE_AGENT_ID),
        )
        await db.commit()

    # since=now should exclude the old entry
    resp = await client.get(f"/api/v1/audit?since={base - 100}")
    assert resp.status_code == 200
    timestamps = [e["timestamp"] for e in resp.json()["entries"]]
    assert all(ts >= base - 100 for ts in timestamps), "since filter not working"

    # until=old_ts+1 should only return old entries
    resp = await client.get(f"/api/v1/audit?until={old_ts}")
    assert resp.status_code == 200
    timestamps2 = [e["timestamp"] for e in resp.json()["entries"]]
    assert all(ts <= old_ts for ts in timestamps2), "until filter not working"


async def test_audit_search_by_description(client: httpx.AsyncClient, db_path: Path) -> None:
    """?search filters entries by operation description substring."""
    import time as _time
    from gateway.state_db import get_db as _get_db

    now = int(_time.time())
    async with _get_db(db_path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO staged_operations
               (id, op_type, protocol, description, status, agent_id, created_at, expires_at)
               VALUES ('op_search_needle', 'move', 'imap', 'Move unique_needle_xyz to Archive',
                       'pending', ?, ?, ?)""",
            (_FAKE_AGENT_ID, now, now + 172800),
        )
        await db.execute(
            """INSERT INTO audit_log (timestamp, operation_id, event, actor, agent_id, op_type)
               VALUES (?, 'op_search_needle', 'staged', 'ai_agent', ?, 'move')""",
            (now, _FAKE_AGENT_ID),
        )
        await db.commit()

    resp = await client.get("/api/v1/audit?search=unique_needle_xyz")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert any(e.get("operation_id") == "op_search_needle" for e in entries), (
        "Expected search to return the needle entry"
    )

    resp2 = await client.get("/api/v1/audit?search=zzz_no_match_zzz")
    assert resp2.status_code == 200
    assert all(e.get("operation_id") != "op_search_needle" for e in resp2.json()["entries"])
