"""
OAuth2 token manager for upstream XOAUTH2 authentication.

Handles: refresh token → access token exchange, in-memory caching,
expiry-aware lookup (refresh if expires_at - now < 120s).

Access tokens are cached in a process-local dict (never persisted) so a
secret-manager backend isn't churned with a new secret version every hour.
The long-lived refresh token / client secret are resolved on demand via
gateway.credentials.fetch_credential (which may hit AWS/GCP secret managers).

Supported providers:
  - google: token endpoint https://oauth2.googleapis.com/token

The XOAUTH2 string format (base64-encoded):
  user=<email>\x01auth=Bearer <access_token>\x01\x01

Usage:
  xoauth2 = await get_xoauth2_string(agent_id, db_path)
  # → "dXNlcj1...base64..." — pass directly to IMAP AUTHENTICATE XOAUTH2
  #                           or SMTP AUTH XOAUTH2

Data flow:
  get_xoauth2_string()
       │
       ├─ load agent_credentials row from DB
       │    (oauth2_provider, oauth2_refresh_token, oauth2_client_id,
       │     oauth2_client_secret, oauth2_access_token,
       │     oauth2_access_token_expires_at)
       │
       ├─ if cached access token is valid (expires_at - now > 120s)
       │    └─ build XOAUTH2 string from in-memory cache → return
       │
       └─ else: call _refresh_google_token()
                 │
                 ├─ POST https://oauth2.googleapis.com/token
                 │    grant_type=refresh_token
                 │
                 └─ store (access_token, expires_at) in process-local cache
                      └─ build XOAUTH2 string → return
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

from gateway.credentials import fetch_credential
from gateway.state_db import get_db

_GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Refresh the cached token if it expires within this many seconds.
_EXPIRY_BUFFER_SECONDS = 120

# Process-local cache of short-lived access tokens, keyed by agent id.
#   agent_id -> (email, access_token, expires_at_epoch_seconds)
# Access tokens are derived from the refresh token and live ~1h, so we keep
# them in memory only — never persisted — to avoid churning the secret store
# (or the DB) on every refresh. Worst case, each process refreshes once per
# hour per active agent.
_access_token_cache: dict[str, tuple[str, str, float]] = {}

# Per-agent locks so concurrent connections for the same agent trigger at most
# one refresh (single-flight) instead of a thundering herd.
_refresh_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


class OAuth2Error(Exception):
    """Raised on OAuth2 token exchange failures."""


async def _refresh_lock_for(agent_id: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _refresh_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            _refresh_locks[agent_id] = lock
        return lock


def clear_access_token_cache() -> None:
    """Drop all cached access tokens. For tests and forced re-auth."""
    _access_token_cache.clear()


async def get_access_token(agent_id: str, db_path: Path) -> tuple[str, str]:
    """Return (email, access_token) for the given agent.

    Checks the cached access token first; refreshes from the provider
    if absent or expiring within _EXPIRY_BUFFER_SECONDS.

    Raises OAuth2Error on any failure (missing credentials, HTTP error,
    bad response).  Never logs access tokens.

    Callers that need the full XOAUTH2-encoded string (e.g. the IMAP/SMTP
    proxy raw protocol layer) should use get_xoauth2_string() instead.
    Callers with library support for XOAUTH2 (e.g. aiosmtplib.auth_xoauth2)
    should call this directly to avoid double-encoding.
    """
    # Fast path: serve a still-fresh access token from the in-memory cache.
    cached = _access_token_cache.get(agent_id)
    if cached is not None and (cached[2] - time.time()) > _EXPIRY_BUFFER_SECONDS:
        return cached[0], cached[1]

    # Slow path: refresh, guarded per-agent so concurrent callers refresh once.
    lock = await _refresh_lock_for(agent_id)
    async with lock:
        # Re-check under the lock — another caller may have just refreshed.
        cached = _access_token_cache.get(agent_id)
        if cached is not None and (cached[2] - time.time()) > _EXPIRY_BUFFER_SECONDS:
            return cached[0], cached[1]

        async with get_db(db_path) as db:
            async with db.execute(
                """
                SELECT upstream_user,
                       oauth2_provider,
                       oauth2_refresh_token,
                       oauth2_client_id,
                       oauth2_client_secret
                FROM agent_credentials
                WHERE id = ?
                """,
                (agent_id,),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            raise OAuth2Error(f"Agent credential {agent_id!r} not found")

        row = dict(row)
        provider = row.get("oauth2_provider")
        if not provider:
            raise OAuth2Error(f"Agent {agent_id!r} has no oauth2_provider configured")

        email = row["upstream_user"]
        refresh_token_enc = row.get("oauth2_refresh_token")
        client_id = row.get("oauth2_client_id")
        client_secret_enc = row.get("oauth2_client_secret")

        if not refresh_token_enc:
            raise OAuth2Error(f"Agent {agent_id!r}: oauth2_refresh_token not configured")
        if not client_id:
            raise OAuth2Error(f"Agent {agent_id!r}: oauth2_client_id not configured")
        if not client_secret_enc:
            raise OAuth2Error(f"Agent {agent_id!r}: oauth2_client_secret not configured")

        # Resolve the long-lived secrets from the configured backend (Secrets
        # Manager / GCP Secret Manager / local AES). These resolutions are
        # themselves cached by the secret store's short-TTL layer.
        refresh_token = await fetch_credential(refresh_token_enc)
        client_secret = await fetch_credential(client_secret_enc)

        if provider == "google":
            access_token, new_expires_at = await _refresh_google_token(
                client_id, client_secret, refresh_token
            )
        else:
            raise OAuth2Error(f"Unsupported oauth2_provider: {provider!r}")

        # Cache the new access token in memory only — never persisted.
        _access_token_cache[agent_id] = (email, access_token, float(new_expires_at))
        return email, access_token


async def get_xoauth2_string(agent_id: str, db_path: Path) -> str:
    """Return the base64-encoded XOAUTH2 string for the given agent.

    Wraps get_access_token() and encodes the result for use in the raw
    IMAP/SMTP AUTHENTICATE/AUTH XOAUTH2 command.
    """
    email, access_token = await get_access_token(agent_id, db_path)
    return _build_xoauth2_string(email, access_token)


def _build_xoauth2_string(email: str, access_token: str) -> str:
    """Build the base64-encoded XOAUTH2 bearer string.

    Format: base64("user=<email>\x01auth=Bearer <access_token>\x01\x01")
    """
    raw = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode("ascii")).decode("ascii")


async def _refresh_google_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> tuple[str, int]:
    """Exchange a Google refresh token for a new access token.

    Calls https://oauth2.googleapis.com/token via httpx (preferred) or
    urllib.request (fallback).  Returns (access_token, expires_at_unix).

    Raises OAuth2Error on HTTP errors or unexpected response shapes.
    Never logs the token values.
    """
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        import httpx  # noqa: PLC0415
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_GOOGLE_TOKEN_ENDPOINT, data=payload)
        status_code = resp.status_code
        body = resp.text
    except ImportError:
        # httpx not available — fall back to urllib in a thread executor
        status_code, body = await asyncio.get_event_loop().run_in_executor(
            None, _urllib_post, _GOOGLE_TOKEN_ENDPOINT, payload
        )

    if status_code != 200:
        # Extract just the error code from Google's JSON response — safe to log,
        # contains no credential material (e.g. "invalid_grant", "invalid_client").
        error_code = "unknown"
        error_desc = ""
        try:
            err_data = json.loads(body)
            error_code = err_data.get("error", "unknown")
            error_desc = err_data.get("error_description", "")
        except json.JSONDecodeError:
            pass
        raise OAuth2Error(
            f"Google token refresh failed: HTTP {status_code} "
            f"error={error_code!r} description={error_desc!r}"
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OAuth2Error(f"Google token endpoint returned non-JSON: {exc}") from exc

    access_token = data.get("access_token")
    if not access_token:
        raise OAuth2Error(
            "Google token endpoint response missing 'access_token' "
            "(details redacted)"
        )

    expires_in = int(data.get("expires_in", 3600))
    expires_at = int(time.time()) + expires_in

    return access_token, expires_at


def _urllib_post(url: str, payload: dict) -> tuple[int, str]:
    """Synchronous POST via urllib — used as fallback when httpx is absent."""
    import urllib.error  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    encoded = urllib.parse.urlencode(payload).encode("ascii")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        return exc.code, exc.read().decode("utf-8", errors="replace")
