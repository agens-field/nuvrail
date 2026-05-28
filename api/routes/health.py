"""
Health check endpoint — fly.io (and any other probe) liveness check.

GET /health
  No auth required.
  Returns 200 with {"status": "ok", "timestamp": "<iso8601>"}.

fly.toml wires this at [checks.api_health] path = "/health".
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", include_in_schema=True)
async def health_check() -> dict:
    """Liveness probe — always returns 200 while the process is up."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
