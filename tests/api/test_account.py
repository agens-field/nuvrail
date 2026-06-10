"""
Tests for session management endpoints — token info + rotate (issue #28).

GET  /api/v1/account/token
POST /api/v1/account/token/rotate
"""
from __future__ import annotations

from pathlib import Path

import httpx

# db_path and client fixtures are provided by tests/api/conftest.py.


# Counter for unique emails to avoid AuthAbuseProtector account-level rate limiting
_email_counter = 0


def _unique_email() -> str:
    global _email_counter
    _email_counter += 1
    return f"user{_email_counter}@example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_and_login(
    client: httpx.AsyncClient,
    email: str | None = None,
    password: str = "supersecurepass",
) -> tuple[str, str]:  # (token, email)
    if email is None:
        email = _unique_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": "Test User"},
    )
    assert resp.status_code == 201
    resp2 = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp2.status_code == 200
    return resp2.json()["token"], email


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Issue #28 — Token info
# ---------------------------------------------------------------------------


async def test_get_token_info_returns_created_at(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /account/token returns created_at after login."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/token", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "created_at" in data
    assert data["created_at"] is not None
    assert isinstance(data["created_at"], int)


async def test_get_token_info_last_used_field_present(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """last_used_at field is present (may be null or int depending on throttle timing)."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/token", headers=_auth(token))
    assert resp.status_code == 200
    assert "last_used_at" in resp.json()


async def test_get_token_info_requires_auth(client: httpx.AsyncClient) -> None:
    """GET /account/token returns 401 without a token."""
    resp = await client.get("/api/v1/account/token")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Issue #28 — Token rotate
# ---------------------------------------------------------------------------


async def test_rotate_token_returns_new_token(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """POST /account/token/rotate returns a new token with expected shape."""
    token, _ = await _register_and_login(client)
    resp = await client.post("/api/v1/account/token/rotate", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["token_type"] == "bearer"
    assert data["token"] != token  # new token differs from old
    assert "user_id" in data
    assert "email" in data


async def test_rotate_token_invalidates_old_token(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """After rotation, the old token is rejected."""
    old_token, _ = await _register_and_login(client)
    resp = await client.post("/api/v1/account/token/rotate", headers=_auth(old_token))
    new_token = resp.json()["token"]

    # Old token should now be 401
    resp_old = await client.get("/api/v1/account/token", headers=_auth(old_token))
    assert resp_old.status_code == 401

    # New token should work
    resp_new = await client.get("/api/v1/account/token", headers=_auth(new_token))
    assert resp_new.status_code == 200


async def test_rotate_token_resets_created_at(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Rotated token has a fresh api_token_created_at and null last_used_at."""
    token, _ = await _register_and_login(client)
    info_before = (await client.get("/api/v1/account/token", headers=_auth(token))).json()

    resp = await client.post("/api/v1/account/token/rotate", headers=_auth(token))
    new_token = resp.json()["token"]

    info_after = (await client.get("/api/v1/account/token", headers=_auth(new_token))).json()
    assert info_after["created_at"] >= info_before["created_at"]
    assert info_after["last_used_at"] is None


async def test_rotate_token_requires_auth(client: httpx.AsyncClient) -> None:
    """POST /account/token/rotate returns 401 without a token."""
    resp = await client.post("/api/v1/account/token/rotate")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Issue #27 — Data export
# ---------------------------------------------------------------------------

from gateway.state_db import get_db  # noqa: E402


async def test_export_returns_json_attachment(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """GET /account/export returns JSON with Content-Disposition attachment."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "nuvrail-export-" in cd


async def test_export_contains_expected_fields(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Export JSON contains all required top-level fields."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    data = resp.json()
    for field in ("exported_at", "account", "agents", "operations", "audit_log", "auto_approval_rules"):
        assert field in data, f"Missing field: {field}"


async def test_export_account_fields(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Export account block has email, display_name, created_at."""
    email = _unique_email()
    token, _ = await _register_and_login(client, email=email)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    account = resp.json()["account"]
    assert account["email"] == email
    assert account["display_name"] == "Test User"
    assert isinstance(account["created_at"], int)


async def test_export_no_credentials(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Export agents list does not contain credential values."""
    token, _ = await _register_and_login(client)
    resp = await client.get("/api/v1/account/export", headers=_auth(token))
    data = resp.json()
    for agent in data["agents"]:
        for forbidden_key in ("hashed_token", "upstream_password", "oauth2_refresh_token",
                               "oauth2_client_secret", "oauth2_access_token"):
            assert forbidden_key not in agent, f"Credential field leaked: {forbidden_key}"


async def test_export_writes_audit_event(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Exporting writes an 'export_requested' audit log event."""
    token, _ = await _register_and_login(client)
    await client.get("/api/v1/account/export", headers=_auth(token))

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT event FROM audit_log WHERE event = 'export_requested'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["event"] == "export_requested"


async def test_export_requires_auth(client: httpx.AsyncClient) -> None:
    """GET /account/export returns 401 without a token."""
    resp = await client.get("/api/v1/account/export")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Issue #26 — Account deletion
# ---------------------------------------------------------------------------

from api.auth import _last_used_update  # noqa: E402


async def test_delete_account_success(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """DELETE /account with correct password returns 200 ok=true."""
    token, _ = await _register_and_login(client)
    resp = await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "supersecurepass"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_delete_account_wrong_password(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """DELETE /account with wrong password returns 400."""
    token, _ = await _register_and_login(client)
    resp = await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "wrongpassword"},
        headers=_auth(token),
    )
    assert resp.status_code == 400


async def test_deleted_account_rejected_on_next_request(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """After deletion, the old token is rejected with 401."""
    token, _ = await _register_and_login(client)
    _last_used_update.clear()

    await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "supersecurepass"},
        headers=_auth(token),
    )

    resp = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert resp.status_code == 401


async def test_delete_account_sets_deleted_at(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """DELETE /account sets users.deleted_at to a non-null timestamp."""
    email = _unique_email()
    token, _ = await _register_and_login(client, email=email)
    await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "supersecurepass"},
        headers=_auth(token),
    )

    # Email is tombstoned on deletion (R5), so query by deleted_at, not email.
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT deleted_at FROM users WHERE deleted_at IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["deleted_at"] is not None


async def test_delete_account_scrubs_sensitive_fields(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """DELETE /account nulls out sensitive user fields."""
    email = _unique_email()
    token, _ = await _register_and_login(client, email=email)
    await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "supersecurepass"},
        headers=_auth(token),
    )

    # The original email is pseudonymized to a tombstone on deletion (R5), so
    # the row is no longer findable by it — look it up by deleted_at instead.
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT email, hashed_password, api_token, display_name\n"
            "             FROM users WHERE deleted_at IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["api_token"] is None
    assert row["display_name"] is None
    assert row["hashed_password"] == ""
    # Original email must be gone; replaced by a non-reversible .invalid tombstone.
    assert row["email"] != email
    assert row["email"].endswith("@nuvrail.invalid")
    assert row["email"].startswith("deleted+")


async def test_delete_account_pseudonymizes_email(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """R5: deletion replaces the email with a non-reversible tombstone so the
    retained audit log can no longer be re-linked to the natural person."""
    email = _unique_email()
    token, _ = await _register_and_login(client, email=email)
    await client.request(
        "DELETE", "/api/v1/account",
        json={"password": "supersecurepass"}, headers=_auth(token),
    )
    async with get_db(db_path) as db:
        # The original email no longer exists anywhere in users.
        async with db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE email = ?", (email,)
        ) as cur:
            assert (await cur.fetchone())["n"] == 0


async def test_delete_account_writes_audit_event(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """DELETE /account writes an 'account_deleted' audit event."""
    token, _ = await _register_and_login(client)
    await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "supersecurepass"},
        headers=_auth(token),
    )

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT event FROM audit_log WHERE event = 'account_deleted'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


async def test_delete_account_requires_auth(client: httpx.AsyncClient) -> None:
    """DELETE /account returns 401 without a token."""
    resp = await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "anything"},
    )
    assert resp.status_code == 401


async def test_delete_account_cancels_pending_ops(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """DELETE /account marks pending ops for this user's agents as 'cancelled'."""
    import time as _time

    token, email = await _register_and_login(client)

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ) as cur:
            user_row = await cur.fetchone()
        user_id = user_row["id"]

        cur2 = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token,
                upstream_host, upstream_user, upstream_password, created_at)
               VALUES (?, 'test', 'nuvrail_delXX', 'hash', 'imap.example.com',
                       'u@example.com', NULL, ?)""",
            (user_id, int(_time.time())),
        )
        await db.commit()
        agent_id = cur2.lastrowid

        now = int(_time.time())
        op_id = f"op_d{agent_id}"
        await db.execute(
            """INSERT INTO staged_operations
               (id, created_at, expires_at, status, op_type, protocol,
                description, agent_id)
               VALUES (?, ?, ?, 'pending', 'move', 'imap', 'Test op', ?)""",
            (op_id, now, now + 172800, str(agent_id)),
        )
        await db.commit()

    await client.request(
        "DELETE",
        "/api/v1/account",
        json={"password": "supersecurepass"},
        headers=_auth(token),
    )

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT status FROM staged_operations WHERE id = ?", (op_id,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Issue #72 — account deletion tears down upstream secrets in the external
# secret store (and revokes OAuth2 grants) BEFORE nulling the reference columns.
#
#   register → seed agent with v2 secret refs (aws-sm) + google OAuth2
#        │
#   DELETE /account
#        │
#   delete_account() stage 3b: purge_agent_upstream_secrets(row)
#        ├─ revoke_google_refresh_token(<resolved refresh>)
#        └─ store.delete(ref) × each populated secret field
#        │
#   ...THEN stage 4 nulls the columns (refs already consumed)
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402

import gateway.secret_store as _ss  # noqa: E402
from gateway import credentials as _creds  # noqa: E402


class _RecordingStore:
    """aws-sm fake backend that records every put/delete by ref."""

    backend = "aws-sm"

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.deleted: list[str] = []
        self._n = 0

    async def put(self, plaintext, *, ctx):
        self._n += 1
        ref = f"fakeref://{ctx.field}/{self._n}"
        self.data[ref] = plaintext
        return ref

    async def get(self, ref):
        return self.data[ref]

    async def delete(self, ref):
        self.deleted.append(ref)
        self.data.pop(ref, None)


async def _seed_agent_with_secrets(
    db_path: Path, email: str, store, *, provider: str | None,
) -> dict[str, str]:
    """Insert one agent for the given user with v2 references in every secret
    field. Returns {field: ref} so the test can assert each ref was deleted."""
    refs: dict[str, str] = {}
    fields = (
        "upstream_password",
        "oauth2_refresh_token",
        "oauth2_access_token",
        "oauth2_client_secret",
    )
    for f in fields:
        stored = await _creds.store_credential(f"secret-{f}", field=f)
        # stored is a v2 envelope {"v":2,"backend":"aws-sm","ref":...}
        import json as _json

        refs[f] = _json.loads(stored)["ref"]
        refs.setdefault("_stored", {})  # type: ignore[arg-type]
        refs[f + "::stored"] = stored
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ) as cur:
            user_id = (await cur.fetchone())["id"]
        await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token, upstream_host,
                upstream_imap_port, upstream_smtp_port, upstream_user,
                upstream_password, oauth2_provider, oauth2_refresh_token,
                oauth2_access_token, oauth2_client_secret, created_at)
               VALUES (?, 'l', 'nuvrail_sec72', 'h', 'imap.example.com', 993,
                       587, 'u@example.com', ?, ?, ?, ?, ?, 0)""",
            (
                user_id,
                refs["upstream_password::stored"],
                provider,
                refs["oauth2_refresh_token::stored"],
                refs["oauth2_access_token::stored"],
                refs["oauth2_client_secret::stored"],
            ),
        )
        await db.commit()
    return refs


async def test_delete_account_deletes_external_secrets_and_revokes_oauth2(
    client: httpx.AsyncClient, db_path: Path, monkeypatch
) -> None:
    """#72: every populated upstream secret is deleted from the external store,
    the Google OAuth2 grant is revoked, and the columns are nulled afterward."""
    monkeypatch.setenv("NUVRAIL_SECRET_BACKEND", "aws-sm")
    monkeypatch.setenv("NUVRAIL_MASTER_KEY", _os.urandom(32).hex())
    _creds._cached_master_key = None
    store = _RecordingStore()
    _ss.reset_secret_store_cache()
    _ss._stores["aws-sm"] = store

    revoked: list[str] = []

    async def _fake_revoke(refresh_token: str) -> bool:
        revoked.append(refresh_token)
        return True

    monkeypatch.setattr(
        "gateway.oauth2_tokens.revoke_google_refresh_token", _fake_revoke
    )
    try:
        token, email = await _register_and_login(client)
        refs = await _seed_agent_with_secrets(
            db_path, email, store, provider="google"
        )

        resp = await client.request(
            "DELETE", "/api/v1/account",
            json={"password": "supersecurepass"}, headers=_auth(token),
        )
        assert resp.status_code == 200

        # Each of the four secret refs was deleted from the external store.
        for f in (
            "upstream_password", "oauth2_refresh_token",
            "oauth2_access_token", "oauth2_client_secret",
        ):
            assert refs[f] in store.deleted, f"{f} ref not deleted from store"

        # The Google grant was revoked with the RESOLVED refresh token.
        assert revoked == ["secret-oauth2_refresh_token"]

        # Columns are nulled afterward (refs already consumed, no orphans).
        async with get_db(db_path) as db:
            async with db.execute(
                "SELECT id FROM users WHERE email LIKE 'deleted+%@nuvrail.invalid'"
            ) as cur:
                assert await cur.fetchone() is not None
    finally:
        _ss.reset_secret_store_cache()
        _creds._cached_master_key = None


async def test_delete_account_local_backend_no_external_delete(
    client: httpx.AsyncClient, db_path: Path, monkeypatch
) -> None:
    """#72: with the local AES backend there is no external secret to delete and
    no provider revoke — deletion still succeeds (delete_credential is a no-op
    for v1/plaintext). Guards against the cross-backend helper crashing."""
    monkeypatch.delenv("NUVRAIL_SECRET_BACKEND", raising=False)
    monkeypatch.setenv("NUVRAIL_MASTER_KEY", _os.urandom(32).hex())
    _creds._cached_master_key = None

    revoke_calls: list[str] = []

    async def _fake_revoke(refresh_token: str) -> bool:  # pragma: no cover
        revoke_calls.append(refresh_token)
        return True

    monkeypatch.setattr(
        "gateway.oauth2_tokens.revoke_google_refresh_token", _fake_revoke
    )
    try:
        token, email = await _register_and_login(client)
        # local backend: store_credential returns a v1 AES envelope, not a ref
        pw = await _creds.store_credential("pw", field="upstream_password")
        async with get_db(db_path) as db:
            async with db.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ) as cur:
                user_id = (await cur.fetchone())["id"]
            await db.execute(
                """INSERT INTO agent_credentials
                   (user_id, label, agent_username, hashed_token, upstream_host,
                    upstream_user, upstream_password, created_at)
                   VALUES (?, 'l', 'nuvrail_loc72', 'h', 'imap.example.com',
                           'u@example.com', ?, 0)""",
                (user_id, pw),
            )
            await db.commit()

        resp = await client.request(
            "DELETE", "/api/v1/account",
            json={"password": "supersecurepass"}, headers=_auth(token),
        )
        assert resp.status_code == 200
        # No provider configured → no revoke attempted.
        assert revoke_calls == []
    finally:
        _creds._cached_master_key = None
