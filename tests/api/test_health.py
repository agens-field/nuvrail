"""
Tests for GET /health — liveness probe endpoint.

Verifies:
- 200 response with no auth
- Response body shape: {"status": "ok", "timestamp": "<iso8601>"}
- Timestamp is a parseable UTC ISO-8601 string
- No auth header required (intentionally unauthenticated)
"""
from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from api.main import app


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    """Return an AsyncClient wired to the app (no DB override needed)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoint:
    async def test_returns_200(self, client: httpx.AsyncClient) -> None:
        """GET /health must return HTTP 200."""
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_response_shape(self, client: httpx.AsyncClient) -> None:
        """Response body must be {"status": "ok", "timestamp": <str>}."""
        resp = await client.get("/health")
        body = resp.json()
        assert body["status"] == "ok"
        assert isinstance(body["timestamp"], str)

    async def test_timestamp_is_valid_iso8601(self, client: httpx.AsyncClient) -> None:
        """Timestamp must be a parseable UTC ISO-8601 string."""
        resp = await client.get("/health")
        ts_str = resp.json()["timestamp"]
        # fromisoformat accepts "2026-01-01T00:00:00+00:00" and ".../Z" variants
        # Python 3.11+ handles Z; for 3.9/3.10 compatibility, normalise first.
        ts_str_norm = ts_str.replace("Z", "+00:00")
        ts = datetime.fromisoformat(ts_str_norm)
        # Should be UTC (offset 0 or +00:00)
        assert ts.utcoffset() is not None
        assert ts.utcoffset().total_seconds() == 0

    async def test_no_auth_required(self, client: httpx.AsyncClient) -> None:
        """No Authorization header — must still return 200, not 401/403."""
        resp = await client.get("/health")
        assert resp.status_code not in (401, 403)

    async def test_body_keys(self, client: httpx.AsyncClient) -> None:
        """Response body carries status, timestamp, and the loop-health block."""
        resp = await client.get("/health")
        assert set(resp.json().keys()) == {"status", "timestamp", "loops_ok", "loops"}

    async def test_loop_heartbeat_surfaced(self, client: httpx.AsyncClient) -> None:
        """A recorded heartbeat appears under /health loops; stays 200 even when
        a loop is stale (liveness must not depend on worker health)."""
        from gateway import loop_health

        loop_health.reset_heartbeats()
        # A fresh heartbeat → not stale.
        loop_health.record_heartbeat("scrubber", interval_seconds=3600)
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["loops"]["scrubber"]["stale"] is False
        assert body["loops_ok"] is True

        # An old heartbeat → stale, but the probe is still 200.
        loop_health.reset_heartbeats()
        loop_health.record_heartbeat("scrubber", interval_seconds=3600, now=1)
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["loops"]["scrubber"]["stale"] is True
        assert resp.json()["loops_ok"] is False
        loop_health.reset_heartbeats()
