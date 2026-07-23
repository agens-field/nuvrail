"""
Tests for auth + agent credential endpoints.

POST /api/v1/auth/register
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/agents
GET  /api/v1/agents
DELETE /api/v1/agents/{id}

Also verifies that existing operations/audit endpoints now require auth.

Uses httpx.AsyncClient + tmp_path DB, same pattern as test_operations.py.
Both get_db_path (routes) and get_auth_db_path (auth dependency) are
overridden so all DB access hits the same isolated test DB.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from api.routes import auth as auth_routes
from gateway.security_controls import AuthAbuseProtector
from gateway.state_db import get_db

# db_path and client fixtures are provided by tests/api/conftest.py.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_and_login(
    client: httpx.AsyncClient,
    email: str = "test@example.com",
    password: str = "hunter2",
) -> str:
    """Register a user and return their bearer token."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "Tester"},
    )
    assert resp.status_code == 201
    # Log in to get the token
    resp2 = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp2.status_code == 200
    return resp2.json()["token"]


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


async def test_register_creates_user(client: httpx.AsyncClient) -> None:
    """POST /auth/register returns 201 with user_id."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "user_id" in data
    assert data["email"] == "alice@example.com"
    assert data["user_id"] > 0


async def test_register_duplicate_email(client: httpx.AsyncClient) -> None:
    """POST /auth/register with duplicate email returns 409."""
    payload = {"email": "dup@example.com", "password": "abc"}
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Login tests
# ---------------------------------------------------------------------------


async def test_login_valid(client: httpx.AsyncClient) -> None:
    """POST /auth/login with correct credentials returns a token."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": "correcthorse"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "correcthorse"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["token_type"] == "bearer"
    assert data["user_id"] > 0
    assert data["email"] == "bob@example.com"


async def test_login_bad_password(client: httpx.AsyncClient) -> None:
    """POST /auth/login with wrong password returns 401."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "password": "rightpass"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401


async def test_login_unknown_email(client: httpx.AsyncClient) -> None:
    """POST /auth/login with unknown email returns 401."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "anything"},
    )
    assert resp.status_code == 401


async def test_login_rate_limit_and_lockout(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated failures should trigger lockout with HTTP 429 + Retry-After."""
    test_protector = AuthAbuseProtector(
        namespace="test_api_login",
        attempt_window_seconds=60,
        max_attempts_per_ip_window=50,
        max_attempts_per_account_window=50,
        failure_window_seconds=60,
        max_failures_before_lockout=2,
        base_lockout_seconds=30,
        max_lockout_seconds=30,
    )
    monkeypatch.setattr(auth_routes, "LOGIN_ABUSE_PROTECTOR", test_protector)

    await client.post(
        "/api/v1/auth/register",
        json={"email": "lockout@example.com", "password": "rightpass"},
    )

    # First bad attempt remains a normal 401.
    bad1 = await client.post(
        "/api/v1/auth/login",
        json={"email": "lockout@example.com", "password": "wrongpass"},
    )
    assert bad1.status_code == 401

    # Second bad attempt crosses the threshold and is locked out.
    bad2 = await client.post(
        "/api/v1/auth/login",
        json={"email": "lockout@example.com", "password": "wrongpass"},
    )
    assert bad2.status_code == 429
    assert bad2.headers.get("Retry-After") == "30"


# ---------------------------------------------------------------------------
# /me tests
# ---------------------------------------------------------------------------


async def test_me_with_valid_token(client: httpx.AsyncClient) -> None:
    """GET /auth/me with a valid token returns user info."""
    token = await _register_and_login(client)
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "test@example.com"
    assert data["display_name"] == "Tester"
    assert "user_id" in data
    assert "created_at" in data


async def test_me_without_token(client: httpx.AsyncClient) -> None:
    """GET /auth/me without a token returns 401."""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_suspended_user_rejected_with_403(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Issue #65: a suspended account is rejected at bearer auth with 403.

    The token is still valid (so not 401) — the account is administratively
    disabled. This blocks every authenticated route, including approve/send and
    agent management, for a suspended user.
    """
    token = await _register_and_login(client, email="suspendme@example.com")
    # Sanity: works before suspension.
    ok = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert ok.status_code == 200

    # Suspend directly in the DB (the manual incident-response path for #65).
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE users SET suspended_at = 1 WHERE email = ?",
            ("suspendme@example.com",),
        )
        await db.commit()

    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    assert "suspend" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Operations + audit now require auth
# ---------------------------------------------------------------------------


async def test_operations_require_auth(client: httpx.AsyncClient) -> None:
    """GET /operations without token returns 401."""
    resp = await client.get("/api/v1/operations")
    assert resp.status_code == 401


async def test_audit_requires_auth(client: httpx.AsyncClient) -> None:
    """GET /audit without token returns 401."""
    resp = await client.get("/api/v1/audit")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Agent credential tests
# ---------------------------------------------------------------------------


async def test_create_agent(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /agents returns agent_username and agent_token."""
    token = await _register_and_login(client)

    async def _ok_verify(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _ok_verify)
    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.gmail.com",
            "upstream_user": "me@gmail.com",
            "upstream_password": "apppassword",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "agent_username" in data
    assert data["agent_username"].startswith("nuvrail_")
    assert "agent_token" in data
    assert len(data["agent_token"]) > 0
    assert data["upstream_host"] == "imap.gmail.com"
    assert data["upstream_user"] == "me@gmail.com"
    # Token must NOT be empty (it's the show-once plaintext)
    assert data["agent_token"] != ""


async def test_agent_password_stored_encrypted(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /agents must store upstream_password as an AES-256-GCM envelope, not plaintext."""
    from gateway.credentials import is_encrypted
    from gateway.state_db import get_db

    token = await _register_and_login(client)
    plaintext_password = "my-upstream-password"

    async def _ok_verify(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _ok_verify)

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.example.com",
            "upstream_user": "user@example.com",
            "upstream_password": plaintext_password,
        },
    )
    assert resp.status_code == 201
    agent_id = resp.json()["id"]

    # Read the raw stored value directly from the DB
    async with get_db(db_path) as db, db.execute(
        "SELECT upstream_password FROM agent_credentials WHERE id = ?", (agent_id,)
    ) as cur:
        row = await cur.fetchone()

    assert row is not None, "Agent credential row not found in DB"
    stored = row[0]

    assert stored != plaintext_password, (
        "upstream_password was stored as plaintext — encrypt_credential() not called"
    )
    assert is_encrypted(stored), (
        f"upstream_password does not look like an AES-GCM envelope: {stored!r}"
    )


async def test_agent_token_not_repeated(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /agents does NOT include the agent_token field."""
    token = await _register_and_login(client)
    auth = {"Authorization": f"Bearer {token}"}

    async def _ok_verify(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _ok_verify)
    await client.post(
        "/api/v1/agents",
        headers=auth,
        json={
            "upstream_host": "imap.example.com",
            "upstream_user": "agent@example.com",
            "upstream_password": "pass",
        },
    )
    resp = await client.get("/api/v1/agents", headers=auth)
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 1
    assert "agent_token" not in agents[0]
    assert "agent_username" in agents[0]


async def test_revoke_agent(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /agents/{id} sets revoked_at on the credential."""
    token = await _register_and_login(client)
    auth = {"Authorization": f"Bearer {token}"}

    async def _ok_verify(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _ok_verify)

    # Create an agent
    create_resp = await client.post(
        "/api/v1/agents",
        headers=auth,
        json={
            "upstream_host": "imap.example.com",
            "upstream_user": "x@example.com",
            "upstream_password": "p",
        },
    )
    assert create_resp.status_code == 201
    agent_id = create_resp.json()["id"]

    # Revoke it
    revoke_resp = await client.delete(f"/api/v1/agents/{agent_id}", headers=auth)
    assert revoke_resp.status_code == 204

    # List agents — revoked_at should be set
    list_resp = await client.get("/api/v1/agents", headers=auth)
    agents = list_resp.json()
    assert len(agents) == 1
    assert agents[0]["revoked_at"] is not None


async def test_revoke_agent_purges_secrets_and_nulls_columns(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disconnecting an agent (DELETE /agents/{id}) must purge its upstream
    secrets BEFORE nulling the reference columns, so nothing is orphaned in the
    external secret store (Privacy §6 — removed on disconnect). Mirrors the #72
    fix for the account-deletion path, applied to the per-agent disconnect.
    """
    token = await _register_and_login(client)
    auth = {"Authorization": f"Bearer {token}"}

    async def _ok_verify(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _ok_verify)

    # Record the purge call (the helper itself is unit-tested under #72; here we
    # only assert the disconnect path invokes it with this agent's row).
    purged: list[dict] = []

    async def _record_purge(agent_row: dict, **kwargs: object) -> None:
        purged.append(dict(agent_row))

    monkeypatch.setattr(auth_routes, "purge_agent_upstream_secrets", _record_purge)

    create_resp = await client.post(
        "/api/v1/agents",
        headers=auth,
        json={
            "upstream_host": "imap.example.com",
            "upstream_user": "x@example.com",
            "upstream_password": "p",
        },
    )
    assert create_resp.status_code == 201
    agent_id = create_resp.json()["id"]

    revoke_resp = await client.delete(f"/api/v1/agents/{agent_id}", headers=auth)
    assert revoke_resp.status_code == 204

    # The disconnect path called the purge helper with the agent's stored row,
    # which still carried the (encrypted) password at call time — i.e. purge ran
    # BEFORE the columns were nulled.
    assert len(purged) == 1
    assert purged[0]["upstream_password"] is not None

    # After the call, every secret column is nulled (no orphaned reference left).
    async with get_db(db_path) as db, db.execute(
        """
            SELECT upstream_password, oauth2_refresh_token,
                   oauth2_access_token, oauth2_client_secret, revoked_at
            FROM agent_credentials WHERE id = ?
            """,
        (agent_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row["upstream_password"] is None
    assert row["oauth2_refresh_token"] is None
    assert row["oauth2_access_token"] is None
    assert row["oauth2_client_secret"] is None
    assert row["revoked_at"] is not None


async def test_create_agent_imap_auth_failed_no_db_row(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _register_and_login(client)

    async def _fail_verify(*args: object, **kwargs: object) -> None:
        raise auth_routes.ImapValidationError("imap_auth_failed", "Wrong password")

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _fail_verify)

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.gmail.com",
            "upstream_user": "me@gmail.com",
            "upstream_password": "bad",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "imap_auth_failed"

    async with get_db(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM agent_credentials") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0


async def test_human_token_stored_as_sha256(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Bearer token must be stored as SHA-256 hex digest, not plaintext."""
    import hashlib

    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "sha@example.com", "password": "pw"},
    )
    assert resp.status_code == 201

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "sha@example.com", "password": "pw"},
    )
    assert login_resp.status_code == 200
    plaintext_token = login_resp.json()["token"]

    async with get_db(db_path) as db, db.execute(
        "SELECT api_token FROM users WHERE email = ?", ("sha@example.com",)
    ) as cur:
        row = await cur.fetchone()

    assert row is not None
    stored = row[0]
    expected_hash = hashlib.sha256(plaintext_token.encode()).hexdigest()

    assert stored != plaintext_token, "Token stored as plaintext — must be SHA-256"
    assert len(stored) == 64, f"Expected 64-char hex digest, got {len(stored)} chars"
    assert stored == expected_hash, "Stored hash doesn't match sha256(token)"


async def test_create_agent_imap_success_creates_row(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _register_and_login(client)

    async def _ok_verify(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _ok_verify)

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.gmail.com",
            "upstream_user": "me@gmail.com",
            "upstream_password": "good",
        },
    )
    assert resp.status_code == 201

    async with get_db(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM agent_credentials") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_create_agent_oauth2_no_password_required(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """OAuth2 agent creation succeeds without upstream_password.
    No IMAP validation is attempted for OAuth2 agents.
    """
    token = await _register_and_login(client, email="oauth2test@example.com")

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.gmail.com",
            "upstream_imap_port": 993,
            "upstream_smtp_port": 587,
            "upstream_user": "martin@animalhorde.com",
            "oauth2_provider": "google",
            "oauth2_client_id": "fake_client_id",
            "oauth2_client_secret": "fake_client_secret",
            "oauth2_refresh_token": "fake_refresh_token",
            "label": "gmail-xoauth2-test",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["upstream_user"] == "martin@animalhorde.com"
    assert "agent_token" in data

    # Verify OAuth2 fields are stored in DB (encrypted, not plaintext)
    async with get_db(db_path) as db, db.execute(
        "SELECT oauth2_provider, oauth2_client_id, oauth2_refresh_token, "
        "oauth2_client_secret, upstream_password "
        "FROM agent_credentials WHERE upstream_user = ?",
        ("martin@animalhorde.com",),
    ) as cur:
        row = await cur.fetchone()

    assert row is not None
    assert row["oauth2_provider"] == "google"
    assert row["oauth2_client_id"] == "fake_client_id"  # not a secret, stored plaintext
    assert row["oauth2_refresh_token"] != "fake_refresh_token"  # must be encrypted
    assert row["oauth2_client_secret"] != "fake_client_secret"  # must be encrypted
    assert row["upstream_password"] is None  # no password for OAuth2 agents


@pytest.mark.asyncio(loop_scope="session")
async def test_create_agent_oauth2_rejects_conflicting_auth(
    client: httpx.AsyncClient,
) -> None:
    """Providing both upstream_password and oauth2_provider is rejected."""
    token = await _register_and_login(client, email="oauth2conflict@example.com")

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.gmail.com",
            "upstream_user": "martin@animalhorde.com",
            "upstream_password": "some_password",
            "oauth2_provider": "google",
            "oauth2_client_id": "fake_client_id",
            "oauth2_client_secret": "fake_client_secret",
            "oauth2_refresh_token": "fake_refresh_token",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "conflicting_auth"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_agent_oauth2_rejects_incomplete_fields(
    client: httpx.AsyncClient,
) -> None:
    """oauth2_provider set but missing oauth2_refresh_token is rejected."""
    token = await _register_and_login(client, email="oauth2incomplete@example.com")

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.gmail.com",
            "upstream_user": "martin@animalhorde.com",
            "oauth2_provider": "google",
            "oauth2_client_id": "fake_client_id",
            # oauth2_client_secret and oauth2_refresh_token intentionally omitted
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "incomplete_oauth2"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_agent_oauth2_rejects_unsupported_provider(
    client: httpx.AsyncClient,
) -> None:
    """Unsupported oauth2_provider value is rejected."""
    token = await _register_and_login(client, email="oauth2badprovider@example.com")

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.example.com",
            "upstream_user": "user@example.com",
            "oauth2_provider": "yahoo",  # not supported
            "oauth2_client_id": "fake_client_id",
            "oauth2_client_secret": "fake_client_secret",
            "oauth2_refresh_token": "fake_refresh_token",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "unsupported_oauth2_provider"


@pytest.mark.asyncio(loop_scope="session")
async def test_list_agents_includes_last_activity_at(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /agents returns last_activity_at=None for a new agent, and a timestamp
    once a staged_operations row exists for that agent."""
    import time as _time

    token = await _register_and_login(client)

    async def _ok_verify(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(auth_routes, "_verify_imap_connection", _ok_verify)

    resp = await client.post(
        "/api/v1/agents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "upstream_host": "imap.example.com",
            "upstream_user": "activity@example.com",
            "upstream_password": "pw",
        },
    )
    assert resp.status_code == 201
    agent_id = resp.json()["id"]

    # Before any staged op: last_activity_at should be null
    resp = await client.get("/api/v1/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    agents = resp.json()
    agent = next(a for a in agents if a["id"] == agent_id)
    assert agent["last_activity_at"] is None, (
        f"Expected None before any activity; got {agent['last_activity_at']}"
    )

    # Insert a staged_operations row for this agent
    now = int(_time.time())
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO staged_operations
               (id, op_type, protocol, description, status, agent_id, created_at, expires_at)
               VALUES ('op_test_la', 'move', 'imap', 'test op', 'pending', ?, ?, ?)""",
            (agent_id, now, now + 172800),
        )
        await db.commit()

    # Now last_activity_at should reflect that timestamp
    resp = await client.get("/api/v1/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    agents = resp.json()
    agent = next(a for a in agents if a["id"] == agent_id)
    assert agent["last_activity_at"] == now, (
        f"Expected {now}; got {agent['last_activity_at']}"
    )


# ---------------------------------------------------------------------------
# Password reset token must not be logged in plaintext (security)
# ---------------------------------------------------------------------------


async def test_reset_request_does_not_log_token_by_default(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """By default the plaintext reset token / URL must never reach the logs."""
    monkeypatch.delenv("NUVRAIL_RESET_TOKEN_LOG", raising=False)
    await client.post(
        "/api/v1/auth/register",
        json={"email": "resetlog@example.com", "password": "hunter2pass!"},
    )

    caplog.set_level(logging.INFO, logger="api.routes.auth")
    resp = await client.post(
        "/api/v1/auth/reset-request", json={"email": "resetlog@example.com"}
    )
    assert resp.status_code == 200

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "/#/reset" not in messages, "reset URL must not be logged"
    assert "token=" not in messages, "reset token must not be logged"


async def test_reset_request_logs_url_only_when_opted_in(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With the explicit dev opt-in, the full reset URL is logged (for local use)."""
    monkeypatch.setenv("NUVRAIL_RESET_TOKEN_LOG", "1")
    await client.post(
        "/api/v1/auth/register",
        json={"email": "resetlog2@example.com", "password": "hunter2pass!"},
    )

    caplog.set_level(logging.WARNING, logger="api.routes.auth")
    resp = await client.post(
        "/api/v1/auth/reset-request", json={"email": "resetlog2@example.com"}
    )
    assert resp.status_code == 200

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "/#/reset?token=" in messages
