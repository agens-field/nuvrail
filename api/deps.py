"""
Shared FastAPI dependencies.

Centralized here so both api.routes and api.auth can import without
creating circular dependencies. Override in tests via app.dependency_overrides.
"""
from __future__ import annotations

from pathlib import Path

from gateway.state_db import DB_PATH


def get_db_path() -> Path:
    """Return the database path. Override in tests via app.dependency_overrides."""
    return DB_PATH
