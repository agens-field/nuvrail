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
    yield
    ent.reset_entitlements()


async def test_open_core_allows_any_agent_count():
    e = ent.entitlements()
    # Must not raise regardless of how many agents already exist.
    await e.assert_can_create_agent({"id": 1}, current_count=0)
    await e.assert_can_create_agent({"id": 1}, current_count=999)


async def test_open_core_features_all_enabled():
    e = ent.entitlements()
    for feature in ent.KNOWN_FEATURES:
        assert await e.feature_enabled({"id": 1}, feature) is True


async def test_open_core_features_payload_shape():
    payload = await ent.entitlements().features({"id": 1})
    assert payload["plan"] == "open-core"
    assert payload["limits"]["max_agents"] is None
    assert set(payload["features"]) == set(ent.KNOWN_FEATURES)
    assert all(payload["features"].values())


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
