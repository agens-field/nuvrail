"""
API tests for /api/v1/rules CRUD endpoints.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from api.auth import get_auth_db_path, get_current_user
from api.main import app
from api.routes.operations import get_db_path
from gateway.state_db import init_db

_FAKE_USER = {
    "id": 1,
    "email": "test@test.com",
    "display_name": None,
    "created_at": 0,
    "api_token": "testtoken",
}


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_rules_api.db"
    await init_db(path)
    return path


@pytest.fixture()
async def client(db_path: Path) -> httpx.AsyncClient:
    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_rules_list_empty(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/rules")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_rules_create_and_list(client: httpx.AsyncClient) -> None:
    create = await client.post(
        "/api/v1/rules",
        json={
            "enabled": True,
            "priority": 50,
            "op_type": "mark_read",
            "sender_pattern": "*@substack.com",
            "folder_from": "INBOX",
            "action": "approve",
            "description": "Approve Substack mark-read",
        },
    )
    assert create.status_code == 201
    rule = create.json()
    assert rule["id"] > 0
    assert rule["enabled"] is True
    assert rule["action"] == "approve"
    assert rule["priority"] == 50

    list_resp = await client.get("/api/v1/rules")
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == rule["id"]


async def test_rules_patch_updates_fields(client: httpx.AsyncClient) -> None:
    create = await client.post(
        "/api/v1/rules",
        json={"action": "approve", "description": "initial"},
    )
    rid = create.json()["id"]

    patch_resp = await client.patch(
        f"/api/v1/rules/{rid}",
        json={
            "enabled": False,
            "priority": 99,
            "sender_pattern": "*@newsletter.com",
            "action": "reject",
            "description": "updated",
        },
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["id"] == rid
    assert updated["enabled"] is False
    assert updated["priority"] == 99
    assert updated["sender_pattern"] == "*@newsletter.com"
    assert updated["action"] == "reject"
    assert updated["description"] == "updated"


async def test_rules_delete(client: httpx.AsyncClient) -> None:
    create = await client.post(
        "/api/v1/rules",
        json={"action": "approve", "description": "to delete"},
    )
    rid = create.json()["id"]

    delete_resp = await client.delete(f"/api/v1/rules/{rid}")
    assert delete_resp.status_code == 204

    list_resp = await client.get("/api/v1/rules")
    assert list_resp.status_code == 200
    assert list_resp.json() == []


async def test_rules_ordered_by_priority_desc_then_id(client: httpx.AsyncClient) -> None:
    r1 = await client.post(
        "/api/v1/rules",
        json={"priority": 10, "action": "approve", "description": "first"},
    )
    r2 = await client.post(
        "/api/v1/rules",
        json={"priority": 20, "action": "approve", "description": "second"},
    )
    r3 = await client.post(
        "/api/v1/rules",
        json={"priority": 10, "action": "approve", "description": "third"},
    )
    assert r1.status_code == 201 and r2.status_code == 201 and r3.status_code == 201
    id1 = r1.json()["id"]
    id2 = r2.json()["id"]
    id3 = r3.json()["id"]

    rows = (await client.get("/api/v1/rules")).json()
    assert [r["id"] for r in rows] == [id2, id1, id3]
