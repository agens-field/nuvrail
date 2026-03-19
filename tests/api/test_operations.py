"""
API endpoint tests for /api/v1/operations (Milestone 1.0).

Uses httpx.AsyncClient against the FastAPI app with a tmp_path-isolated DB
injected via FastAPI dependency overrides.

The test_approve_smtp_operation test sends a real email to testing@nuvrail.com
(the test mailbox — sending to itself is acceptable for integration verification).
"""
import os
from pathlib import Path

import httpx
import pytest

from api.main import app
from api.routes.operations import get_db_path
from gateway.staging import create_operation
from gateway.state_db import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    """Initialise an isolated test DB and return its path."""
    path = tmp_path / "test_api.db"
    await init_db(path)
    return path


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    """Return an AsyncClient wired to the app with the test DB injected."""
    app.dependency_overrides[get_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_operations_empty(client: httpx.AsyncClient) -> None:
    """GET /api/v1/operations returns empty list when no ops exist."""
    resp = await client.get("/api/v1/operations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["operations"] == []


async def test_list_operations_with_pending(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /api/v1/operations returns created pending ops."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move 42 to Archive",
        db_path=db_path,
    )
    resp = await client.get("/api/v1/operations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["operations"][0]["id"] == op_id
    assert data["operations"][0]["status"] == "pending"


async def test_list_operations_status_filter(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /api/v1/operations?status=pending filters correctly."""
    await create_operation(
        op_type="move",
        protocol="imap",
        description="Pending op",
        db_path=db_path,
    )
    resp = await client.get("/api/v1/operations?status=pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1

    resp2 = await client.get("/api/v1/operations?status=executed")
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 0


async def test_get_operation_detail(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /api/v1/operations/{id} returns correct fields."""
    op_id = await create_operation(
        op_type="smtp_send",
        protocol="smtp",
        description="Send test email",
        smtp_envelope={
            "from": "testing@nuvrail.com",
            "to": ["testing@nuvrail.com"],
            "subject": "Test",
            "body_preview": "Hello",
        },
        db_path=db_path,
    )
    resp = await client.get(f"/api/v1/operations/{op_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == op_id
    assert data["op_type"] == "smtp_send"
    assert data["protocol"] == "smtp"
    assert data["status"] == "pending"
    assert data["smtp_envelope"]["subject"] == "Test"


async def test_get_operation_not_found(client: httpx.AsyncClient) -> None:
    """GET /api/v1/operations/nonexistent returns 404."""
    resp = await client.get("/api/v1/operations/op_000000")
    assert resp.status_code == 404


async def test_reject_operation(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST /api/v1/operations/{id}/reject sets status to 'rejected'."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move to Trash: 5",
        db_path=db_path,
    )
    resp = await client.post(f"/api/v1/operations/{op_id}/reject")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == op_id
    assert data["status"] == "rejected"

    # Verify via GET
    detail = await client.get(f"/api/v1/operations/{op_id}")
    assert detail.json()["status"] == "rejected"


async def test_approve_already_approved(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST approve on an already-rejected op returns 409."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move op",
        db_path=db_path,
    )
    # Reject it first
    await client.post(f"/api/v1/operations/{op_id}/reject")

    # Now try to approve the rejected op — expect 409
    resp = await client.post(f"/api/v1/operations/{op_id}/approve")
    assert resp.status_code == 409


async def test_approve_imap_operation(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST approve on an IMAP op marks it executed (no upstream relay in 1.0)."""
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move 10 to Archive",
        imap_command="A001 UID MOVE 10 Archive",
        db_path=db_path,
    )
    resp = await client.post(f"/api/v1/operations/{op_id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == op_id
    assert data["status"] == "executed"

    detail = await client.get(f"/api/v1/operations/{op_id}")
    assert detail.json()["status"] == "executed"


async def test_approve_smtp_operation(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST approve on an SMTP op relays the message to the upstream SMTP server.

    Sends a real email to testing@nuvrail.com — sending to itself is acceptable
    for integration verification. Requires NUVRAIL_TEST_SMTP_* env vars to be set.
    """
    smtp_host = os.environ.get("NUVRAIL_TEST_SMTP_HOST", "")
    if not smtp_host:
        pytest.skip("NUVRAIL_TEST_SMTP_HOST not set")

    op_id = await create_operation(
        op_type="smtp_send",
        protocol="smtp",
        description='Send email to testing@nuvrail.com — Subject: "Milestone 1.0 test"',
        smtp_envelope={
            "from": "testing@nuvrail.com",
            "to": ["testing@nuvrail.com"],
            "subject": "Milestone 1.0 test",
            "body_preview": "This is an automated test from the Nuvrail approval gateway.",
        },
        db_path=db_path,
    )
    resp = await client.post(f"/api/v1/operations/{op_id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == op_id
    assert data["status"] == "executed"
    assert data["executed_at"] is not None

    detail = await client.get(f"/api/v1/operations/{op_id}")
    assert detail.json()["status"] == "executed"


async def test_reject_not_found(client: httpx.AsyncClient) -> None:
    """POST reject on nonexistent op returns 404."""
    resp = await client.post("/api/v1/operations/op_000000/reject")
    assert resp.status_code == 404


async def test_approve_not_found(client: httpx.AsyncClient) -> None:
    """POST approve on nonexistent op returns 404."""
    resp = await client.post("/api/v1/operations/op_000000/approve")
    assert resp.status_code == 404
