"""
Account self-service endpoints.

GET  /api/v1/account/token        — view current token metadata (issue #28)
POST /api/v1/account/token/rotate — revoke old token, issue new one (issue #28)
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import Response  # noqa: F401

from api.auth import generate_token, get_current_user, hash_token_for_storage
from api.models import (
    TokenInfoResponse,
    TokenRotateResponse,
)
from api.routes.operations import get_db_path
from gateway.state_db import get_db

router = APIRouter()


# ---------------------------------------------------------------------------
# Issue #28 — Token info + rotate
# ---------------------------------------------------------------------------


@router.get("/account/token", response_model=TokenInfoResponse)
async def get_token_info(
    current_user: dict = Depends(get_current_user),
) -> TokenInfoResponse:
    """Return metadata about the current API token.

    Returns created_at and last_used_at timestamps (both nullable).
    Does not reveal the token itself.
    """
    return TokenInfoResponse(
        created_at=current_user.get("api_token_created_at"),
        last_used_at=current_user.get("api_token_last_used_at"),
    )


@router.post("/account/token/rotate", response_model=TokenRotateResponse)
async def rotate_token(
    current_user: dict = Depends(get_current_user),
    db_path: Path = Depends(get_db_path),
) -> TokenRotateResponse:
    """Revoke the current API token and issue a new one.

    The new plaintext token is returned ONCE in this response.
    It is never stored in plaintext — only its SHA-256 hash is persisted.
    """
    now = int(time.time())
    new_token = generate_token()
    async with get_db(db_path) as db:
        await db.execute(
            """
            UPDATE users
            SET api_token = ?, api_token_created_at = ?, api_token_last_used_at = NULL
            WHERE id = ?
            """,
            (hash_token_for_storage(new_token), now, current_user["id"]),
        )
        await db.commit()

    return TokenRotateResponse(
        token=new_token,
        token_type="bearer",
        user_id=current_user["id"],
        email=current_user["email"],
    )
