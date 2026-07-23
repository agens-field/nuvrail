"""
Tests for CORS origin restriction (GitHub #19).

Verifies:
  1. _parse_cors_origins() correctly parses comma-separated env var values
  2. An allowed origin receives Access-Control-Allow-Origin in the response
  3. A disallowed origin does not receive Access-Control-Allow-Origin
  4. Wildcard fallback ("*") when env var is absent (dev mode)
  5. Preflight OPTIONS requests are handled correctly for allowed/disallowed origins

Approach: builds a minimal test FastAPI app with the same CORS wiring as
api/main.py rather than reloading the module, so tests are isolated and
deterministic regardless of the test environment's env vars.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_cors_origins(raw: str) -> list[str]:
    """Mirrors the _CORS_ORIGINS parsing logic in api/main.py."""
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["*"]


def _make_app(origins: list[str]) -> FastAPI:
    """Build a minimal FastAPI app with the given CORS origins."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "true"}

    return app


# ---------------------------------------------------------------------------
# _parse_cors_origins unit tests
# ---------------------------------------------------------------------------


def test_parse_cors_single_origin() -> None:
    result = _parse_cors_origins("https://example.com")
    assert result == ["https://example.com"]


def test_parse_cors_multiple_origins() -> None:
    result = _parse_cors_origins("https://example.com,https://nuvrail.example.com")
    assert result == ["https://example.com", "https://nuvrail.example.com"]


def test_parse_cors_strips_whitespace() -> None:
    result = _parse_cors_origins("https://example.com , https://nuvrail.example.com ")
    assert result == ["https://example.com", "https://nuvrail.example.com"]


def test_parse_cors_empty_string_falls_back_to_wildcard() -> None:
    assert _parse_cors_origins("") == ["*"]


def test_parse_cors_whitespace_only_falls_back_to_wildcard() -> None:
    assert _parse_cors_origins("   ") == ["*"]


def test_parse_cors_skips_empty_segments() -> None:
    result = _parse_cors_origins("https://example.com,,https://nuvrail.example.com,")
    assert result == ["https://example.com", "https://nuvrail.example.com"]


# ---------------------------------------------------------------------------
# CORS middleware behaviour tests
# ---------------------------------------------------------------------------

PROD_ORIGINS = ["https://example.com", "https://nuvrail.example.com"]


@pytest.mark.asyncio
async def test_allowed_origin_receives_acao_header() -> None:
    """An allowed origin gets Access-Control-Allow-Origin echoed back."""
    app = _make_app(PROD_ORIGINS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ping", headers={"Origin": "https://example.com"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://example.com"


@pytest.mark.asyncio
async def test_second_allowed_origin_receives_acao_header() -> None:
    app = _make_app(PROD_ORIGINS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ping", headers={"Origin": "https://nuvrail.example.com"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://nuvrail.example.com"


@pytest.mark.asyncio
async def test_disallowed_origin_blocked() -> None:
    """A foreign origin must not receive Access-Control-Allow-Origin."""
    app = _make_app(PROD_ORIGINS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ping", headers={"Origin": "https://evil.com"})
    assert resp.status_code == 200  # request still succeeds; CORS is browser-enforced
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_no_origin_header_gets_no_acao_header() -> None:
    """Requests without an Origin header (e.g. direct API calls) are unaffected."""
    app = _make_app(PROD_ORIGINS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ping")
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_preflight_allowed_origin() -> None:
    """Preflight OPTIONS from an allowed origin returns 200 with CORS headers."""
    app = _make_app(PROD_ORIGINS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.options(
            "/ping",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://example.com"


@pytest.mark.asyncio
async def test_preflight_disallowed_origin() -> None:
    """Preflight OPTIONS from a disallowed origin must not return CORS headers."""
    app = _make_app(PROD_ORIGINS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.options(
            "/ping",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_wildcard_mode_allows_any_origin() -> None:
    """Dev fallback: wildcard origins allow any origin."""
    app = _make_app(["*"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ping", headers={"Origin": "https://anything.example"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Production fail-closed resolution (api.main.resolve_cors_origins)
# ---------------------------------------------------------------------------


def test_resolve_explicit_origins_parsed() -> None:
    from api.main import resolve_cors_origins

    assert resolve_cors_origins("https://a.com, https://b.com", "prod") == [
        "https://a.com", "https://b.com",
    ]


def test_resolve_empty_in_dev_falls_back_to_wildcard() -> None:
    from api.main import resolve_cors_origins

    assert resolve_cors_origins("", "dev") == ["*"]
    assert resolve_cors_origins("   ", "test") == ["*"]


@pytest.mark.parametrize("env", ["prod", "production", "Prod", "  PRODUCTION  "])
def test_resolve_empty_in_production_fails_closed(env: str) -> None:
    """An unset CORS allowlist in production must refuse to start, not wildcard."""
    from api.main import resolve_cors_origins

    with pytest.raises(RuntimeError, match="NUVRAIL_CORS_ORIGINS must be set"):
        resolve_cors_origins("", env)


def test_resolve_explicit_origins_in_production_ok() -> None:
    """Production with an explicit allowlist resolves normally (no raise)."""
    from api.main import resolve_cors_origins

    assert resolve_cors_origins("https://app.nuvrail.com", "production") == [
        "https://app.nuvrail.com",
    ]
