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
