"""
Health check endpoint — fly.io (and any other probe) liveness check.

GET /health
  No auth required.
  Returns 200 with {"status": "ok", "timestamp": "<iso8601>", "loops": {...}}.

The ``loops`` block reports each background worker's heartbeat (see
gateway.loop_health) so monitoring can detect a silently-dead loop — e.g. a
stopped scrubber (bodies linger past 7 days) or retention purge (GDPR purge
halts). It is **informational**: the HTTP status stays 200 even when a loop is
stale, because a stale worker must not make fly.io kill an otherwise-healthy API
process. Alerting keys on ``loops_ok == false`` / ``loops.<name>.stale`` in the
body (or the per-loop ERROR logs).

fly.toml wires this at [checks.api_health] path = "/health".
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from gateway.loop_health import get_loop_health

router = APIRouter(tags=["health"])


@router.get("/health", include_in_schema=True)
async def health_check() -> dict:
    """Liveness probe — always returns 200 while the process is up.

    Includes background-loop heartbeats for observability; the status code does
    not depend on them (a stale loop must not trigger a process restart).
    """
    loop_health = get_loop_health()
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "loops_ok": loop_health["ok"],
        "loops": loop_health["loops"],
    }
