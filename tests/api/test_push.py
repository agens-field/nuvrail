"""
API tests for /api/v1/push subscription ownership scoping.

A subscription belongs to the user who registered it; another user must not
be able to delete it, and re-registering an endpoint reassigns ownership.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from api.auth import get_auth_db_path
from api.main import app
from api.routes.operations import get_db_path
from gateway.state_db import get_db, init_db


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_push_api.db"
    await init_db(path)
    return path


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _register_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "hunter2pass!"})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "hunter2pass!"})
    assert resp.status_code == 200
    return resp.json()["token"]


def _sub_body(endpoint: str) -> dict:
    return {"endpoint": endpoint, "p256dh": "p256dh-key", "auth": "auth-key"}


async def _owner_of(db_path: Path, endpoint: str) -> int | None:
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT user_id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        ) as cur:
            row = await cur.fetchone()
    return row["user_id"] if row else None


async def test_subscribe_records_owning_user(client: httpx.AsyncClient, db_path: Path) -> None:
    token = await _register_login(client, "pushowner@example.com")
    resp = await client.post(
        "/api/v1/push/subscribe",
        json=_sub_body("https://push/endpoint-a"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert await _owner_of(db_path, "https://push/endpoint-a") == 1


async def test_unsubscribe_cannot_remove_another_users_subscription(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    token_a = await _register_login(client, "usera@example.com")
    token_b = await _register_login(client, "userb@example.com")

    # User A subscribes.
    await client.post(
        "/api/v1/push/subscribe",
        json=_sub_body("https://push/shared-endpoint"),
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # User B tries to unsubscribe A's endpoint — must be a no-op.
    resp = await client.request(
        "DELETE",
        "/api/v1/push/subscribe",
        json=_sub_body("https://push/shared-endpoint"),
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 204
    # A's subscription must still exist.
    assert await _owner_of(db_path, "https://push/shared-endpoint") == 1


async def test_resubscribe_reassigns_ownership(client: httpx.AsyncClient, db_path: Path) -> None:
    token_a = await _register_login(client, "first@example.com")
    token_b = await _register_login(client, "second@example.com")

    await client.post(
        "/api/v1/push/subscribe",
        json=_sub_body("https://push/reclaim"),
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert await _owner_of(db_path, "https://push/reclaim") == 1

    # Same endpoint re-registered while logged in as user B → B now owns it.
    await client.post(
        "/api/v1/push/subscribe",
        json=_sub_body("https://push/reclaim"),
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert await _owner_of(db_path, "https://push/reclaim") == 2
