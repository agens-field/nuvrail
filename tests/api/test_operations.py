"""
API endpoint tests for /api/v1/operations (Milestone 1.0).

Uses httpx.AsyncClient against the FastAPI app with a tmp_path-isolated DB
injected via FastAPI dependency overrides.

The test_approve_smtp_operation test sends a real email to testing@example.com
(the test mailbox — sending to itself is acceptable for integration verification).
"""
import os
import time
from pathlib import Path

import httpx
import pytest

from api.auth import get_auth_db_path, get_current_user
from api.main import app
from api.routes.operations import get_db_path
from gateway.credentials import encrypt_credential
from gateway.staging import create_operation, get_operation
from gateway.state_db import get_db, init_db

# Fake user used to satisfy auth dependency in tests that don't test auth itself
_FAKE_USER = {"id": 1, "email": "test@test.com", "display_name": None, "created_at": 0, "api_token": "testtoken"}

# The operations API scopes every query to the caller's own agents
# (staged_operations.agent_id → agent_credentials.user_id). Tests therefore
# attach their operations to an agent owned by _FAKE_USER. The owner agent is
# seeded as the first agent_credentials row in each fresh test DB, so its id is
# deterministically 1.
OWNER_AGENT_ID = 1


async def _create_op(**kwargs):
    """Stage an operation owned by _FAKE_USER unless an agent_id is supplied.

    Mirrors gateway.staging.create_operation but defaults agent_id to the
    seeded owner agent so the operation is visible under tenant-scoping.
    """
    kwargs.setdefault("agent_id", OWNER_AGENT_ID)
    return await create_operation(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    """Initialise an isolated test DB and return its path."""
    path = tmp_path / "test_api.db"
    await init_db(path)
    return path


@pytest.fixture(autouse=True)
async def _owner_agent(db_path: Path) -> None:
    """Seed an agent_credentials row owned by _FAKE_USER (id=1).

    Operations created in tests are attached to OWNER_AGENT_ID so they are
    visible to the authenticated user under the API's tenant-scoping. Seeded
    first in a fresh DB so its id is 1 — asserted to fail loudly if that
    assumption ever breaks.
    """
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "owner", "nuvrail_owner", "x", "imap.example.com", 993, 587,
             "owner@example.com", "pass", 0),
        )
        await db.commit()
        assert cur.lastrowid == OWNER_AGENT_ID, (
            f"owner agent expected id={OWNER_AGENT_ID}, got {cur.lastrowid}"
        )


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    """Return an AsyncClient wired to the app with the test DB injected.

    get_current_user is bypassed so these tests don't need a real token.
    """
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
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
    assert data["batch_summaries"] == {}


async def test_list_operations_batch_summaries(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Multi-op batches get an intent-aggregated summary; singletons don't."""
    await _create_op(
        op_type="move", protocol="imap", description="Archive: 1:5",
        message_ids=["1:5"], folder_from="INBOX", folder_to="[Gmail]/All Mail",
        intent_label="archive", intent_confidence=1.0,
        batch_id="batch_test1", db_path=db_path,
    )
    await _create_op(
        op_type="mark_read", protocol="imap", description="Mark as read: 7,8",
        message_ids=["7,8"], folder_from="INBOX",
        batch_id="batch_test1", db_path=db_path,
    )
    # A second batch with only one op — no summary expected
    await _create_op(
        op_type="move", protocol="imap", description="Move 9 to Receipts",
        message_ids=["9"], folder_from="INBOX", folder_to="Receipts",
        batch_id="batch_solo", db_path=db_path,
    )

    resp = await client.get("/api/v1/operations?status=pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_summaries"].keys() == {"batch_test1"}
    assert data["batch_summaries"]["batch_test1"] == (
        "Inbox triage — 5 archived, 2 marked read"
    )


async def test_list_operations_with_pending(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /api/v1/operations returns created pending ops."""
    op_id = await _create_op(
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
    await _create_op(
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


async def test_list_operations_agent_filter(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /api/v1/operations?agent_id= filters correctly."""
    async with get_db(db_path) as db:
        cur1 = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "A1", "nuvrail_a1", "x", "imap.example.com", 993, 587, "a1@example.com", "pass", 0),
        )
        cur2 = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "A2", "nuvrail_a2", "x", "imap.example.com", 993, 587, "a2@example.com", "pass", 0),
        )
        await db.commit()
        agent_id_1 = cur1.lastrowid
        agent_id_2 = cur2.lastrowid

    op_id_1 = await _create_op(
        op_type="move",
        protocol="imap",
        description="Agent 1 op",
        agent_id=agent_id_1,
        db_path=db_path,
    )
    await _create_op(
        op_type="move",
        protocol="imap",
        description="Agent 2 op",
        agent_id=agent_id_2,
        db_path=db_path,
    )

    resp = await client.get(f"/api/v1/operations?agent_id={agent_id_1}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["operations"][0]["id"] == op_id_1


async def test_get_operation_detail(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /api/v1/operations/{id} returns correct fields."""
    full_body = "Hello, this is the complete message body for approval review."
    op_id = await _create_op(
        op_type="smtp_send",
        protocol="smtp",
        description="Send test email",
        smtp_envelope={
            "from": "testing@example.com",
            "to": ["testing@example.com"],
            "subject": "Test",
            "body": full_body,
            "body_preview": full_body[:200],
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
    # Verify full body is returned in the approval card payload (not truncated)
    assert data["smtp_envelope"]["body"] == full_body
    assert data["smtp_envelope"]["body_preview"] == full_body[:200]


async def test_smtp_envelope_full_body_stored_and_returned(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """
    Regression test for GitHub #4: SMTP approval must use full body, not 200-char preview.

    The smtp_envelope stored in staging must contain both `body` (full text) and
    `body_preview` (truncated). The GET response must expose both so the approver
    can read the complete message before deciding.
    """
    long_body = "A" * 500  # deliberately longer than 200-char preview limit
    preview = long_body[:200]
    op_id = await _create_op(
        op_type="smtp_send",
        protocol="smtp",
        description="Send long email",
        smtp_envelope={
            "from": "agent@nuvrail.example.com",
            "to": ["human@example.com"],
            "subject": "Long body test",
            "body": long_body,
            "body_preview": preview,
        },
        db_path=db_path,
    )
    resp = await client.get(f"/api/v1/operations/{op_id}")
    assert resp.status_code == 200
    envelope = resp.json()["smtp_envelope"]
    # Full body must be present and complete
    assert envelope["body"] == long_body, "Full body must not be truncated in approval payload"
    assert len(envelope["body"]) == 500
    # Preview is still present for quick-scan display
    assert envelope["body_preview"] == preview
    assert len(envelope["body_preview"]) == 200


async def test_get_operation_not_found(client: httpx.AsyncClient) -> None:
    """GET /api/v1/operations/nonexistent returns 404."""
    resp = await client.get("/api/v1/operations/op_000000")
    assert resp.status_code == 404


async def test_reject_operation(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST /api/v1/operations/{id}/reject sets status to 'rejected'."""
    op_id = await _create_op(
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
    op_id = await _create_op(
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
    """POST approve on an IMAP op executes against the upstream IMAP server.

    Uses a CREATE op (creates a folder then immediately renames/deletes it)
    so the test doesn't require real UIDs or an existing folder state.
    Requires NUVRAIL_TEST_IMAP_* env vars. Skipped if not set.
    """
    imap_host = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
    if not imap_host:
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set — skipping upstream IMAP test")

    imap_port = int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993"))
    imap_user = os.environ.get("NUVRAIL_TEST_IMAP_USER", "")
    imap_pass = os.environ.get("NUVRAIL_TEST_IMAP_PASS", "")

    # Insert an agent credential row so the approve path can look up credentials
    # via agent_id rather than falling back to env vars.
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "test", "nuvrail_imap_approve_test", "x",
             imap_host, imap_port, 587, imap_user,
             encrypt_credential(imap_pass), int(time.time())),
        )
        await db.commit()
        agent_id = cur.lastrowid

    folder_name = f"NuvrailTest_{int(time.time())}"

    op_id = await _create_op(
        op_type="create",
        protocol="imap",
        agent_id=agent_id,
        description=f"Create folder {folder_name}",
        imap_command=f"A001 CREATE {folder_name}",
        folder_to=folder_name,
        db_path=db_path,
    )
    resp = await client.post(f"/api/v1/operations/{op_id}/approve")
    assert resp.status_code == 200, f"Approve failed: {resp.text}"
    data = resp.json()
    assert data["id"] == op_id
    assert data["status"] == "executed"

    detail = await client.get(f"/api/v1/operations/{op_id}")
    assert detail.json()["status"] == "executed"

    # Cleanup: delete the test folder via direct IMAP
    import aioimaplib
    c = aioimaplib.IMAP4_SSL(host=imap_host, port=imap_port)
    await c.wait_hello_from_server()
    await c.login(imap_user, imap_pass)
    await c.delete(folder_name)
    await c.logout()


async def test_approve_smtp_operation(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST approve on an SMTP op relays the message to the upstream SMTP server.

    Sends a real email to testing@example.com — sending to itself is acceptable
    for integration verification. Requires NUVRAIL_TEST_SMTP_* env vars to be set.
    """
    smtp_host = os.environ.get("NUVRAIL_TEST_SMTP_HOST", "")
    if not smtp_host:
        pytest.skip("NUVRAIL_TEST_SMTP_HOST not set")

    smtp_port = int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587"))
    smtp_user = os.environ.get("NUVRAIL_TEST_SMTP_USER", "")
    smtp_pass = os.environ.get("NUVRAIL_TEST_SMTP_PASS", "")

    # Insert agent credential so approve path can look up credentials by agent_id
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "test", "nuvrail_smtp_approve_test", "x",
             smtp_host, 993, smtp_port, smtp_user,
             encrypt_credential(smtp_pass), int(time.time())),
        )
        await db.commit()
        smtp_agent_id = cur.lastrowid

    op_id = await _create_op(
        op_type="smtp_send",
        protocol="smtp",
        agent_id=smtp_agent_id,
        description='Send email to testing@example.com — Subject: "Milestone 1.0 test"',
        smtp_envelope={
            "from": "testing@example.com",
            "to": ["testing@example.com"],
            "subject": "Milestone 1.0 test",
            "body": "This is an automated test from the Nuvrail approval gateway.",
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


async def test_reject_restores_snapshot_and_queues_pending_reverts(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """
    POST /reject on a STORE op with snapshot:
      - restores messages table flags to pre-op state
      - inserts pending_reverts rows for the proxy to inject
    """
    import json
    from gateway.state_db import (
        apply_optimistic_flag_update,
        get_message,
        get_or_create_folder,
        get_pending_reverts,
        upsert_message,
    )

    # Set up: folder + message with no flags
    folder_id = await get_or_create_folder("INBOX", user_id=None, db_path=db_path)
    await upsert_message(folder_id, 99, seq_num=1, flags=[], db_path=db_path)

    # Stage the op with a snapshot of the pre-op state
    snap = {"99": {"flags": [], "seq_num": 1, "folder_id": folder_id}}
    op_id = await _create_op(
        op_type="mark_read",
        protocol="imap",
        description="Mark 99 as read",
        imap_command="A001 UID STORE 99 +FLAGS (\\Seen)",
        message_ids=["99"],
        flags_add=[r"\Seen"],
        snapshot=snap,
        db_path=db_path,
    )

    # Simulate optimistic update: message now shows \Seen
    await apply_optimistic_flag_update(
        folder_id, "99", flags_add=[r"\Seen"], flags_remove=[], db_path=db_path
    )
    msg = await get_message(folder_id, 99, db_path=db_path)
    assert r"\Seen" in json.loads(msg["flags"]), "Pre-reject: optimistic update should be applied"

    # Reject via API
    resp = await client.post(f"/api/v1/operations/{op_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    # Messages table should be restored to empty flags
    msg_after = await get_message(folder_id, 99, db_path=db_path)
    assert json.loads(msg_after["flags"]) == [], "After reject: flags should be restored to []"

    # pending_reverts should have 1 row for UID 99
    reverts = await get_pending_reverts(folder_id, db_path=db_path)
    assert len(reverts) == 1
    assert reverts[0]["uid"] == 99
    assert reverts[0]["delivered_at"] is None


async def test_reject_without_snapshot_does_not_raise(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST /reject on an op without snapshot succeeds without error."""
    op_id = await _create_op(
        op_type="move",
        protocol="imap",
        description="Move to Archive",
        db_path=db_path,
    )
    resp = await client.post(f"/api/v1/operations/{op_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# NOTE: the API-level auto-approval test moved to the nuvrail-enterprise package
# along with the rules engine. See docs/REPO_SPLIT.md.


# ---------------------------------------------------------------------------
# Batch approve/reject tests
# ---------------------------------------------------------------------------


async def test_batch_approve_multiple(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST /operations/batch/approve with 3 IMAP ops → all in approved list, status=executed.

    Uses op_type='append' which has a no-op early return in _execute_imap_upstream,
    so no upstream IMAP connection is needed.
    """
    op_ids = []
    for i in range(3):
        op_id = await _create_op(
            op_type="append",
            protocol="imap",
            description=f"Append op {i}",
            db_path=db_path,
        )
        op_ids.append(op_id)

    resp = await client.post("/api/v1/operations/batch/approve", json={"operation_ids": op_ids})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["approved"]) == 3
    assert len(data["failed"]) == 0
    assert len(data["skipped"]) == 0
    approved_ids = {r["id"] for r in data["approved"]}
    assert approved_ids == set(op_ids)
    for r in data["approved"]:
        assert r["status"] == "executed"


async def test_batch_approve_skips_non_pending(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Batch approve with one already-approved op → goes to skipped."""
    op_id_pending = await _create_op(
        op_type="append",
        protocol="imap",
        description="Pending op",
        db_path=db_path,
    )
    op_id_already = await _create_op(
        op_type="append",
        protocol="imap",
        description="Already approved op",
        db_path=db_path,
    )
    # Approve op_id_already first so it's no longer pending
    pre = await client.post(f"/api/v1/operations/{op_id_already}/approve")
    assert pre.status_code == 200

    resp = await client.post(
        "/api/v1/operations/batch/approve",
        json={"operation_ids": [op_id_pending, op_id_already]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["approved"]) == 1
    assert data["approved"][0]["id"] == op_id_pending
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["id"] == op_id_already
    assert len(data["failed"]) == 0


async def test_batch_approve_skips_not_found(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Batch approve with an unknown op_id → goes to skipped."""
    op_id = await _create_op(
        op_type="append",
        protocol="imap",
        description="Real op",
        db_path=db_path,
    )
    fake_id = "op_000000nonexistent"

    resp = await client.post(
        "/api/v1/operations/batch/approve",
        json={"operation_ids": [op_id, fake_id]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["approved"]) == 1
    assert data["approved"][0]["id"] == op_id
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["id"] == fake_id
    assert len(data["failed"]) == 0


async def test_batch_approve_continues_after_failure(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Batch approve: one fails (mocked _do_approve raises), others still processed."""
    from unittest.mock import patch
    from api.models import ApproveResponse

    op_ids = []
    for i in range(3):
        op_id = await _create_op(
            op_type="append",
            protocol="imap",
            description=f"Op {i}",
            db_path=db_path,
        )
        op_ids.append(op_id)

    call_count = 0

    async def _mock_do_approve(op_id: str, row: dict, db_path_arg: Path) -> ApproveResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated upstream failure")
        return ApproveResponse(id=op_id, status="executed", executed_at=None)

    with patch("api.routes.operations._do_approve", side_effect=_mock_do_approve):
        resp = await client.post(
            "/api/v1/operations/batch/approve",
            json={"operation_ids": op_ids},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["approved"]) == 2
    assert len(data["failed"]) == 1
    assert data["failed"][0]["id"] == op_ids[1]
    assert "simulated upstream failure" in data["failed"][0]["error"]
    assert len(data["skipped"]) == 0


async def test_batch_reject_multiple(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST /operations/batch/reject with 3 ops → all in rejected list."""
    op_ids = []
    for i in range(3):
        op_id = await _create_op(
            op_type="move",
            protocol="imap",
            description=f"Move op {i}",
            db_path=db_path,
        )
        op_ids.append(op_id)

    resp = await client.post("/api/v1/operations/batch/reject", json={"operation_ids": op_ids})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["rejected"]) == 3
    assert len(data["failed"]) == 0
    assert len(data["skipped"]) == 0
    rejected_ids = {r["id"] for r in data["rejected"]}
    assert rejected_ids == set(op_ids)
    for r in data["rejected"]:
        assert r["status"] == "rejected"


async def test_batch_reject_skips_non_pending(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Batch reject with one already-rejected op → goes to skipped."""
    op_id_pending = await _create_op(
        op_type="move",
        protocol="imap",
        description="Pending op",
        db_path=db_path,
    )
    op_id_already = await _create_op(
        op_type="move",
        protocol="imap",
        description="Already rejected op",
        db_path=db_path,
    )
    pre = await client.post(f"/api/v1/operations/{op_id_already}/reject")
    assert pre.status_code == 200

    resp = await client.post(
        "/api/v1/operations/batch/reject",
        json={"operation_ids": [op_id_pending, op_id_already]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["rejected"]) == 1
    assert data["rejected"][0]["id"] == op_id_pending
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["id"] == op_id_already
    assert len(data["failed"]) == 0


async def test_batch_approve_max_100(client: httpx.AsyncClient) -> None:
    """POST batch/approve with 101 ids → 400."""
    ids = [f"op_{i:06d}" for i in range(101)]
    resp = await client.post("/api/v1/operations/batch/approve", json={"operation_ids": ids})
    assert resp.status_code == 400
    assert "100" in resp.json()["detail"]


async def test_batch_approve_empty(client: httpx.AsyncClient) -> None:
    """POST batch/approve with empty list → 400."""
    resp = await client.post("/api/v1/operations/batch/approve", json={"operation_ids": []})
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


async def test_single_approve_still_works(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Refactored approve_op still works correctly via single-op endpoint.

    Uses op_type='append' which is a no-op at the upstream IMAP level.
    """
    op_id = await _create_op(
        op_type="append",
        protocol="imap",
        description="Single approve test",
        db_path=db_path,
    )
    resp = await client.post(f"/api/v1/operations/{op_id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == op_id
    assert data["status"] == "executed"

    detail = await client.get(f"/api/v1/operations/{op_id}")
    assert detail.json()["status"] == "executed"


async def test_single_reject_still_works(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Refactored reject_op still works correctly via single-op endpoint."""
    op_id = await _create_op(
        op_type="move",
        protocol="imap",
        description="Single reject test",
        db_path=db_path,
    )
    resp = await client.post(f"/api/v1/operations/{op_id}/reject")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == op_id
    assert data["status"] == "rejected"

    detail = await client.get(f"/api/v1/operations/{op_id}")
    assert detail.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# Tenant isolation (cross-user IDOR) regression tests
# ---------------------------------------------------------------------------


async def _seed_foreign_op(db_path: Path, *, op_type: str = "move", status: str | None = None) -> str:
    """Create an operation owned by a *different* user (id=2) and return its id.

    The authenticated test user is _FAKE_USER (id=1); operations seeded here
    belong to user 2 via a separate agent, so user 1 must never be able to see
    or act on them.
    """
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (2, "victim", "nuvrail_victim", "x", "imap.example.com", 993, 587,
             "victim@example.com", "pass", 0),
        )
        await db.commit()
        foreign_agent_id = cur.lastrowid

    op_id = await create_operation(
        op_type=op_type,
        protocol="imap",
        description="Victim's private operation",
        agent_id=foreign_agent_id,
        db_path=db_path,
    )
    if status is not None:
        async with get_db(db_path) as db:
            await db.execute(
                "UPDATE staged_operations SET status = ? WHERE id = ?", (status, op_id)
            )
            await db.commit()
    return op_id


async def test_list_excludes_other_users_operations(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /operations must not leak operations owned by other users."""
    foreign_op = await _seed_foreign_op(db_path)
    mine = await _create_op(op_type="move", protocol="imap", description="My op", db_path=db_path)

    resp = await client.get("/api/v1/operations")
    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()["operations"]}
    assert mine in ids
    assert foreign_op not in ids


async def test_get_other_users_operation_returns_404(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /operations/{id} on another user's op returns 404 (not 403, to avoid leaking existence)."""
    foreign_op = await _seed_foreign_op(db_path)
    resp = await client.get(f"/api/v1/operations/{foreign_op}")
    assert resp.status_code == 404


async def test_approve_other_users_operation_returns_404(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """A user cannot approve (execute) another user's operation."""
    foreign_op = await _seed_foreign_op(db_path)
    resp = await client.post(f"/api/v1/operations/{foreign_op}/approve")
    assert resp.status_code == 404
    # The victim's op must remain pending — never executed.
    row = await get_operation(foreign_op, db_path=db_path)
    assert row["status"] == "pending"


async def test_reject_other_users_operation_returns_404(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """A user cannot reject another user's operation."""
    foreign_op = await _seed_foreign_op(db_path)
    resp = await client.post(f"/api/v1/operations/{foreign_op}/reject")
    assert resp.status_code == 404
    row = await get_operation(foreign_op, db_path=db_path)
    assert row["status"] == "pending"


async def test_undo_other_users_operation_returns_404(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """A user cannot undo another user's executed operation."""
    foreign_op = await _seed_foreign_op(db_path, op_type="move", status="executed")
    resp = await client.post(f"/api/v1/operations/{foreign_op}/undo")
    assert resp.status_code == 404


async def test_batch_approve_skips_other_users_operations(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Batch approve treats another user's op as not-found (skipped), never executed."""
    foreign_op = await _seed_foreign_op(db_path, op_type="append")
    resp = await client.post(
        "/api/v1/operations/batch/approve",
        json={"operation_ids": [foreign_op]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["approved"]) == 0
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["id"] == foreign_op
    row = await get_operation(foreign_op, db_path=db_path)
    assert row["status"] == "pending"


async def test_batch_reject_skips_other_users_operations(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Batch reject treats another user's op as not-found (skipped), never rejected."""
    foreign_op = await _seed_foreign_op(db_path)
    resp = await client.post(
        "/api/v1/operations/batch/reject",
        json={"operation_ids": [foreign_op]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["rejected"]) == 0
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["id"] == foreign_op
    row = await get_operation(foreign_op, db_path=db_path)
    assert row["status"] == "pending"


async def test_message_previews_do_not_leak_across_tenants(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Approval-card message previews must resolve the folder within the owning
    tenant. Folder names are unique only per-user (UNIQUE(user_id, name)), so a
    name-only lookup can surface another tenant's same-named folder ("INBOX") and
    leak their sender/subject — especially since UIDs overlap across mailboxes.

    Deterministic trigger: ONLY the other tenant has an "INBOX". The authenticated
    user (OWNER_AGENT_ID / user 1) has no folder of that name, so a correctly
    scoped lookup finds nothing, while the old name-only lookup would resolve to —
    and leak — the other tenant's mailbox. Regression for the #73 follow-up.
    """
    from gateway.state_db import get_or_create_folder, upsert_message

    # Other tenant (user 2) owns the only "INBOX" + a message at uid 1.
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO users (id, email, hashed_password, api_token, created_at) "
            "VALUES (2, 'victim@secret.test', 'x', 'victimtoken', 0)"
        )
        await db.commit()
    victim_folder = await get_or_create_folder("INBOX", user_id=2, db_path=db_path)
    await upsert_message(
        victim_folder, 1, seq_num=1, sender="victim@secret.test",
        subject="VICTIM CONFIDENTIAL", db_path=db_path,
    )

    # The authenticated user (user 1) stages an op referencing "INBOX"/uid 1 —
    # but owns no such folder. A name-only lookup would hit the victim's INBOX.
    op_id = await _create_op(
        op_type="move", protocol="imap", description="Archive uid 1",
        message_ids=["1"], folder_from="INBOX", folder_to="Archive",
        db_path=db_path,
    )

    resp = await client.get(f"/api/v1/operations/{op_id}")
    assert resp.status_code == 200
    previews = resp.json()["message_previews"]

    # The other tenant's mailbox metadata must never surface.
    senders = {p["sender"] for p in previews}
    subjects = {p["subject"] for p in previews}
    assert "victim@secret.test" not in senders, f"cross-tenant leak: {previews}"
    assert "VICTIM CONFIDENTIAL" not in subjects, f"cross-tenant leak: {previews}"
