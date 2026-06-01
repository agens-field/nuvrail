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
    """The provider is process-global; save/restore it around each test."""
    saved = ext._auto_decision_provider
    yield
    ext._auto_decision_provider = saved


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


async def test_load_plugins_registers_builtin_rules():
    ext.reset_auto_decision_provider()
    assert ext._auto_decision_provider is None

    ext.load_plugins(app=None)

    # The in-core rules engine should now be the registered provider.
    from gateway.rules import auto_decision
    assert ext._auto_decision_provider is auto_decision


def test_load_plugins_is_exception_safe(monkeypatch):
    """A broken entry-point lookup must not raise out of load_plugins."""
    def boom(*a, **k):
        raise RuntimeError("registry down")

    monkeypatch.setattr(ext.importlib.metadata, "entry_points", boom)
    # Should swallow the error and still complete.
    ext.load_plugins(app=None)
    assert ext._plugins_loaded is True
