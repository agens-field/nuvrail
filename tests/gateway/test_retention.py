"""
Tests for the GDPR data-retention purge (R5) — gateway.retention.

  seed: a deleted (tombstoned) user with rows across every user-linked table
        + a second active user + a second recently-deleted user
            │
            ▼
  purge_expired_deleted_users(now)
            │
   ┌────────┴─────────────────────────────────────────────┐
   past-window deleted user → ALL its rows hard-deleted    │
   within-window deleted user → untouched                  │
   active user → untouched                                 │
   global folders/messages → untouched                     │
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import gateway.secret_store as ss
from gateway import credentials as creds
from gateway.audit import insert_audit_event
from gateway.retention import find_purgeable_users, purge_expired_deleted_users
from gateway.state_db import get_db, init_db

_DAY = 86400


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "retention.db"
    await init_db(path)
    return path


async def _seed_user(
    db_path: Path, *, email: str, deleted_at: int | None
) -> int:
    """Create a user with one agent, one op, one revert, audit + rule + sub."""
    async with get_db(db_path) as db:
        ucur = await db.execute(
            "INSERT INTO users (email, hashed_password, created_at, deleted_at) "
            "VALUES (?, 'x', 0, ?)",
            (email, deleted_at),
        )
        user_id = int(ucur.lastrowid)
        acur = await db.execute(
            """INSERT INTO agent_credentials
               (user_id, label, agent_username, hashed_token, upstream_host,
                upstream_imap_port, upstream_smtp_port, upstream_user,
                upstream_password, created_at)
               VALUES (?, 'l', ?, 'h', 'imap.example.com', 993, 587,
                       'u@example.com', 'p', 0)""",
            (user_id, f"agent_{user_id}"),
        )
        agent_id = int(acur.lastrowid)
        op_id = f"op-{user_id}"
        await db.execute(
            """INSERT INTO staged_operations
               (id, created_at, expires_at, status, op_type, protocol,
                description, agent_id)
               VALUES (?, 0, 0, 'executed', 'smtp_send', 'smtp', 'd', ?)""",
            (op_id, str(agent_id)),
        )
        await db.execute(
            """INSERT INTO pending_reverts
               (operation_id, folder_id, uid, true_flags, created_at)
               VALUES (?, 1, 1, '[]', 0)""",
            (op_id,),
        )
        await db.execute(
            """INSERT INTO auto_approval_rules
               (enabled, priority, op_type, action, description, created_at, user_id)
               VALUES (1, 1, 'smtp_send', 'approve', 'd', 0, ?)""",
            (user_id,),
        )
        await db.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at, user_id) "
            "VALUES (?, 'k', 'a', 0, ?)",
            (f"https://push/{user_id}", user_id),
        )
        await insert_audit_event(
            db, timestamp=0, event="executed", actor="human",
            user_id=user_id, agent_id=str(agent_id), operation_id=op_id,
        )
        await db.commit()
    return user_id


async def _counts_for_user(db_path: Path, user_id: int) -> dict[str, int]:
    """Count remaining rows linked to a user across every purged table."""
    async with get_db(db_path) as db:
        out: dict[str, int] = {}
        for label, sql, params in [
            ("users", "SELECT COUNT(*) FROM users WHERE id = ?", (user_id,)),
            ("agents", "SELECT COUNT(*) FROM agent_credentials WHERE user_id = ?", (user_id,)),
            ("rules", "SELECT COUNT(*) FROM auto_approval_rules WHERE user_id = ?", (user_id,)),
            ("subs", "SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (user_id,)),
            ("audit", "SELECT COUNT(*) FROM audit_log WHERE user_id = ?", (user_id,)),
            ("ops", ("SELECT COUNT(*) FROM staged_operations WHERE agent_id IN "
                    "(SELECT CAST(id AS TEXT) FROM agent_credentials WHERE user_id = ?)"), (user_id,)),
        ]:
            async with db.execute(sql, params) as cur:
                out[label] = (await cur.fetchone())[0]
    return out


async def test_purges_user_past_window_completely(db_path: Path) -> None:
    now = 1_000_000_000
    # Deleted 366 days ago (> 365-day default window).
    uid = await _seed_user(db_path, email="old@x.com", deleted_at=now - 366 * _DAY)

    # Pre-check: everything is present.
    before = await _counts_for_user(db_path, uid)
    assert all(v >= 1 for v in before.values()), before

    purged = await purge_expired_deleted_users(db_path=db_path, now=now)
    assert purged == 1

    after = await _counts_for_user(db_path, uid)
    assert after == dict.fromkeys(after, 0), after
    # The op's pending_reverts row is gone too (child via operation_id).
    async with get_db(db_path) as db, db.execute(
        "SELECT COUNT(*) FROM pending_reverts WHERE operation_id = ?",
        (f"op-{uid}",),
    ) as cur:
        assert (await cur.fetchone())[0] == 0


async def test_within_window_user_survives(db_path: Path) -> None:
    now = 1_000_000_000
    # Deleted only 100 days ago — inside the 365-day window.
    uid = await _seed_user(db_path, email="recent@x.com", deleted_at=now - 100 * _DAY)
    purged = await purge_expired_deleted_users(db_path=db_path, now=now)
    assert purged == 0
    after = await _counts_for_user(db_path, uid)
    assert after["users"] == 1 and after["audit"] == 1


async def test_active_user_never_purged(db_path: Path) -> None:
    now = 1_000_000_000
    uid = await _seed_user(db_path, email="active@x.com", deleted_at=None)
    assert await purge_expired_deleted_users(db_path=db_path, now=now) == 0
    assert (await _counts_for_user(db_path, uid))["users"] == 1


async def test_find_purgeable_respects_cutoff(db_path: Path) -> None:
    now = 1_000_000_000
    old = await _seed_user(db_path, email="o@x.com", deleted_at=now - 400 * _DAY)
    await _seed_user(db_path, email="r@x.com", deleted_at=now - 10 * _DAY)
    await _seed_user(db_path, email="a@x.com", deleted_at=None)
    purgeable = await find_purgeable_users(db_path=db_path, now=now)
    assert purgeable == [old]


async def test_per_user_mailbox_mirror_is_purged(db_path: Path) -> None:
    """Issue #73: the deleted user's per-user mirror is purged; other tenants' survive.

    The mirror is now tenant-scoped (folders.user_id), so it IS the deleted
    user's personal data and must go. A second user with an identically-named
    'INBOX' must keep their folder + message rows untouched.
    """
    now = 1_000_000_000
    old_uid = await _seed_user(db_path, email="old2@x.com", deleted_at=now - 400 * _DAY)
    keep_uid = await _seed_user(db_path, email="keep@x.com", deleted_at=None)

    async with get_db(db_path) as db:
        # Each user owns an 'INBOX' with one message — same names, distinct rows.
        old_f = await db.execute(
            "INSERT INTO folders (user_id, name, uidvalidity) VALUES (?, 'INBOX', 1)",
            (old_uid,),
        )
        keep_f = await db.execute(
            "INSERT INTO folders (user_id, name, uidvalidity) VALUES (?, 'INBOX', 1)",
            (keep_uid,),
        )
        await db.execute(
            "INSERT INTO messages (folder_id, uid, flags, subject) VALUES (?, 1, '[]', 'old secret')",
            (old_f.lastrowid,),
        )
        await db.execute(
            "INSERT INTO messages (folder_id, uid, flags, subject) VALUES (?, 1, '[]', 'keep secret')",
            (keep_f.lastrowid,),
        )
        await db.commit()

    await purge_expired_deleted_users(db_path=db_path, now=now)

    async with get_db(db_path) as db:
        # Deleted user's mirror is gone.
        async with db.execute(
            "SELECT COUNT(*) FROM folders WHERE user_id = ?", (old_uid,)
        ) as cur:
            assert (await cur.fetchone())[0] == 0
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE subject = 'old secret'"
        ) as cur:
            assert (await cur.fetchone())[0] == 0
        # Surviving user's mirror is intact.
        async with db.execute(
            "SELECT COUNT(*) FROM folders WHERE user_id = ?", (keep_uid,)
        ) as cur:
            assert (await cur.fetchone())[0] == 1
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE subject = 'keep secret'"
        ) as cur:
            assert (await cur.fetchone())[0] == 1
    # And the deleted user is gone.
    assert (await _counts_for_user(db_path, old_uid))["users"] == 0


# ---------------------------------------------------------------------------
# Issue #72 — the hard-purge is the backstop: it tears down upstream secrets in
# the external store + revokes the OAuth2 grant BEFORE deleting the row that
# holds the references. Without this, the secret is orphaned in Secret Manager
# forever (live mailbox access retained).
# ---------------------------------------------------------------------------
class _RecordingStore:
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


async def test_purge_deletes_external_secrets_and_revokes_oauth2(
    db_path: Path, monkeypatch
) -> None:
    """#72 backstop: past-window purge deletes every upstream secret ref from the
    external store and revokes the Google grant before dropping the row."""
    monkeypatch.setenv("NUVRAIL_SECRET_BACKEND", "aws-sm")
    monkeypatch.setenv("NUVRAIL_MASTER_KEY", os.urandom(32).hex())
    creds._cached_master_key = None
    store = _RecordingStore()
    ss.reset_secret_store_cache()
    ss._stores["aws-sm"] = store

    revoked: list[str] = []

    async def _fake_revoke(refresh_token: str) -> bool:
        revoked.append(refresh_token)
        return True

    monkeypatch.setattr(
        "gateway.oauth2_tokens.revoke_google_refresh_token", _fake_revoke
    )
    try:
        now = 1_000_000_000
        uid = await _seed_user(
            db_path, email="sec72@x.com", deleted_at=now - 400 * _DAY
        )
        # Replace the seeded agent's secret columns with v2 references.
        refs: dict[str, str] = {}
        stored: dict[str, str] = {}
        for f in (
            "upstream_password", "oauth2_refresh_token",
            "oauth2_access_token", "oauth2_client_secret",
        ):
            s = await creds.store_credential(f"secret-{f}", field=f)
            stored[f] = s
            refs[f] = json.loads(s)["ref"]
        async with get_db(db_path) as db:
            await db.execute(
                """UPDATE agent_credentials
                   SET upstream_password = ?, oauth2_provider = 'google',
                       oauth2_refresh_token = ?, oauth2_access_token = ?,
                       oauth2_client_secret = ?
                   WHERE user_id = ?""",
                (
                    stored["upstream_password"],
                    stored["oauth2_refresh_token"],
                    stored["oauth2_access_token"],
                    stored["oauth2_client_secret"],
                    uid,
                ),
            )
            await db.commit()

        purged = await purge_expired_deleted_users(db_path=db_path, now=now)
        assert purged == 1

        for f, ref in refs.items():
            assert ref in store.deleted, f"{f} ref not deleted from store"
        assert revoked == ["secret-oauth2_refresh_token"]
        # Row is gone (references already consumed — no orphans left behind).
        assert (await _counts_for_user(db_path, uid))["users"] == 0
        assert (await _counts_for_user(db_path, uid))["agents"] == 0
    finally:
        ss.reset_secret_store_cache()
        creds._cached_master_key = None
