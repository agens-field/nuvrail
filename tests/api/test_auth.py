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
from pathlib import Path

import httpx
import pytest

from api.routes import auth as auth_routes
from api.auth import get_auth_db_path
from api.main import app
from api.routes.operations import get_db_path
from gateway.state_db import get_db
from gateway.security_controls import AuthAbuseProtector
from gateway.state_db import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    """Initialise an isolated test DB and return its path."""
    path = tmp_path / "test_auth.db"
    await init_db(path)
    return path


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    """Return an AsyncClient wired to the app with the test DB injected.

    Both get_db_path (used by operation/audit/auth routes) and
    get_auth_db_path (used by get_current_user) are overridden so all
    DB access hits the same isolated test DB.
    """
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


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
    async with get_db(db_path) as db:
        async with db.execute(
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

    async with get_db(db_path) as db:
        async with db.execute(
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
            "upstream_user": "mmodahl@animalhorde.com",
            "oauth2_provider": "google",
            "oauth2_client_id": "fake_client_id",
            "oauth2_client_secret": "fake_client_secret",
            "oauth2_refresh_token": "fake_refresh_token",
            "label": "gmail-xoauth2-test",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["upstream_user"] == "mmodahl@animalhorde.com"
    assert "agent_token" in data

    # Verify OAuth2 fields are stored in DB (encrypted, not plaintext)
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT oauth2_provider, oauth2_client_id, oauth2_refresh_token, "
            "oauth2_client_secret, upstream_password "
            "FROM agent_credentials WHERE upstream_user = ?",
            ("mmodahl@animalhorde.com",),
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
            "upstream_user": "mmodahl@animalhorde.com",
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
            "upstream_user": "mmodahl@animalhorde.com",
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
