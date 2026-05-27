"""
Nuvrail Approval REST API — Milestone 1.0+.

FastAPI application exposing:
  /api/v1/operations  — list, approve, reject
  /api/v1/audit       — audit log (read-only)

Base URL: http://localhost:8080/api/v1

Background tasks:
  - Operation expiry loop: runs every hour, expires pending ops past 48h,
    reverts local state, writes audit entries.

Sub-milestone: 1.0 (operations CRUD)
Sub-milestone: 1.2 (audit, expiry)
Sub-milestone: 3.3 (auto-approval rules)
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.limiter import limiter
from api.routes import audit, auth, oauth2, operations, push, rules
from api.routes import account
from gateway.expiry import run_expiry_loop
from gateway.scrubber import run_scrubber_loop
from gateway.state_db import DB_PATH, init_db

logger = logging.getLogger(__name__)

_EXPIRY_INTERVAL = float(os.environ.get("NUVRAIL_EXPIRY_INTERVAL_SECONDS", "3600"))
_EXPIRY_INITIAL_DELAY = float(os.environ.get("NUVRAIL_EXPIRY_INITIAL_DELAY_SECONDS", "60"))
_SCRUBBER_INTERVAL = float(os.environ.get("NUVRAIL_SCRUBBER_INTERVAL_SECONDS", "3600"))
_SCRUBBER_INITIAL_DELAY = float(os.environ.get("NUVRAIL_SCRUBBER_INITIAL_DELAY_SECONDS", "90"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize DB and start background tasks on startup; clean up on shutdown."""
    await init_db()

    # Start operation expiry background loop
    expiry_task = asyncio.create_task(
        run_expiry_loop(
            db_path=DB_PATH,
            interval_seconds=_EXPIRY_INTERVAL,
            initial_delay_seconds=_EXPIRY_INITIAL_DELAY,
        ),
        name="nuvrail-expiry-loop",
    )
    # Start body-preview scrubber background loop (issue #29)
    scrubber_task = asyncio.create_task(
        run_scrubber_loop(
            db_path=DB_PATH,
            interval_seconds=_SCRUBBER_INTERVAL,
            initial_delay_seconds=_SCRUBBER_INITIAL_DELAY,
        ),
        name="nuvrail-scrubber-loop",
    )
    logger.info(
        "Nuvrail API started — expiry loop every %.0fs, scrubber loop every %.0fs",
        _EXPIRY_INTERVAL, _SCRUBBER_INTERVAL,
    )

    yield

    # Cancel background tasks on shutdown
    for task in (expiry_task, scrubber_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Nuvrail API shutdown — background loops stopped")


app = FastAPI(title="Nuvrail Approval API", version="0.1.0", lifespan=lifespan)

# Rate limiting (slowapi) — attached before CORS so 429s get proper headers.
# The limiter is stored on app.state so route decorators can reference it.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_CORS_ORIGINS_RAW = os.environ.get("NUVRAIL_CORS_ORIGINS", "")
if _CORS_ORIGINS_RAW.strip():
    _CORS_ORIGINS = [o.strip() for o in _CORS_ORIGINS_RAW.split(",") if o.strip()]
else:
    # Default: allow all origins in dev (no env var set).
    # Always set NUVRAIL_CORS_ORIGINS in production.
    logger.warning(
        "NUVRAIL_CORS_ORIGINS is not set — allowing all origins (*). "
        "Set it to your deployed URL (e.g. https://test.nuvrail.com) in production."
    )
    _CORS_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(operations.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(push.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(oauth2.router, prefix="/api/v1")
app.include_router(rules.router, prefix="/api/v1")
app.include_router(account.router, prefix="/api/v1")
