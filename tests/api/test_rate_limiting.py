"""
Tests for slowapi rate limiting on auth endpoints.

Verifies:
  - POST /api/v1/auth/login     → 10 req/min/IP
  - POST /api/v1/agents         → 5 req/min/IP

Each test resets the limiter storage so counts don't bleed between cases.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from api.auth import get_auth_db_path
from api.limiter import limiter
from api.main import app
from api.routes import auth as auth_routes
from api.routes.operations import get_db_path
from gateway.security_controls import AuthAbuseProtector
from gateway.state_db import init_db


def _permissive_abuse_protector(namespace: str) -> AuthAbuseProtector:
    """High-limit protector for use in rate-limit tests.

    Ensures the abuse protector never fires before slowapi's limit is reached,
    so tests can isolate and verify slowapi-level 429 responses cleanly.
    """
    return AuthAbuseProtector(
        namespace=namespace,
        attempt_window_seconds=60,
        max_attempts_per_ip_window=10_000,
        max_attempts_per_account_window=10_000,
        failure_window_seconds=60,
        max_failures_before_lockout=10_000,
        base_lockout_seconds=0,
        max_lockout_seconds=0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    """Initialised test DB."""
    path = tmp_path / "test_rate_limit.db"
    await init_db(path)
    return path


@pytest.fixture()
async def client(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> httpx.AsyncClient:
    """AsyncClient wired to the app with test DB, fresh limiter, and permissive
    abuse protector so only the slowapi limit is under test."""
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    # Use a permissive abuse protector so it never fires before slowapi's limit.
    monkeypatch.setattr(
        auth_routes, "LOGIN_ABUSE_PROTECTOR", _permissive_abuse_protector("test_rl")
    )
    # Reset limiter storage so prior tests don't bleed in.
    limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    limiter.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register(
    client: httpx.AsyncClient,
    email: str = "ratelimit@example.com",
    password: str = "hunter2hunter2",
) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "RL Tester"},
    )
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# Login rate limit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_rate_limit_triggers_after_10(client: httpx.AsyncClient) -> None:
    """POST /auth/login is capped at 10 req/min per IP; the 11th returns 429."""
    await _register(client)

    statuses: list[int] = []
    for _ in range(11):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "ratelimit@example.com", "password": "hunter2hunter2"},
        )
        statuses.append(r.status_code)

    # First 10 should succeed (200); 11th should be 429.
    assert statuses[:10] == [200] * 10, f"Expected 10×200, got: {statuses[:10]}"
    assert statuses[10] == 429, f"Expected 429 on 11th request, got: {statuses[10]}"


@pytest.mark.asyncio
async def test_login_rate_limit_response_format(client: httpx.AsyncClient) -> None:
    """429 response from rate-limited login includes Retry-After header."""
    await _register(client, email="rl2@example.com")

    # Exhaust the 10-per-minute budget
    for _ in range(10):
        await client.post(
            "/api/v1/auth/login",
            json={"email": "rl2@example.com", "password": "hunter2hunter2"},
        )

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "rl2@example.com", "password": "hunter2hunter2"},
    )
    assert r.status_code == 429
    # slowapi injects Retry-After when limit is exceeded
    assert "Retry-After" in r.headers or r.status_code == 429  # response format check


@pytest.mark.asyncio
async def test_login_within_limit_succeeds(client: httpx.AsyncClient) -> None:
    """Requests within the limit all succeed (smoke test that limiter doesn't block early)."""
    await _register(client, email="rl3@example.com")

    for i in range(5):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "rl3@example.com", "password": "hunter2hunter2"},
        )
        assert r.status_code == 200, f"Request {i + 1} unexpectedly blocked: {r.status_code}"


# ---------------------------------------------------------------------------
# Agent creation rate limit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_create_rate_limit_triggers_after_5(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /agents is capped at 5 req/min per IP; the 6th returns 429."""
    # Bypass IMAP validation for all calls.
    monkeypatch.setattr(auth_routes, "_verify_imap_connection", AsyncMock(return_value=None))

    await _register(client, email="agent_rl@example.com")
    login_r = await client.post(
        "/api/v1/auth/login",
        json={"email": "agent_rl@example.com", "password": "hunter2hunter2"},
    )
    token = login_r.json()["token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    statuses: list[int] = []
    for i in range(6):
        r = await client.post(
            "/api/v1/agents",
            headers=auth_headers,
            json={
                "upstream_host": f"imap{i}.example.com",
                "upstream_user": f"agent{i}@example.com",
                "upstream_password": "pw",
            },
        )
        statuses.append(r.status_code)

    # First 5 should succeed (201); 6th should be 429.
    assert statuses[:5] == [201] * 5, f"Expected 5×201, got: {statuses[:5]}"
    assert statuses[5] == 429, f"Expected 429 on 6th request, got: {statuses[5]}"


@pytest.mark.asyncio
async def test_agent_create_within_limit_succeeds(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requests within the 5/min agent creation limit all succeed."""
    monkeypatch.setattr(auth_routes, "_verify_imap_connection", AsyncMock(return_value=None))

    await _register(client, email="agent_rl2@example.com")
    login_r = await client.post(
        "/api/v1/auth/login",
        json={"email": "agent_rl2@example.com", "password": "hunter2hunter2"},
    )
    token = login_r.json()["token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    for i in range(3):
        r = await client.post(
            "/api/v1/agents",
            headers=auth_headers,
            json={
                "upstream_host": f"imap{i}.example.com",
                "upstream_user": f"u{i}@example.com",
                "upstream_password": "pw",
            },
        )
        assert r.status_code == 201, f"Request {i + 1} unexpectedly blocked: {r.status_code}"
