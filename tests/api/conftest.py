"""
Shared fixtures for the api test suite.

Provides the common DB + HTTP-client fixtures used across the suite, plus an
autouse reset of the in-memory rate-limit / abuse-protector state so counts
from one test don't bleed into the next.

Modules that need different behaviour override these locally (closest scope
wins): e.g. test_operations / test_rules / test_audit define a `client` that
also overrides get_current_user to bypass auth, and test_audit / test_agent_audit
use a `db_path` that pre-seeds users and agents.
"""
from pathlib import Path

import httpx
import pytest

from api.auth import get_auth_db_path
from api.limiter import limiter
from api.main import app
from api.routes.auth import LOGIN_ABUSE_PROTECTOR
from api.routes.operations import get_db_path
from gateway.state_db import init_db


@pytest.fixture(autouse=True)
def reset_rate_limiters() -> None:
    """Clear all in-memory rate-limit storage before each test."""
    limiter.reset()
    LOGIN_ABUSE_PROTECTOR.reset()


@pytest.fixture(autouse=True)
def _default_open_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the whole api suite to an OPEN-signup deployment.

    Production now defaults to NUVRAIL_SIGNUP_MODE=closed (fail-closed), which
    would 403 the many existing tests that exercise /auth/register mechanics.
    Those tests are about registration behaviour, not the gating policy, so we
    model the test box as an operator who has explicitly opted into open
    signup. The dedicated gating tests (test_signup_gating.py) monkeypatch this
    per-test to exercise closed / invite / open-without-ack.
    """
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "open")
    monkeypatch.setenv("NUVRAIL_ALLOW_OPEN_SIGNUP", "1")


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    """Return the path to a freshly initialised, isolated SQLite DB."""
    path = tmp_path / "test_api.db"
    await init_db(path)
    return path


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    """AsyncClient wired to the app with the test DB injected (real auth).

    Both get_db_path (routes) and get_auth_db_path (get_current_user) point at
    the same isolated DB, so callers authenticate with a real bearer token.
    Modules that bypass auth override this fixture locally.
    """
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
