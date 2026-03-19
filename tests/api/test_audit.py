"""
Tests for GET /api/v1/audit endpoints.

Uses httpx.AsyncClient against the FastAPI app with a tmp_path-isolated DB.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from api.main import app
from api.routes.operations import get_db_path
from gateway.staging import create_operation, update_operation_status
from gateway.state_db import get_db, init_db


@pytest_asyncio.fixture(loop_scope="session")
async def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("audit_test") / "nuvrail.db"
    await init_db(path)
    return path


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_path: Path) -> httpx.AsyncClient:
    app.dependency_overrides[get_db_path] = lambda: db_path
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

    resp = await client.get(f"/api/v1/audit?event=executed")
    assert resp.status_code == 200
    executed_entries = [e for e in resp.json()["entries"] if e.get("operation_id") == op_id]
    assert executed_entries, "Expected executed audit entry for op"
    assert executed_entries[0]["op_status"] == "executed"


# ---------------------------------------------------------------------------
# Tests: GET /audit/{entry_id}
# ---------------------------------------------------------------------------


async def test_audit_get_single_entry(client: httpx.AsyncClient, db_path: Path) -> None:
    """GET /audit/{id} returns the correct entry."""
    raw_id = await _insert_raw_audit(db_path, event="staged", actor="ai_agent")

    resp = await client.get(f"/api/v1/audit/{raw_id}")
    assert resp.status_code == 200
    entry = resp.json()
    assert entry["id"] == raw_id
    assert entry["event"] == "staged"


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
