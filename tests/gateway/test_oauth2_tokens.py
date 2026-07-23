"""
Tests for gateway/oauth2_tokens.py

Covers:
  - _build_xoauth2_string: correct base64 encoding
  - get_xoauth2_string: cache hit (no refresh), cache miss (refresh called),
    OAuth2Error on missing provider/credentials
  - _refresh_google_token: success path via httpx mock, HTTP error path
  - _refresh_microsoft_token: success path, HTTP error path, scope echoed
  - get_access_token: dispatches to the right provider refresh (google/microsoft)
  - DB schema: oauth2 columns present after init_db()
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.credentials import encrypt_credential
from gateway.oauth2_tokens import (
    OAuth2Error,
    _access_token_cache,
    _build_xoauth2_string,
    _refresh_google_token,
    _refresh_microsoft_token,
    clear_access_token_cache,
    get_access_token,
    get_xoauth2_string,
)
from gateway.state_db import get_db, init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """The access-token cache is process-global; reset it around each test."""
    clear_access_token_cache()
    yield
    clear_access_token_cache()


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "oauth2_test.db"
    await init_db(path)
    return path


async def _insert_agent(
    db_path: Path,
    *,
    oauth2_provider: str | None = None,
    oauth2_refresh_token_plain: str | None = None,
    oauth2_client_id: str | None = None,
    oauth2_client_secret_plain: str | None = None,
    oauth2_access_token_plain: str | None = None,
    oauth2_access_token_expires_at: int | None = None,
    upstream_user: str = "user@example.com",
) -> int:
    """Insert a minimal agent_credentials row and return its id."""
    async with get_db(db_path) as db:
        # Need a parent user first
        async with db.execute(
            "SELECT id FROM users LIMIT 1"
        ) as cur:
            user_row = await cur.fetchone()
        if user_row is None:
            cur2 = await db.execute(
                "INSERT INTO users (email, hashed_password, created_at) VALUES (?,?,?)",
                ("test@example.com", "fakehash", int(time.time())),
            )
            user_id = cur2.lastrowid
        else:
            user_id = user_row["id"]

        cur3 = await db.execute(
            """
            INSERT INTO agent_credentials
                (user_id, label, agent_username, hashed_token,
                 upstream_host, upstream_user, upstream_password,
                 oauth2_provider, oauth2_refresh_token, oauth2_client_id,
                 oauth2_client_secret, oauth2_access_token,
                 oauth2_access_token_expires_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                "test",
                "nuvrail_test01",
                "fakehash",
                "imap.gmail.com",
                upstream_user,
                encrypt_credential("fake_password"),
                oauth2_provider,
                encrypt_credential(oauth2_refresh_token_plain) if oauth2_refresh_token_plain else None,
                oauth2_client_id,
                encrypt_credential(oauth2_client_secret_plain) if oauth2_client_secret_plain else None,
                encrypt_credential(oauth2_access_token_plain) if oauth2_access_token_plain else None,
                oauth2_access_token_expires_at,
                int(time.time()),
            ),
        )
        await db.commit()
        return cur3.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# _build_xoauth2_string
# ---------------------------------------------------------------------------


def test_build_xoauth2_string_format() -> None:
    """XOAUTH2 string must be base64(user=<email>\\x01auth=Bearer <token>\\x01\\x01)."""
    result = _build_xoauth2_string("user@example.com", "ya29.token")
    decoded = base64.b64decode(result).decode("ascii")
    assert decoded == "user=user@example.com\x01auth=Bearer ya29.token\x01\x01"


# ---------------------------------------------------------------------------
# get_xoauth2_string — error cases
# ---------------------------------------------------------------------------


async def test_get_xoauth2_string_no_provider(db_path: Path) -> None:
    """Raises OAuth2Error if agent has no oauth2_provider."""
    agent_id = await _insert_agent(db_path, oauth2_provider=None)
    with pytest.raises(OAuth2Error, match="oauth2_provider"):
        await get_xoauth2_string(str(agent_id), db_path)


async def test_get_xoauth2_string_missing_agent(db_path: Path) -> None:
    """Raises OAuth2Error for unknown agent_id."""
    with pytest.raises(OAuth2Error, match="not found"):
        await get_xoauth2_string("9999", db_path)


async def test_get_xoauth2_string_no_refresh_token(db_path: Path) -> None:
    """Raises OAuth2Error if refresh token not configured."""
    agent_id = await _insert_agent(
        db_path,
        oauth2_provider="google",
        oauth2_client_id="client_id",
        oauth2_client_secret_plain="secret",
    )
    with pytest.raises(OAuth2Error, match="refresh_token"):
        await get_xoauth2_string(str(agent_id), db_path)


# ---------------------------------------------------------------------------
# get_xoauth2_string — cache hit
# ---------------------------------------------------------------------------


async def test_get_xoauth2_string_cache_hit(db_path: Path) -> None:
    """Returns the in-memory cached token without calling refresh when fresh."""
    future_expiry = float(int(time.time()) + 3600)  # 1 hour from now
    agent_id = await _insert_agent(
        db_path,
        oauth2_provider="google",
        oauth2_refresh_token_plain="refresh_tok",
        oauth2_client_id="client_id",
        oauth2_client_secret_plain="secret",
    )
    # Seed the process-local cache as if a previous refresh had populated it.
    _access_token_cache[str(agent_id)] = (
        "user@example.com",
        "cached_access_token",
        future_expiry,
    )

    with patch("gateway.oauth2_tokens._refresh_google_token") as mock_refresh:
        result = await get_xoauth2_string(str(agent_id), db_path)

    mock_refresh.assert_not_called()
    decoded = base64.b64decode(result).decode("ascii")
    assert "Bearer cached_access_token" in decoded


# ---------------------------------------------------------------------------
# get_xoauth2_string — cache miss / refresh
# ---------------------------------------------------------------------------


async def test_get_xoauth2_string_cache_miss_refreshes(db_path: Path) -> None:
    """Calls refresh and caches the new token in memory when none is cached."""
    agent_id = await _insert_agent(
        db_path,
        oauth2_provider="google",
        oauth2_refresh_token_plain="refresh_tok",
        oauth2_client_id="client_id",
        oauth2_client_secret_plain="secret",
    )
    new_expires = int(time.time()) + 3600

    with patch(
        "gateway.oauth2_tokens._refresh_google_token",
        new_callable=AsyncMock,
        return_value=("new_access_token", new_expires),
    ):
        result = await get_xoauth2_string(str(agent_id), db_path)

    decoded = base64.b64decode(result).decode("ascii")
    assert "Bearer new_access_token" in decoded

    # The new token is cached in memory (not persisted to the DB).
    cached = _access_token_cache.get(str(agent_id))
    assert cached is not None
    assert cached[1] == "new_access_token"
    assert cached[2] == float(new_expires)


async def test_get_xoauth2_string_second_call_uses_cache(db_path: Path) -> None:
    """A refresh populates the cache; the next call must not refresh again."""
    agent_id = await _insert_agent(
        db_path,
        oauth2_provider="google",
        oauth2_refresh_token_plain="refresh_tok",
        oauth2_client_id="client_id",
        oauth2_client_secret_plain="secret",
    )
    new_expires = int(time.time()) + 3600

    with patch(
        "gateway.oauth2_tokens._refresh_google_token",
        new_callable=AsyncMock,
        return_value=("new_access_token", new_expires),
    ) as mock_refresh:
        await get_xoauth2_string(str(agent_id), db_path)
        await get_xoauth2_string(str(agent_id), db_path)

    mock_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# _refresh_google_token — httpx path
# ---------------------------------------------------------------------------


async def test_refresh_google_token_success() -> None:
    """Returns (access_token, expires_at) on HTTP 200."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = json.dumps({
        "access_token": "ya29.new",
        "expires_in": 3600,
    })

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        token, expires_at = await _refresh_google_token("cid", "csecret", "refresh")

    assert token == "ya29.new"
    assert expires_at > int(time.time())


async def test_refresh_google_token_http_error() -> None:
    """Raises OAuth2Error on non-200 HTTP response."""
    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.text = json.dumps({"error": "invalid_grant"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(OAuth2Error, match="HTTP 400"):
            await _refresh_google_token("cid", "csecret", "refresh")


# ---------------------------------------------------------------------------
# _refresh_microsoft_token — httpx path (Outlook/O365, Azure AD)
# ---------------------------------------------------------------------------


async def test_refresh_microsoft_token_success() -> None:
    """Returns (access_token, expires_at) on HTTP 200."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.text = json.dumps({
        "access_token": "eyJ0.ms.new",
        "expires_in": 3600,
    })

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        token, expires_at = await _refresh_microsoft_token("cid", "csecret", "refresh")

    assert token == "eyJ0.ms.new"
    assert expires_at > int(time.time())
    # Microsoft requires the scope to be echoed on the refresh grant.
    _, kwargs = mock_client.post.call_args
    assert "scope" in kwargs["data"]
    assert "offline_access" in kwargs["data"]["scope"]
    assert kwargs["data"]["grant_type"] == "refresh_token"


async def test_refresh_microsoft_token_http_error() -> None:
    """Raises OAuth2Error on non-200 HTTP response, surfacing the error code."""
    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.text = json.dumps({"error": "invalid_grant"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(OAuth2Error, match="invalid_grant"):
            await _refresh_microsoft_token("cid", "csecret", "refresh")


async def test_get_access_token_dispatches_microsoft(db_path: Path) -> None:
    """A microsoft-provider agent refreshes via the Microsoft token path."""
    agent_id = await _insert_agent(
        db_path,
        oauth2_provider="microsoft",
        oauth2_refresh_token_plain="ms-refresh",
        oauth2_client_id="ms-cid",
        oauth2_client_secret_plain="ms-secret",
        upstream_user="user@outlook.com",
    )
    with patch(
        "gateway.oauth2_tokens._refresh_microsoft_token",
        new=AsyncMock(return_value=("ms-access", int(time.time()) + 3600)),
    ) as mock_ms, patch(
        "gateway.oauth2_tokens._refresh_google_token",
        new=AsyncMock(side_effect=AssertionError("google path must not run")),
    ):
        email, token = await get_access_token(agent_id, db_path)

    assert email == "user@outlook.com"
    assert token == "ms-access"
    mock_ms.assert_awaited_once()


# ---------------------------------------------------------------------------
# revoke_google_refresh_token — best-effort grant revocation on deletion (#72)
# ---------------------------------------------------------------------------
from gateway.oauth2_tokens import revoke_google_refresh_token  # noqa: E402


def _mock_httpx(status_code: int):
    fake_resp = MagicMock()
    fake_resp.status_code = status_code
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)
    return mock_client


async def test_revoke_returns_true_on_200() -> None:
    with patch("httpx.AsyncClient", return_value=_mock_httpx(200)):
        assert await revoke_google_refresh_token("refresh") is True


async def test_revoke_returns_false_on_non_200() -> None:
    # A bad token / already-revoked etc. must NOT raise — deletion proceeds.
    with patch("httpx.AsyncClient", return_value=_mock_httpx(400)):
        assert await revoke_google_refresh_token("refresh") is False


async def test_revoke_returns_false_on_exception() -> None:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=RuntimeError("network down"))
    with patch("httpx.AsyncClient", return_value=mock_client):
        assert await revoke_google_refresh_token("refresh") is False


async def test_revoke_empty_token_is_noop_false() -> None:
    # No network call for an empty token.
    with patch("httpx.AsyncClient", side_effect=AssertionError("should not call")):
        assert await revoke_google_refresh_token("") is False


# ---------------------------------------------------------------------------
# DB schema: oauth2 columns present after init_db
# ---------------------------------------------------------------------------


async def test_oauth2_columns_in_schema(db_path: Path) -> None:
    """All oauth2 columns must exist in agent_credentials after init_db."""
    async with get_db(db_path) as db, db.execute("PRAGMA table_info(agent_credentials)") as cur:
        cols = {row["name"] for row in await cur.fetchall()}

    expected = {
        "oauth2_provider",
        "oauth2_refresh_token",
        "oauth2_client_id",
        "oauth2_client_secret",
        "oauth2_access_token",
        "oauth2_access_token_expires_at",
    }
    assert expected <= cols, f"Missing columns: {expected - cols}"
