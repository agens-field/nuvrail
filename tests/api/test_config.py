"""Tests for the public deployment-config endpoint.

    GET /api/v1/config -> {"signup_mode": closed|invite|open}

Two things matter here:
  1. The endpoint is UNAUTHENTICATED (the web app fetches it pre-login).
  2. It reports the SAME fail-closed mode that /auth/register enforces, so the
     UI and the enforcement can't drift. We assert all three modes plus the
     fail-closed degrade of an unknown mode.

The api-suite autouse fixture (conftest) defaults the box to open signup; each
test here monkeypatches the env to the mode under test (closest scope wins).
No auth header is sent — a 200 with the body proves the route is public.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from api.main import app
from api.routes.operations import get_db_path


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    """AsyncClient with the DB injected but NO auth override.

    /config takes no auth, so we deliberately do not override get_current_user;
    a successful call with no bearer token proves the endpoint is public.
    """
    app.dependency_overrides[get_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize("mode", ["closed", "invite"])
async def test_config_reports_non_open_mode(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", mode)
    monkeypatch.delenv("NUVRAIL_ALLOW_OPEN_SIGNUP", raising=False)
    resp = await client.get("/api/v1/config")
    assert resp.status_code == 200
    assert resp.json() == {"signup_mode": mode}


async def test_config_reports_open_mode(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "open")
    monkeypatch.setenv("NUVRAIL_ALLOW_OPEN_SIGNUP", "1")
    resp = await client.get("/api/v1/config")
    assert resp.status_code == 200
    assert resp.json() == {"signup_mode": "open"}


async def test_config_is_unauthenticated(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bearer token, no cookie — the endpoint still answers 200."""
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "closed")
    resp = await client.get("/api/v1/config")  # no auth header at all
    assert resp.status_code == 200
    assert "signup_mode" in resp.json()


@pytest.mark.parametrize("raw", ["", "garbage", "OPENish"])
async def test_config_fails_closed_on_unknown_mode(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """An unset/unknown mode must advertise the SAFE state (closed), matching
    the fail-closed resolution the register route enforces."""
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", raw)
    monkeypatch.delenv("NUVRAIL_ALLOW_OPEN_SIGNUP", raising=False)
    resp = await client.get("/api/v1/config")
    assert resp.status_code == 200
    assert resp.json() == {"signup_mode": "closed"}
