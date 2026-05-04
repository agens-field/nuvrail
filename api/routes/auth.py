"""
Auth + agent credential endpoints — Milestone auth.

POST /api/v1/auth/register  — create a user account
POST /api/v1/auth/login     — exchange email/password for a bearer token
GET  /api/v1/auth/me        — return current user info (requires Bearer)

POST   /api/v1/agents       — register upstream email account, get agent credentials (shown once)
GET    /api/v1/agents       — list agent credentials for current user (no token field)
DELETE /api/v1/agents/{id}  — revoke an agent credential

Lane 3: human → REST API (bearer token)
Lane 2: AI agent → proxy (agent_username + agent_token as IMAP/SMTP password)
"""
from __future__ import annotations

import asyncio
import imaplib
import secrets
import socket
import ssl
import time
from pathlib import Path
from typing import List, Union

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from api.auth import (
    generate_token,
    get_current_user,
    hash_agent_token,
    hash_password,
    hash_token_for_storage,
    verify_password,
)
from gateway.credentials import encrypt_credential
from gateway.security_controls import build_auth_abuse_protector
from api.models import (
    AgentCreateRequest,
    AgentCreateResponse,
    AgentResponse,
    LoginRequest,
    LoginResponse,
    UserCreateRequest,
    UserResponse,
)
from api.routes.operations import get_db_path
from gateway.state_db import get_db

router = APIRouter()
LOGIN_ABUSE_PROTECTOR = build_auth_abuse_protector("api_login")


class ImapValidationError(Exception):
    """Raised when upstream IMAP credential validation fails."""

    def __init__(self, error: str, detail: str):
        super().__init__(detail)
        self.error = error
        self.detail = detail


def _build_auth_failure_detail(upstream_host: str) -> str:
    host = upstream_host.lower()
    if any(k in host for k in ("gmail.com", "icloud.com", "me.com", "mac.com")):
        return (
            "IMAP authentication failed. Wrong username or password. "
            "For Gmail/iCloud, use an app-specific password."
        )
    return "IMAP authentication failed. Wrong username or password."


def _verify_imap_connection_sync(
    upstream_host: str,
    upstream_imap_port: int,
    upstream_user: str,
    upstream_password: str,
) -> None:
    client: imaplib.IMAP4_SSL | None = None
    try:
        client = imaplib.IMAP4_SSL(upstream_host, upstream_imap_port, timeout=10)
        client.login(upstream_user, upstream_password)
        status_, _ = client.select("INBOX")
        if status_.upper() != "OK":
            raise imaplib.IMAP4.error("Failed to select INBOX folder")
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


async def _verify_imap_connection(
    upstream_host: str,
    upstream_imap_port: int,
    upstream_user: str,
    upstream_password: str,
) -> None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                _verify_imap_connection_sync,
                upstream_host,
                upstream_imap_port,
                upstream_user,
                upstream_password,
            ),
            timeout=10,
        )
    except asyncio.TimeoutError as exc:
        raise ImapValidationError(
            "imap_timeout", "Timed out while verifying IMAP connection (10s limit)."
        ) from exc
    except ssl.SSLError as exc:
        raise ImapValidationError(
            "imap_ssl_error", "IMAP SSL/TLS handshake failed. Check host and IMAP SSL port."
        ) from exc
    except socket.timeout as exc:
        raise ImapValidationError(
            "imap_timeout", "Timed out while verifying IMAP connection (10s limit)."
        ) from exc
    except imaplib.IMAP4.abort as exc:
        raise ImapValidationError(
            "imap_connection_failed", "Unable to reach IMAP server. Check host and port."
        ) from exc
    except imaplib.IMAP4.error as exc:
        raise ImapValidationError("imap_auth_failed", _build_auth_failure_detail(upstream_host)) from exc
    except (socket.gaierror, ConnectionRefusedError, OSError) as exc:
        raise ImapValidationError(
            "imap_connection_failed", "Unable to reach IMAP server. Check host and port."
        ) from exc


# ---------------------------------------------------------------------------
# Account endpoints
# ---------------------------------------------------------------------------


@router.post("/auth/register", response_model=UserResponse, status_code=201)
async def register(
    body: UserCreateRequest,
    db_path: Path = Depends(get_db_path),
) -> UserResponse:
    """Create a new user account.

    Returns 409 if the email is already registered.
    """
    now = int(time.time())
    hashed = hash_password(body.password)
    # Generate an API token immediately on registration so the client can
    # auto-login without a second round-trip.
    api_token = generate_token()

    async with get_db(db_path) as db:
        # Check for existing email
        async with db.execute(
            "SELECT id FROM users WHERE email = ?", (body.email,)
        ) as cur:
            existing = await cur.fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email {body.email!r} is already registered",
            )

        cur2 = await db.execute(
            """
            INSERT INTO users (email, display_name, hashed_password, api_token, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (body.email, body.display_name, hashed, hash_token_for_storage(api_token), now),
        )
        await db.commit()
        user_id = cur2.lastrowid

    return UserResponse(
        user_id=user_id,  # type: ignore[arg-type]
        email=body.email,
        display_name=body.display_name,
        created_at=now,
    )


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db_path: Path = Depends(get_db_path),
) -> LoginResponse:
    """Exchange email + password for a long-lived bearer token.

    Returns 401 on bad credentials (deliberately vague).
    """
    client_host = request.client.host if request.client and request.client.host else "unknown"
    decision = await LOGIN_ABUSE_PROTECTOR.start_attempt(ip=client_host, account=body.email)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Too many login attempts",
                "reason": decision.reason,
                "retry_after_seconds": decision.retry_after_seconds,
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM users WHERE email = ?", (body.email,)
        ) as cur:
            row = await cur.fetchone()

    if row is None or not verify_password(body.password, row["hashed_password"]):
        failure = await LOGIN_ABUSE_PROTECTOR.record_failure(ip=client_host, account=body.email)
        if failure.lockout_applied:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Too many login attempts",
                    "reason": "temporary_lockout",
                    "retry_after_seconds": failure.retry_after_seconds,
                },
                headers={"Retry-After": str(failure.retry_after_seconds)},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await LOGIN_ABUSE_PROTECTOR.record_success(ip=client_host, account=body.email)
    user = dict(row)

    # Generate a fresh plaintext token for this session; store only its SHA-256
    # hash so the DB never holds a recoverable secret.  Each login invalidates
    # the previous token (only the latest hash is stored).
    fresh_token = generate_token()
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE users SET api_token = ? WHERE id = ?",
            (hash_token_for_storage(fresh_token), user["id"]),
        )
        await db.commit()

    return LoginResponse(
        token=fresh_token,  # plaintext — shown once, not stored
        token_type="bearer",
        user_id=user["id"],
        email=user["email"],
    )


@router.get("/auth/me", response_model=UserResponse)
async def me(
    current_user: dict = Depends(get_current_user),
) -> UserResponse:
    """Return the current user's profile."""
    return UserResponse(
        user_id=current_user["id"],
        email=current_user["email"],
        display_name=current_user.get("display_name"),
        created_at=current_user["created_at"],
    )


# ---------------------------------------------------------------------------
# Agent credential endpoints
# ---------------------------------------------------------------------------


@router.post("/agents", response_model=AgentCreateResponse, status_code=201)
async def create_agent(
    body: AgentCreateRequest,
    current_user: dict = Depends(get_current_user),
    db_path: Path = Depends(get_db_path),
) -> Union[AgentCreateResponse, JSONResponse]:
    """Register upstream email credentials and generate an agent token.

    The plaintext agent_token is returned ONCE in this response.
    It is NEVER stored and NEVER returned again — the caller must save it.
    """
    now = int(time.time())
    agent_username = "nuvrail_" + secrets.token_hex(8)
    agent_token_plain = generate_token()
    hashed = hash_agent_token(agent_token_plain)
    label = body.label or "default"
    try:
        await _verify_imap_connection(
            body.upstream_host,
            body.upstream_imap_port,
            body.upstream_user,
            body.upstream_password,
        )
    except ImapValidationError as exc:
        return JSONResponse(status_code=422, content={"error": exc.error, "detail": exc.detail})

    async with get_db(db_path) as db:
        cur = await db.execute(
            """
            INSERT INTO agent_credentials
                (user_id, label, agent_username, hashed_token,
                 upstream_host, upstream_imap_port, upstream_smtp_port,
                 upstream_user, upstream_password, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                current_user["id"],
                label,
                agent_username,
                hashed,
                body.upstream_host,
                body.upstream_imap_port,
                body.upstream_smtp_port,
                body.upstream_user,
                encrypt_credential(body.upstream_password),
                now,
            ),
        )
        await db.commit()
        cred_id = cur.lastrowid

    return AgentCreateResponse(
        id=cred_id,  # type: ignore[arg-type]
        agent_username=agent_username,
        agent_token=agent_token_plain,  # SHOWN ONCE — never stored as plaintext
        label=label,
        upstream_host=body.upstream_host,
        upstream_user=body.upstream_user,
    )


@router.get("/agents", response_model=List[AgentResponse])
async def list_agents(
    current_user: dict = Depends(get_current_user),
    db_path: Path = Depends(get_db_path),
) -> List[AgentResponse]:
    """List agent credentials for the current user. Token is never returned."""
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT id, agent_username, label, upstream_host, upstream_user,
                   created_at, revoked_at
            FROM agent_credentials
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (current_user["id"],),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    return [AgentResponse(**r) for r in rows]


@router.delete("/agents/{agent_id}", status_code=204)
async def revoke_agent(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db_path: Path = Depends(get_db_path),
) -> None:
    """Revoke an agent credential. Proxy will reject it on next auth attempt."""
    now = int(time.time())
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT id, user_id FROM agent_credentials WHERE id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent credential {agent_id} not found",
            )
        if row["user_id"] != current_user["id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not your credential",
            )

        await db.execute(
            "UPDATE agent_credentials SET revoked_at = ? WHERE id = ?",
            (now, agent_id),
        )
        await db.commit()
