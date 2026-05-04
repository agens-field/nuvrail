"""
Authentication utilities for Nuvrail API.

Lane 3: human → REST API via bearer token.
Lane 2: AI agent → proxy via agent username + token (IMAP/SMTP password).
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gateway.state_db import DB_PATH, get_db

_bearer = HTTPBearer(auto_error=False)


def get_auth_db_path() -> Path:
    """Auth-specific DB path dependency. Override in tests via app.dependency_overrides."""
    return DB_PATH

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def generate_token(nbytes: int = 32) -> str:
    """Generate a URL-safe base58-encoded random token.

    The returned value is plaintext — show it to the client once, then store
    only hash_token_for_storage(token) in the DB.  Never store the plaintext.
    """
    raw = secrets.token_bytes(nbytes)
    # Simple base58 encoding
    num = int.from_bytes(raw, "big")
    result = []
    while num > 0:
        num, rem = divmod(num, 58)
        result.append(BASE58_ALPHABET[rem])
    return "".join(reversed(result)).zfill(44)


def hash_token_for_storage(token: str) -> str:
    """Return SHA-256 hex digest of a bearer token for safe DB storage.

    SHA-256 is deterministic and fast, making it suitable for lookup without
    bcrypt's per-call overhead.  The token itself has 32 bytes of entropy
    (256 bits), so a fast hash is acceptable — brute-force is infeasible.
    """
    import hashlib  # noqa: PLC0415
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(plain: str) -> str:
    """Hash a human password with bcrypt (rounds=12)."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def hash_agent_token(plain: str) -> str:
    """Hash an agent token with bcrypt (rounds=10 — verified per-connection)."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password/token against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def get_user_by_token(token: str, db_path: Path = DB_PATH) -> Optional[dict]:
    """Look up a user row by their API bearer token.

    The incoming token is plaintext; the DB stores sha256(token) so we hash
    before querying.  See hash_token_for_storage().
    """
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM users WHERE api_token = ?", (hash_token_for_storage(token),)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db_path: Path = Depends(get_auth_db_path),
) -> dict:
    """FastAPI dependency: validate Bearer token and return the user row."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await get_user_by_token(credentials.credentials, db_path=db_path)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
