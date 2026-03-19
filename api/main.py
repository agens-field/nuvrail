"""
Nuvrail Approval REST API — Milestone 1.0.

FastAPI application exposing:
  /api/v1/operations  — list, approve, reject

Base URL: http://localhost:8080/api/v1

Sub-milestone: 1.0 (operations CRUD)
Sub-milestone: 1.2 (audit)
Sub-milestone: 3.3 (auto-approval rules)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import operations
from gateway.state_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize DB on startup."""
    await init_db()
    yield


app = FastAPI(title="Nuvrail Approval API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(operations.router, prefix="/api/v1")
