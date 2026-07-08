"""
Tests for OAuth2 web flow endpoints (provider-parameterized).

GET /api/v1/oauth2/{provider}/start
GET /api/v1/oauth2/{provider}/callback
GET /api/v1/oauth2/{provider}/result

Covers Google (Gmail) and Microsoft (Outlook/O365 via Azure AD), plus the
unknown-provider guard.

Uses httpx.AsyncClient + tmp_path DB, same pattern as test_auth.py.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from api.routes import oauth2 as oauth2_routes

# db_path and client fixtures are provided by tests/api/conftest.py.


@pytest.fixture(autouse=True)
def clear_oauth2_state():
    """Wipe module-level _pending / _results between tests."""
    oauth2_routes._pending.clear()
    oauth2_routes._results.clear()
    yield
    oauth2_routes._pending.clear()
    oauth2_routes._results.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_and_login(
    client: httpx.AsyncClient,
    email: str = "oauth2test@example.com",
    password: str = "hunter2",
) -> str:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201
    resp2 = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp2.status_code == 200
    return resp2.json()["token"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_start_returns_503_when_env_not_configured(
    client: httpx.AsyncClient,
) -> None:
    """/start returns 503 when GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are absent."""
    token = await _register_and_login(client, email="noenv@example.com")

    with patch.dict("os.environ", {}, clear=False):
        # Ensure the vars are absent even if set in the outer environment.
        import os
        env_backup = {}
        for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
            env_backup[key] = os.environ.pop(key, None)

        try:
            resp = await client.get(
                "/api/v1/oauth2/google/start",
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            for key, val in env_backup.items():
                if val is not None:
                    os.environ[key] = val

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "oauth2_not_configured"


@pytest.mark.asyncio(loop_scope="session")
async def test_start_returns_auth_url_and_state_when_configured(
    client: httpx.AsyncClient,
) -> None:
    """/start returns {auth_url, state} when env vars are set."""
    token = await _register_and_login(client, email="configured@example.com")

    with patch.dict(
        "os.environ",
        {"GOOGLE_CLIENT_ID": "fake_client_id", "GOOGLE_CLIENT_SECRET": "fake_secret"},
    ):
        resp = await client.get(
            "/api/v1/oauth2/google/start?label=My+Gmail",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "auth_url" in body
    assert "state" in body
    assert "accounts.google.com" in body["auth_url"]
    assert "fake_client_id" in body["auth_url"]
    assert body["state"] in body["auth_url"]
    # State was stored in _pending
    assert body["state"] in oauth2_routes._pending


@pytest.mark.asyncio(loop_scope="session")
async def test_result_returns_404_for_unknown_state(
    client: httpx.AsyncClient,
) -> None:
    """/result returns 404 for a state that was never created."""
    token = await _register_and_login(client, email="unknown@example.com")

    resp = await client.get(
        "/api/v1/oauth2/google/result?state=doesnotexist",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"] == "oauth2_state_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_result_returns_404_for_expired_state(
    client: httpx.AsyncClient,
) -> None:
    """/result returns 404 when the state was created but the flow never completed."""
    token = await _register_and_login(client, email="expired@example.com")

    # Manually insert an expired pending entry (simulates /start ran > 300s ago,
    # callback never arrived, result was never stored).
    stale_state = "stale_" + "x" * 59
    oauth2_routes._pending[stale_state] = {
        "user_id": 999,
        "label": "stale",
        "created_at": 0,  # epoch — definitely expired
    }
    # _results has no entry for this state.

    resp = await client.get(
        f"/api/v1/oauth2/google/result?state={stale_state}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"] == "oauth2_state_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_result_not_returned_to_other_user(client: httpx.AsyncClient) -> None:
    """A user must not be able to read another user's OAuth2 result via its state.

    Even if the high-entropy state value leaks (e.g. via URL/referrer), polling
    /result as a different user returns 404 and does NOT consume the result, so
    the legitimate owner can still retrieve their freshly minted agent token.
    """
    reg_a = await client.post(
        "/api/v1/auth/register",
        json={"email": "owner-a@example.com", "password": "hunter2pass!"},
    )
    assert reg_a.status_code == 201
    token_a = reg_a.json()["token"]
    uid_a = reg_a.json()["user_id"]

    token_b = await _register_and_login(client, email="attacker-b@example.com")

    state = "s_" + "x" * 60
    oauth2_routes._results[state] = {
        "user_id": uid_a,
        "agent_username": "nuvrail_secret",
        "agent_token": "super-secret-token",
        "label": "gmail",
        "upstream_user": "owner-a@gmail.com",
    }

    # Attacker (user B) polls with the leaked state → 404, result preserved.
    resp_b = await client.get(
        f"/api/v1/oauth2/google/result?state={state}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_b.status_code == 404
    assert resp_b.json()["error"] == "oauth2_state_not_found"
    assert state in oauth2_routes._results, "result must not be consumed by a non-owner"

    # Legitimate owner (user A) polls → 200 with credentials, internal user_id stripped.
    resp_a = await client.get(
        f"/api/v1/oauth2/google/result?state={state}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_a.status_code == 200
    body = resp_a.json()
    assert body["agent_username"] == "nuvrail_secret"
    assert body["agent_token"] == "super-secret-token"
    assert "user_id" not in body, "internal user_id must not leak in the response"
    assert state not in oauth2_routes._results, "owner read must consume the one-time result"


@pytest.mark.asyncio(loop_scope="session")
async def test_callback_with_invalid_state_redirects_with_error(
    client: httpx.AsyncClient,
) -> None:
    """/callback with unknown state redirects to SPA with error=invalid_state."""
    resp = await client.get(
        "/api/v1/oauth2/google/callback?code=someauthcode&state=bad_state_xyz",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "/#/oauth2/callback" in location
    assert "error=invalid_state" in location
    assert "bad_state_xyz" in location


# ---------------------------------------------------------------------------
# Provider parameterization: Microsoft (Outlook/O365) + unknown provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_microsoft_start_returns_auth_url_when_configured(
    client: httpx.AsyncClient,
) -> None:
    """/microsoft/start returns an Azure AD auth_url when MS env vars are set."""
    token = await _register_and_login(client, email="msuser@example.com")

    with patch.dict(
        "os.environ",
        {"MICROSOFT_CLIENT_ID": "ms_client_id", "MICROSOFT_CLIENT_SECRET": "ms_secret"},
    ):
        resp = await client.get(
            "/api/v1/oauth2/microsoft/start?label=My+Outlook",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "login.microsoftonline.com" in body["auth_url"]
    assert "ms_client_id" in body["auth_url"]
    assert body["state"] in body["auth_url"]
    # State records the provider so the callback won't cross wires.
    assert oauth2_routes._pending[body["state"]]["provider"] == "microsoft"


@pytest.mark.asyncio(loop_scope="session")
async def test_microsoft_start_503_when_env_not_configured(
    client: httpx.AsyncClient,
) -> None:
    """/microsoft/start reports the Microsoft env var names when unset."""
    import os

    token = await _register_and_login(client, email="msnoenv@example.com")
    env_backup = {}
    for key in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET"):
        env_backup[key] = os.environ.pop(key, None)
    try:
        resp = await client.get(
            "/api/v1/oauth2/microsoft/start",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        for key, val in env_backup.items():
            if val is not None:
                os.environ[key] = val

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "oauth2_not_configured"
    assert "MICROSOFT_CLIENT_ID" in body["detail"]


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_provider_start_returns_404(
    client: httpx.AsyncClient,
) -> None:
    """/oauth2/<bogus>/start returns a clear unknown-provider 404."""
    token = await _register_and_login(client, email="bogusprov@example.com")
    resp = await client.get(
        "/api/v1/oauth2/yahoo/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "oauth2_unknown_provider"


@pytest.mark.asyncio(loop_scope="session")
async def test_callback_provider_mismatch_is_invalid_state(
    client: httpx.AsyncClient,
) -> None:
    """A state minted for google, replayed on the microsoft callback, is refused.

    Guards against crossing a google flow's state into a microsoft callback
    (or vice versa) — the callback must honor the provider the state was for.
    """
    import time as _time

    state = "mismatch_" + "x" * 55
    oauth2_routes._pending[state] = {
        "user_id": 12345,
        "label": "gmail",
        "provider": "google",
        "created_at": _time.time(),
    }
    resp = await client.get(
        f"/api/v1/oauth2/microsoft/callback?code=abc&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=invalid_state" in resp.headers["location"]
    # The mismatched state was consumed (popped) rather than left dangling.
    assert state not in oauth2_routes._pending
