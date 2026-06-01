"""
Entitlements seam — where per-plan limits and feature gating plug in.

Core ships ``OpenCoreEntitlements``: nothing is gated, no quotas — the public
free product. The enterprise package registers a ``PlanEntitlements``
implementation (reading ``users.plan_tier``) via ``load_plugins()``, which
enforces the paid tiers — e.g. the agent quota (Pro = more than one agent) and
access to the auto-approval rules engine (Enterprise) — raising HTTP 402 when a
limit is hit.

The agent limit is deliberately NOT hard-coded in core: a quota is a billing
policy, not a property of the open product. Core only exposes the seam
(``assert_can_create_agent``); the policy lives in the enterprise provider.
See docs/REPO_SPLIT.md.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# Canonical feature keys reported by GET /api/v1/features and checked by gates.
KNOWN_FEATURES = ("auto_approval_rules", "multi_agent")


@runtime_checkable
class Entitlements(Protocol):
    async def assert_can_create_agent(self, user: dict, current_count: int) -> None:
        """Raise (e.g. HTTPException 402) if the user may not create another agent."""

    async def feature_enabled(self, user: dict, feature: str) -> bool:
        ...

    async def features(self, user: dict) -> dict:
        """Return a JSON-able summary of the user's plan, features, and limits."""


class OpenCoreEntitlements:
    """Public default: no quotas, and only features present in core are enabled.

    A feature is "available" in open core only if its code ships in core. The
    auto-approval rules engine is an enterprise plugin, so it is reported as
    unavailable here; multi-agent has no quota in open core, so it is available.
    """

    # Features whose implementation ships in the open-core build.
    _OPEN_CORE_FEATURES = {
        "multi_agent": True,
        "auto_approval_rules": False,  # provided by the enterprise plugin
    }

    async def assert_can_create_agent(self, user: dict, current_count: int) -> None:
        return

    async def feature_enabled(self, user: dict, feature: str) -> bool:
        return self._OPEN_CORE_FEATURES.get(feature, False)

    async def features(self, user: dict) -> dict:
        return {
            "plan": "open-core",
            "features": {name: self._OPEN_CORE_FEATURES.get(name, False) for name in KNOWN_FEATURES},
            "limits": {"max_agents": None},  # None = unlimited
        }


_active: Entitlements = OpenCoreEntitlements()


def register_entitlements(impl: Entitlements) -> None:
    """Replace the active entitlements provider (called by the enterprise plugin)."""
    global _active
    _active = impl


def reset_entitlements() -> None:
    """Restore the open-core default. For tests."""
    global _active
    _active = OpenCoreEntitlements()


def entitlements() -> Entitlements:
    """Return the active entitlements provider."""
    return _active
