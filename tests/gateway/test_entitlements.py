"""
Tests for the entitlements seam (gateway/entitlements.py).

The open-core default gates nothing and imposes no quota. A registered provider
replaces it (this is how the enterprise package enforces paid tiers).
"""
from __future__ import annotations

import pytest

import gateway.entitlements as ent


@pytest.fixture(autouse=True)
def _restore_entitlements():
    """Pin the open-core default around every test in this module.

    The active entitlements provider is a PROCESS-GLOBAL (``entitlements._active``).
    When the enterprise plugin is installed, ``load_plugins()`` — triggered by
    the API app fixtures in other test modules — calls ``register_entitlements``
    to swap in ``PlanEntitlements`` and never resets it. If those tests run
    before this module (e.g. ``tests/api`` before ``tests/gateway``), that
    enterprise provider leaks in here and these ``open_core`` tests silently run
    against the WRONG provider — which reads a ``users`` table that this DB-less
    module never creates, raising ``no such table: users``.

    Reset to the open-core default BEFORE each test (not just after) so this
    module is order-independent and actually exercises open core. Mirrors the
    save/reset/restore guard already used in tests/api/test_rate_limiting.py.
    """
    ent.reset_entitlements()
    yield
    ent.reset_entitlements()


async def test_open_core_allows_any_agent_count():
    e = ent.entitlements()
    # Must not raise regardless of how many agents already exist.
    await e.assert_can_create_agent({"id": 1}, current_count=0)
    await e.assert_can_create_agent({"id": 1}, current_count=999)


async def test_open_core_feature_availability():
    e = ent.entitlements()
    # multi-agent has no quota in open core → available.
    assert await e.feature_enabled({"id": 1}, "multi_agent") is True
    # the rules engine is an enterprise plugin → unavailable in open core.
    assert await e.feature_enabled({"id": 1}, "auto_approval_rules") is False
    # unknown features default to unavailable.
    assert await e.feature_enabled({"id": 1}, "nonexistent") is False


async def test_open_core_features_payload_shape():
    payload = await ent.entitlements().features({"id": 1})
    assert payload["plan"] == "open-core"
    assert payload["limits"]["max_agents"] is None
    assert set(payload["features"]) == set(ent.KNOWN_FEATURES)
    assert payload["features"]["multi_agent"] is True
    assert payload["features"]["auto_approval_rules"] is False


async def test_register_and_reset_entitlements():
    class DenyAll:
        async def assert_can_create_agent(self, user, current_count):
            raise RuntimeError("capped")

        async def feature_enabled(self, user, feature):
            return False

        async def features(self, user):
            return {"plan": "test", "features": {}, "limits": {"max_agents": 1}}

    ent.register_entitlements(DenyAll())
    with pytest.raises(RuntimeError, match="capped"):
        await ent.entitlements().assert_can_create_agent({"id": 1}, 1)

    ent.reset_entitlements()
    # Back to open core: no raise.
    await ent.entitlements().assert_can_create_agent({"id": 1}, 1)
