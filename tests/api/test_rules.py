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


async def test_rules_list_includes_hits_count(client: httpx.AsyncClient, db_path: Path) -> None:
    """GET /rules returns a hits=0 count for a new rule with no audit activity."""
    resp = await client.post('/api/v1/rules', json={
        'op_type': 'move', 'action': 'approve', 'description': 'hits test rule',
    })
    assert resp.status_code == 201
    rule_id = resp.json()['id']

    resp = await client.get('/api/v1/rules')
    assert resp.status_code == 200
    rules = resp.json()
    rule = next(r for r in rules if r['id'] == rule_id)
    assert 'hits' in rule
    assert rule['hits'] == 0


async def test_rules_list_hits_count_increments_from_audit_log(
    client: httpx.AsyncClient, db_path: Path
) -> None:
    """Hits count reflects matching auto_rule entries in audit_log."""
    import json as _json
    import time as _time
    from gateway.state_db import get_db as _get_db

    resp = await client.post('/api/v1/rules', json={
        'op_type': 'trash', 'action': 'approve', 'description': 'trash rule',
    })
    assert resp.status_code == 201
    rule_id = resp.json()['id']

    # Simulate two auto_rule audit entries referencing this rule
    now = int(_time.time())
    async with _get_db(db_path) as db:
        for _ in range(2):
            await db.execute(
                """INSERT INTO audit_log (timestamp, event, actor, detail)
                   VALUES (?, 'approved', 'auto_rule', ?)""",
                (now, _json.dumps({'rule_id': rule_id, 'rule_description': 'trash rule'})),
            )
        await db.commit()

    resp = await client.get('/api/v1/rules')
    assert resp.status_code == 200
    rules = resp.json()
    rule = next(r for r in rules if r['id'] == rule_id)
    assert rule['hits'] == 2


async def test_rules_test_no_match(client: httpx.AsyncClient) -> None:
    """POST /rules/test returns matched=False when no rule matches."""
    resp = await client.post('/api/v1/rules/test', json={
        'op_type': 'smtp_send', 'sender': 'nobody@example.com',
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body['matched'] is False
    assert body.get('rule_id') is None


async def test_rules_test_match(client: httpx.AsyncClient, db_path: Path) -> None:
    """POST /rules/test returns matched=True with rule info when a rule fires."""
    resp = await client.post('/api/v1/rules', json={
        'op_type': 'mark_read',
        'sender_pattern': '*@newsletters.com',
        'action': 'approve',
        'description': 'Newsletter auto-approve',
    })
    assert resp.status_code == 201

    resp = await client.post('/api/v1/rules/test', json={
        'op_type': 'mark_read',
        'sender': 'digest@newsletters.com',
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body['matched'] is True
    assert body['action'] == 'approve'
    assert body['rule_description'] == 'Newsletter auto-approve'
    assert body['rule_id'] is not None
