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

import secrets
import time
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import (
    generate_token,
    get_current_user,
    hash_agent_token,
    hash_password,
    verify_password,
)
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
            (body.email, body.display_name, hashed, api_token, now),
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
    db_path: Path = Depends(get_db_path),
) -> LoginResponse:
    """Exchange email + password for a long-lived bearer token.

    Returns 401 on bad credentials (deliberately vague).
    """
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM users WHERE email = ?", (body.email,)
        ) as cur:
            row = await cur.fetchone()

    if row is None or not verify_password(body.password, row["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = dict(row)
    return LoginResponse(
        token=user["api_token"],
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
) -> AgentCreateResponse:
    """Register upstream email credentials and generate an agent token.

    The plaintext agent_token is returned ONCE in this response.
    It is NEVER stored and NEVER returned again — the caller must save it.
    """
    now = int(time.time())
    agent_username = "nuvrail_" + secrets.token_hex(8)
    agent_token_plain = generate_token()
    hashed = hash_agent_token(agent_token_plain)
    label = body.label or "default"

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
                body.upstream_password,
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
