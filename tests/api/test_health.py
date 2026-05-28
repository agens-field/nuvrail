"""
Tests for GET /health — liveness probe endpoint.

Verifies:
- 200 response with no auth
- Response body shape: {"status": "ok", "timestamp": "<iso8601>"}
- Timestamp is a parseable UTC ISO-8601 string
- No auth header required (intentionally unauthenticated)
"""
from __future__ import annotations

from datetime import datetime, timezone

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

    async def test_keys_are_exactly_status_and_timestamp(
        self, client: httpx.AsyncClient
    ) -> None:
        """Response body has exactly the two expected keys, nothing extra."""
        resp = await client.get("/health")
        assert set(resp.json().keys()) == {"status", "timestamp"}
