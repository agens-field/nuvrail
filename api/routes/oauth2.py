"""
OAuth2 web flow endpoints — Gmail XOAUTH2 in-browser setup.

Flow:
  1. GET /api/v1/oauth2/google/start  (authenticated)
       Generates state nonce, returns {auth_url, state}.
       Browser navigates to auth_url (Google consent).

  2. GET /api/v1/oauth2/google/callback  (unauthenticated — called by Google)
       Exchanges code → refresh_token + access_token.
       Creates agent_credentials row.
       Redirects browser to /#/oauth2/callback?state=<state>.

  3. GET /api/v1/oauth2/google/result  (authenticated)
       Frontend polls for completed result (one-time, clears on read).

State lifecycle:
  _pending[state] = {user_id, label, created_at}   (max _STATE_TTL seconds)
  _results[state] = {agent_username, agent_token, label, upstream_user}
                  | {error: str}                    (cleared on first read)

Phase 2 note: _pending / _results are in-process dicts (single worker).
Phase 3: migrate to DB if/when multi-worker deployment is needed.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from api.auth import get_current_user, hash_agent_token
from api.routes.operations import get_db_path
from gateway.credentials import encrypt_credential
from gateway.state_db import get_db

router = APIRouter()

_GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# openid + email: lets us extract upstream_user from the id_token payload
# without an extra userinfo round-trip.
_SCOPE = "https://mail.google.com/ openid email"
_STATE_TTL = 300  # seconds

# ---------------------------------------------------------------------------
# Module-level state (process-scoped, single-worker)
# ---------------------------------------------------------------------------

_pending: dict[str, dict] = {}  # state → {user_id, label, created_at}
_results: dict[str, dict] = {}  # state → result or error dict


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prune_expired() -> None:
    """Remove _pending entries older than _STATE_TTL. Called on each /start."""
    cutoff = time.time() - _STATE_TTL
    expired = [k for k, v in _pending.items() if v["created_at"] < cutoff]
    for k in expired:
        del _pending[k]


def _get_oauth2_env() -> tuple[str, str] | None:
    """Return (client_id, client_secret) from env, or None if not configured."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def _not_configured_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "oauth2_not_configured",
            "detail": "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set on this server.",
        },
    )


def _get_redirect_uri() -> str:
    return os.environ.get(
        "GOOGLE_REDIRECT_URI",
        "https://test.nuvrail.com/api/v1/oauth2/google/callback",
    )


def _urllib_post(url: str, payload: dict) -> tuple[int, str]:
    """Synchronous form POST via urllib (run in executor from async context)."""
    data = urllib.parse.urlencode(payload).encode("ascii")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        return exc.code, exc.read().decode("utf-8", errors="replace")


async def _exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> tuple[str, str]:
    """Exchange an authorization code for (refresh_token, access_token).

    Raises ValueError if the exchange fails or refresh_token is absent.
    """
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    status_code, body = await asyncio.get_event_loop().run_in_executor(
        None, _urllib_post, _GOOGLE_TOKEN_ENDPOINT, payload
    )
    if status_code != 200:
        try:
            err = json.loads(body)
            raise ValueError(
                f"Google token exchange failed: {err.get('error')} — {err.get('error_description')}"
            )
        except json.JSONDecodeError:
            raise ValueError(f"Google token exchange failed: HTTP {status_code}")

    data = json.loads(body)
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            "Google did not return a refresh_token. "
            "Ensure the OAuth2 client uses access_type=offline and prompt=consent."
        )
    access_token = data.get("access_token", "")
    id_token = data.get("id_token", "")
    return refresh_token, access_token, id_token  # type: ignore[return-value]


def _email_from_id_token(id_token: str) -> str:
    """Extract email from a Google id_token JWT payload (base64 decode, no verify)."""
    if not id_token:
        return ""
    try:
        parts = id_token.split(".")
        if len(parts) != 3:
            return ""
        padding = "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.b64decode(parts[1] + padding).decode("utf-8"))
        return payload.get("email", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/oauth2/google/start")
async def oauth2_google_start(
    label: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Initiate Gmail OAuth2 flow.

    Returns {auth_url, state}. The caller should redirect the browser to
    auth_url. On completion Google will redirect to /callback.
    """
    creds = _get_oauth2_env()
    if creds is None:
        return _not_configured_response()
    client_id, _ = creds

    _prune_expired()
    state = secrets.token_hex(32)
    _pending[state] = {
        "user_id": current_user["id"],
        "label": label or "gmail",
        "created_at": time.time(),
    }

    params = {
        "client_id": client_id,
        "redirect_uri": _get_redirect_uri(),
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = _GOOGLE_AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)
    return JSONResponse({"auth_url": auth_url, "state": state})


@router.get("/oauth2/google/callback")
async def oauth2_google_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> RedirectResponse:
    """Google OAuth2 redirect target (unauthenticated — called by Google).

    On success: creates agent_credentials row, redirects to SPA callback route.
    On any failure: stores error in _results, still redirects so the SPA can
    show a useful message rather than a blank server error page.
    """
    frontend_callback = "/#/oauth2/callback"

    # Unknown or expired state — redirect with error flag so SPA can react.
    if not state or state not in _pending:
        qs = urllib.parse.urlencode({"state": state or "", "error": "invalid_state"})
        return RedirectResponse(f"{frontend_callback}?{qs}", status_code=302)

    pending = _pending.pop(state)

    # User denied access or Google returned an error before we got a code.
    if error or not code:
        _results[state] = {"error": error or "no_code"}
        return RedirectResponse(
            f"{frontend_callback}?state={urllib.parse.quote(state)}",
            status_code=302,
        )

    # Attempt code exchange + agent creation.
    creds = _get_oauth2_env()
    if creds is None:
        _results[state] = {"error": "oauth2_not_configured"}
        return RedirectResponse(
            f"{frontend_callback}?state={urllib.parse.quote(state)}",
            status_code=302,
        )
    client_id, client_secret = creds

    try:
        refresh_token, access_token, id_token = await _exchange_code(
            code, client_id, client_secret, _get_redirect_uri()
        )
        upstream_user = _email_from_id_token(id_token)

        now = int(time.time())
        agent_username = "nuvrail_" + secrets.token_hex(8)
        agent_token_plain = secrets.token_urlsafe(32)
        hashed = hash_agent_token(agent_token_plain)

        async with get_db(db_path) as db:
            await db.execute(
                """
                INSERT INTO agent_credentials
                    (user_id, label, agent_username, hashed_token,
                     upstream_host, upstream_smtp_host,
                     upstream_imap_port, upstream_smtp_port,
                     upstream_user, upstream_password,
                     oauth2_provider, oauth2_client_id,
                     oauth2_client_secret, oauth2_refresh_token,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pending["user_id"],
                    pending["label"],
                    agent_username,
                    hashed,
                    "imap.gmail.com",
                    "smtp.gmail.com",
                    993,
                    587,
                    upstream_user,
                    None,
                    "google",
                    client_id,
                    encrypt_credential(client_secret),
                    encrypt_credential(refresh_token),
                    now,
                ),
            )
            await db.commit()

        _results[state] = {
            "agent_username": agent_username,
            "agent_token": agent_token_plain,
            "label": pending["label"],
            "upstream_user": upstream_user,
        }

    except Exception as exc:
        _results[state] = {"error": str(exc)}

    return RedirectResponse(
        f"{frontend_callback}?state={urllib.parse.quote(state)}",
        status_code=302,
    )


@router.get("/oauth2/google/result")
async def oauth2_google_result(
    state: str,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Poll for OAuth2 flow result. One-time: cleared from memory on first read.

    Returns the agent credentials on success, or 404 if the state is unknown,
    expired, or already consumed.
    """
    if state not in _results:
        return JSONResponse(
            status_code=404,
            content={"error": "oauth2_state_not_found"},
        )
    result = _results.pop(state)
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(result)
