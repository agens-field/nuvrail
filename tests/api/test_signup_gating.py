"""Tests for human-account signup gating (NUVRAIL_SIGNUP_MODE).

Two layers:
  1. resolve_signup_mode() — pure fail-closed resolution (no I/O).
  2. /auth/register enforcement across closed / invite / open, plus the
     single-use invite lifecycle (mint -> redeem -> reuse rejected; expired;
     revoked; concurrent double-spend).

The api-suite autouse fixture defaults the deployment to open signup; each
test here monkeypatches the env to the mode under test (closest scope wins).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from api.auth import hash_token_for_storage
from api.signup import resolve_signup_mode
from gateway.state_db import (
    get_db,
    insert_invite_code,
    list_invite_codes,
    redeem_invite_code,
    revoke_invite_code,
)

# db_path + client fixtures come from tests/api/conftest.py.


# ---------------------------------------------------------------------------
# 1. resolve_signup_mode — pure resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "garbage", "Closedd", "opens", "invit", "CLOSE"],
)
def test_unknown_or_empty_mode_fails_closed(raw: str) -> None:
    assert resolve_signup_mode(raw, "") == "closed"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("closed", "closed"), ("CLOSED", "closed"), ("  invite ", "invite"),
     ("Invite", "invite")],
)
def test_known_non_open_modes(raw: str, expected: str) -> None:
    assert resolve_signup_mode(raw, "") == expected


@pytest.mark.parametrize("ack", ["1", "true", "TRUE", "yes", "on", " Yes "])
def test_open_with_ack_allowed(ack: str) -> None:
    assert resolve_signup_mode("open", ack) == "open"


@pytest.mark.parametrize("ack", ["", "0", "false", "no", "nope", "  "])
def test_open_without_ack_raises(ack: str) -> None:
    with pytest.raises(RuntimeError, match="NUVRAIL_ALLOW_OPEN_SIGNUP"):
        resolve_signup_mode("open", ack)


# ---------------------------------------------------------------------------
# 2. /auth/register enforcement
# ---------------------------------------------------------------------------


async def _register(client: httpx.AsyncClient, email: str, code: str | None = None):
    body = {"email": email, "password": "hunter2", "display_name": "T"}
    if code is not None:
        body["invite_code"] = code
    return await client.post("/api/v1/auth/register", json=body)


async def test_closed_mode_blocks_register(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "closed")
    monkeypatch.delenv("NUVRAIL_ALLOW_OPEN_SIGNUP", raising=False)
    resp = await _register(client, "nobody@example.com")
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()


async def test_open_mode_allows_register(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "open")
    monkeypatch.setenv("NUVRAIL_ALLOW_OPEN_SIGNUP", "1")
    resp = await _register(client, "open@example.com")
    assert resp.status_code == 201


async def test_invite_mode_requires_code(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "invite")
    resp = await _register(client, "noinvite@example.com")  # no code
    assert resp.status_code == 403
    assert "invite code is required" in resp.json()["detail"].lower()


async def test_invite_mode_rejects_bad_code(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "invite")
    resp = await _register(client, "bad@example.com", code="not-a-real-code")
    assert resp.status_code == 403
    assert "invalid or already-used" in resp.json()["detail"].lower()


async def test_invite_mode_happy_path_and_single_use(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "invite")
    raw = "clay_test_invite_code_ABC123"
    await insert_invite_code(hash_token_for_storage(raw), db_path=db_path)

    # First use: succeeds.
    resp = await _register(client, "first@example.com", code=raw)
    assert resp.status_code == 201

    # Second use of the SAME code: rejected (single-use spent).
    resp2 = await _register(client, "second@example.com", code=raw)
    assert resp2.status_code == 403
    assert "invalid or already-used" in resp2.json()["detail"].lower()

    # The spent code records who redeemed it.
    rows = await list_invite_codes(db_path)
    assert len(rows) == 1
    assert rows[0]["redeemed_by_email"] == "first@example.com"
    assert rows[0]["redeemed_at"] is not None


async def test_invite_expired_rejected(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "invite")
    raw = "expired_code_XYZ"
    past = int(time.time()) - 60
    await insert_invite_code(hash_token_for_storage(raw), expires_at=past, db_path=db_path)
    resp = await _register(client, "late@example.com", code=raw)
    assert resp.status_code == 403


async def test_invite_revoked_rejected(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "invite")
    raw = "revoke_me_code"
    ch = hash_token_for_storage(raw)
    await insert_invite_code(ch, db_path=db_path)
    assert await revoke_invite_code(ch, db_path=db_path) is True
    resp = await _register(client, "revoked@example.com", code=raw)
    assert resp.status_code == 403


async def test_duplicate_email_does_not_burn_code(
    client: httpx.AsyncClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code must not be spent when registration 409s on a duplicate email."""
    # Seed an existing user (open mode) then switch to invite.
    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "open")
    monkeypatch.setenv("NUVRAIL_ALLOW_OPEN_SIGNUP", "1")
    assert (await _register(client, "dupe@example.com")).status_code == 201

    monkeypatch.setenv("NUVRAIL_SIGNUP_MODE", "invite")
    monkeypatch.delenv("NUVRAIL_ALLOW_OPEN_SIGNUP", raising=False)
    raw = "should_survive_code"
    ch = hash_token_for_storage(raw)
    await insert_invite_code(ch, db_path=db_path)

    resp = await _register(client, "dupe@example.com", code=raw)
    assert resp.status_code == 409  # email already registered

    # The code is still unspent — the email check runs before redemption.
    rows = await list_invite_codes(db_path)
    assert rows[0]["redeemed_at"] is None


# ---------------------------------------------------------------------------
# 3. redeem_invite_code helper — atomic single-use under concurrency
# ---------------------------------------------------------------------------


async def test_redeem_helper_double_spend_only_one_wins(db_path: Path) -> None:
    raw = "concurrent_code"
    ch = hash_token_for_storage(raw)
    await insert_invite_code(ch, db_path=db_path)

    # Fire several concurrent redemptions of the same code.
    results = await asyncio.gather(
        *[redeem_invite_code(ch, f"user{i}@example.com", db_path=db_path) for i in range(8)]
    )
    assert sum(1 for r in results if r) == 1  # exactly one redemption wins

    # And the code is now spent for everyone else.
    assert await redeem_invite_code(ch, "late@example.com", db_path=db_path) is False


async def test_redeem_unknown_code_returns_false(db_path: Path) -> None:
    assert await redeem_invite_code("deadbeef", "x@example.com", db_path=db_path) is False


async def test_stored_invite_is_hash_not_plaintext(db_path: Path) -> None:
    raw = "plaintext_should_not_appear"
    await insert_invite_code(hash_token_for_storage(raw), db_path=db_path)
    async with get_db(db_path) as db, db.execute("SELECT code_hash FROM invite_codes") as cur:
        row = await cur.fetchone()
    assert row["code_hash"] != raw
    assert row["code_hash"] == hash_token_for_storage(raw)
