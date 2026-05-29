"""
Tests for Web Push notification tenant-scoping (gateway/push.py).

The security property under test: a staged operation's notification is
delivered ONLY to the owning user's push subscriptions — never broadcast to
every subscriber (operation descriptions can carry sender/subject metadata).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gateway.push import notify_staged
from gateway.state_db import get_db, init_db


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_push.db"
    await init_db(path)
    return path


async def _add_subscription(db_path: Path, *, user_id: int | None, endpoint: str) -> None:
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
               VALUES (?, ?, 'p256dh-key', 'auth-key', 0)""",
            (user_id, endpoint),
        )
        await db.commit()


async def _notify_collecting_endpoints(db_path: Path, **kwargs) -> set[str]:
    """Run notify_staged with webpush mocked; return the set of endpoints hit."""
    sent: set[str] = set()

    async def _fake_webpush(*, subscription_info, **_kw):
        sent.add(subscription_info["endpoint"])

    with patch("gateway.push._ensure_vapid_keys", lambda: None), patch(
        "pywebpush.webpush_async", new=AsyncMock(side_effect=_fake_webpush)
    ):
        await notify_staged("op_test", "Move spam → Trash", db_path=db_path, **kwargs)
    return sent


async def test_notify_only_reaches_owning_user(db_path: Path) -> None:
    """Only the operation owner's subscriptions are notified."""
    await _add_subscription(db_path, user_id=1, endpoint="https://push/u1")
    await _add_subscription(db_path, user_id=2, endpoint="https://push/u2")

    sent = await _notify_collecting_endpoints(db_path, user_id=1)
    assert sent == {"https://push/u1"}


async def test_notify_with_no_owner_sends_nothing(db_path: Path) -> None:
    """An ownerless operation (user_id=None) notifies no one (fail closed)."""
    await _add_subscription(db_path, user_id=1, endpoint="https://push/u1")
    await _add_subscription(db_path, user_id=2, endpoint="https://push/u2")

    sent = await _notify_collecting_endpoints(db_path, user_id=None)
    assert sent == set()


async def test_notify_ignores_orphaned_subscriptions(db_path: Path) -> None:
    """Pre-migration subscriptions (NULL user_id) receive nothing."""
    await _add_subscription(db_path, user_id=None, endpoint="https://push/orphan")
    await _add_subscription(db_path, user_id=1, endpoint="https://push/u1")

    sent = await _notify_collecting_endpoints(db_path, user_id=1)
    assert sent == {"https://push/u1"}
