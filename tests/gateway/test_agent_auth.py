"""
Tests for gateway/agent_auth.py — the single place agent credentials are
decoded and verified for both proxies.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from api.auth import hash_agent_token
from gateway.agent_auth import (
    decode_sasl_plain,
    get_agent_credential,
    verify_agent_login,
)
from gateway.state_db import get_db, init_db


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "agent_auth.db"
    await init_db(path)
    return path


def _plain(authzid: str, authcid: str, passwd: str) -> str:
    raw = f"{authzid}\x00{authcid}\x00{passwd}".encode()
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------------
# decode_sasl_plain
# ---------------------------------------------------------------------------


def test_decode_sasl_plain_valid() -> None:
    assert decode_sasl_plain(_plain("", "nuvrail_abc", "tok-123")) == ("nuvrail_abc", "tok-123")


def test_decode_sasl_plain_ignores_authzid() -> None:
    # authzid present but ignored; authcid + passwd are used.
    assert decode_sasl_plain(_plain("admin", "agent", "pw")) == ("agent", "pw")


def test_decode_sasl_plain_accepts_bytes() -> None:
    assert decode_sasl_plain(_plain("", "u", "p").encode()) == ("u", "p")


def test_decode_sasl_plain_password_with_nul_safe() -> None:
    # Extra NUL in the password splits into >3 parts; we still take fields 1 and 2.
    payload = base64.b64encode(b"\x00user\x00pass").decode()
    assert decode_sasl_plain(payload) == ("user", "pass")


def test_decode_sasl_plain_missing_fields_returns_none() -> None:
    # Only two fields (authzid + authcid) — not a valid PLAIN triple.
    assert decode_sasl_plain(base64.b64encode(b"\x00onlyuser").decode()) is None


def test_decode_sasl_plain_invalid_base64_returns_none() -> None:
    assert decode_sasl_plain("!!!not base64!!!") is None


def test_decode_sasl_plain_empty_returns_none() -> None:
    assert decode_sasl_plain("") is None


# ---------------------------------------------------------------------------
# verify_agent_login / get_agent_credential
# ---------------------------------------------------------------------------


async def _seed_agent(
    db_path: Path, *, username: str = "nuvrail_x", token: str = "secret-token",
    revoked: bool = False,
) -> int:
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_imap_port, upstream_smtp_port,
                upstream_user, upstream_password, created_at, revoked_at)
               VALUES (1, 'l', ?, ?, 'imap.example.com', 993, 587,
                       'u@example.com', 'x', 0, ?)""",
            (username, hash_agent_token(token), 1 if revoked else None),
        )
        await db.commit()
        return int(cur.lastrowid)


async def test_verify_agent_login_success(db_path: Path) -> None:
    await _seed_agent(db_path, username="nuvrail_ok", token="good-token")
    row = await verify_agent_login("nuvrail_ok", "good-token", db_path)
    assert row is not None
    assert row["agent_username"] == "nuvrail_ok"


async def test_verify_agent_login_wrong_token(db_path: Path) -> None:
    await _seed_agent(db_path, username="nuvrail_ok", token="good-token")
    assert await verify_agent_login("nuvrail_ok", "wrong-token", db_path) is None


async def test_verify_agent_login_unknown_user(db_path: Path) -> None:
    assert await verify_agent_login("nobody", "x", db_path) is None


async def test_verify_agent_login_empty_user(db_path: Path) -> None:
    assert await verify_agent_login("", "x", db_path) is None


async def test_verify_agent_login_revoked(db_path: Path) -> None:
    await _seed_agent(db_path, username="nuvrail_rev", token="t", revoked=True)
    assert await verify_agent_login("nuvrail_rev", "t", db_path) is None


async def test_get_agent_credential_found_and_missing(db_path: Path) -> None:
    agent_id = await _seed_agent(db_path)
    row = await get_agent_credential(agent_id, db_path)
    assert row is not None and row["id"] == agent_id
    assert await get_agent_credential(99999, db_path) is None
    assert await get_agent_credential(None, db_path) is None
