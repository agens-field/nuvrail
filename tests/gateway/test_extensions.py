"""
Tests for the core extension seam (gateway/extensions.py).

- run_auto_decision returns None when no provider is registered (open core).
- run_auto_decision routes to a registered provider.
- load_plugins() registers the built-in rules engine as the provider.
"""
from __future__ import annotations

import pytest

import gateway.extensions as ext


@pytest.fixture(autouse=True)
def _preserve_provider():
    """Provider and migrations are process-global; save/restore around each test."""
    saved = ext._auto_decision_provider
    saved_migs = list(ext._migrations)
    yield
    ext._auto_decision_provider = saved
    ext._migrations[:] = saved_migs


async def test_no_provider_returns_none():
    ext.reset_auto_decision_provider()
    result = await ext.run_auto_decision({"op_type": "trash"}, db_path=None, user_id=1)
    assert result is None


async def test_routes_to_registered_provider():
    ext.reset_auto_decision_provider()
    seen = {}

    async def fake_provider(op, *, db_path, user_id):
        seen["op"] = op
        seen["user_id"] = user_id
        return {"action": "approve", "rule": {"id": 7}}

    ext.register_auto_decision_provider(fake_provider)
    result = await ext.run_auto_decision({"op_type": "trash"}, db_path="db", user_id=42)

    assert result == {"action": "approve", "rule": {"id": 7}}
    assert seen["user_id"] == 42
    assert seen["op"] == {"op_type": "trash"}


async def test_load_plugins_no_builtin_provider_in_open_core():
    """Open core ships no auto-decision provider: load_plugins registers none.

    The rules engine is an enterprise plugin; with no plugin installed, the
    provider stays unset and staging follows the manual-approval path.
    """
    ext.reset_auto_decision_provider()
    ext.load_plugins(app=None)
    assert ext._auto_decision_provider is None


async def test_register_migration_runs_in_init_db(tmp_path):
    from gateway.state_db import get_db, init_db

    ext.reset_migrations()
    ran = {}

    async def probe_migration(db):
        await db.execute("CREATE TABLE IF NOT EXISTS _ext_probe (x INTEGER)")
        ran["called"] = True

    ext.register_migration(probe_migration)
    # Registering the same migration twice must not duplicate it.
    ext.register_migration(probe_migration)
    assert len(ext.get_migrations()) == 1

    db_path = tmp_path / "mig.db"
    await init_db(db_path)

    assert ran.get("called") is True
    async with get_db(db_path) as db:
        async with db.execute("PRAGMA table_info(_ext_probe)") as cur:
            cols = await cur.fetchall()
    assert cols, "migration-created table should exist after init_db"


def test_load_plugins_is_exception_safe(monkeypatch):
    """A broken entry-point lookup must not raise out of load_plugins."""
    def boom(*a, **k):
        raise RuntimeError("registry down")

    monkeypatch.setattr(ext.importlib.metadata, "entry_points", boom)
    # Should swallow the error and still complete.
    ext.load_plugins(app=None)
    assert ext._plugins_loaded is True
