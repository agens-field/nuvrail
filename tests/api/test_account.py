"""
Tests for session management endpoints — token info + rotate (issue #28).

GET  /api/v1/account/token
POST /api/v1/account/token/rotate
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from api.auth import get_auth_db_path
from api.main import app
from api.routes.operations import get_db_path
from gateway.state_db import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_account.db"
    await init_db(path)
    return path


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# Counter for unique emails to avoid AuthAbuseProtector account-level rate limiting
_email_counter = 0


def _unique_email() -> str:
    global _email_counter
    _email_counter += 1
    return f"user{_email_counter}@example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_and_login(
    client: httpx.AsyncClient,
    email: str | None = None,
    password: str = "supersecurepass",
) -> tuple[str, str]:  # (token, email)
    if email is None:
        email = _unique_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "Test User"},
    )
    assert resp.status_code == 201
    resp2 = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp2.status_code == 200
    return resp2.json()["token"], email


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Issue #28 — Token info
# ---------------------------------------------------------------------------


async def test_get_token_info_returns_created_at(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /account/token returns created_at after login."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/token", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "created_at" in data
    assert data["created_at"] is not None
    assert isinstance(data["created_at"], int)


async def test_get_token_info_last_used_field_present(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """last_used_at field is present (may be null or int depending on throttle timing)."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/token", headers=_auth(token))
    assert resp.status_code == 200
    assert "last_used_at" in resp.json()


async def test_get_token_info_requires_auth(client: httpx.AsyncClient) -> None:
    """GET /account/token returns 401 without a token."""
    resp = await client.get("/api/v1/account/token")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Issue #28 — Token rotate
# ---------------------------------------------------------------------------


async def test_rotate_token_returns_new_token(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST /account/token/rotate returns a new token with expected shape."""
    token, _ = await _register_and_login(client)
    resp = await client.post("/api/v1/account/token/rotate", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["token_type"] == "bearer"
    assert data["token"] != token  # new token differs from old
    assert "user_id" in data
    assert "email" in data


async def test_rotate_token_invalidates_old_token(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """After rotation, the old token is rejected."""
    old_token, _ = await _register_and_login(client)
    resp = await client.post("/api/v1/account/token/rotate", headers=_auth(old_token))
    new_token = resp.json()["token"]

    # Old token should now be 401
    resp_old = await client.get("/api/v1/account/token", headers=_auth(old_token))
    assert resp_old.status_code == 401

    # New token should work
    resp_new = await client.get("/api/v1/account/token", headers=_auth(new_token))
    assert resp_new.status_code == 200


async def test_rotate_token_resets_created_at(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Rotated token has a fresh api_token_created_at and null last_used_at."""
    token, _ = await _register_and_login(client)
    info_before = (await client.get("/api/v1/account/token", headers=_auth(token))).json()

    resp = await client.post("/api/v1/account/token/rotate", headers=_auth(token))
    new_token = resp.json()["token"]

    info_after = (await client.get("/api/v1/account/token", headers=_auth(new_token))).json()
    assert info_after["created_at"] >= info_before["created_at"]
    assert info_after["last_used_at"] is None


async def test_rotate_token_requires_auth(client: httpx.AsyncClient) -> None:
    """POST /account/token/rotate returns 401 without a token."""
    resp = await client.post("/api/v1/account/token/rotate")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Issue #27 — Data export
# ---------------------------------------------------------------------------

from gateway.state_db import get_db  # noqa: E402


async def test_export_returns_json_attachment(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /account/export returns JSON with Content-Disposition attachment."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "nuvrail-export-" in cd


async def test_export_contains_expected_fields(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Export JSON contains all required top-level fields."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    data = resp.json()
    for field in ("exported_at", "account", "agents", "operations", "audit_log", "auto_approval_rules"):
        assert field in data, f"Missing field: {field}"


async def test_export_account_fields(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Export account block has email, display_name, created_at."""
    email = _unique_email()
    token, _ = await _register_and_login(client, email=email)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    account = resp.json()["account"]
    assert account["email"] == email
    assert account["display_name"] == "Test User"
    assert isinstance(account["created_at"], int)


async def test_export_no_credentials(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Export agents list does not contain credential values."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    data = resp.json()
    for agent in data["agents"]:
        for forbidden_key in ("hashed_token", "upstream_password", "oauth2_refresh_token",
                               "oauth2_client_secret", "oauth2_access_token"):
            assert forbidden_key not in agent, f"Credential field leaked: {forbidden_key}"


async def test_export_writes_audit_event(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Exporting writes an 'export_requested' audit log event."""
    token, _ = await _register_and_login(client)
    await client.get("/api/v1/account/export", headers=_auth(token))

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT event FROM audit_log WHERE event = 'export_requested'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["event"] == "export_requested"


async def test_export_requires_auth(client: httpx.AsyncClient) -> None:
    """GET /account/export returns 401 without a token."""
    resp = await client.get("/api/v1/account/export")
    assert resp.status_code == 401
