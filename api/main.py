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

from api.routes import audit, operations
from gateway.expiry import run_expiry_loop
from gateway.state_db import DB_PATH, init_db

logger = logging.getLogger(__name__)

_EXPIRY_INTERVAL = float(os.environ.get("NUVRAIL_EXPIRY_INTERVAL_SECONDS", "3600"))
_EXPIRY_INITIAL_DELAY = float(os.environ.get("NUVRAIL_EXPIRY_INITIAL_DELAY_SECONDS", "60"))


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
    logger.info("Nuvrail API started — expiry loop running every %.0fs", _EXPIRY_INTERVAL)

    yield

    # Cancel the expiry loop on shutdown
    expiry_task.cancel()
    try:
        await expiry_task
    except asyncio.CancelledError:
        pass
    logger.info("Nuvrail API shutdown — expiry loop stopped")


app = FastAPI(title="Nuvrail Approval API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(operations.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
