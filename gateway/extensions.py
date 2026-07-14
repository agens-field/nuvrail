"""
Core extension points for the open-core / enterprise plugin split.

Two mechanisms live here:

1. Auto-decision provider — the seam the auto-approval rules engine plugs into.
   Core's staging path calls ``run_auto_decision()``; with no provider registered
   it returns ``None`` and operations follow the normal manual-approval flow.
   The in-core rules engine registers itself today (``gateway/rules.py``); when
   the rules engine is extracted to the enterprise package it will register here
   instead, via ``load_plugins()`` and the ``nuvrail.plugins`` entry point.

2. Plugin discovery — ``load_plugins()`` registers built-ins and then loads any
   installed packages advertising the ``nuvrail.plugins`` entry-point group. Each
   plugin exposes a ``setup(app)`` callable that registers routers / migrations /
   entitlements / auto-decision providers.

Neither mechanism changes behaviour in the public build: with no plugin
installed, the rules engine that ships in core registers the only provider and
there are no external entry points to load.
"""
from __future__ import annotations

import importlib.metadata
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# A provider takes ``(op, *, db_path, user_id)`` and returns either ``None`` (no
# decision) or a dict with at least ``{"action": "approve"|"reject", "rule": dict}``.
AutoDecisionProvider = Callable[..., Awaitable[Optional[dict]]]

# A migration takes an open aiosqlite connection and applies idempotent schema
# changes (e.g. ADD COLUMN guarded by a PRAGMA check). Run during init_db().
Migration = Callable[..., Awaitable[None]]

_auto_decision_provider: Optional[AutoDecisionProvider] = None
_migrations: list[Migration] = []


def register_auto_decision_provider(fn: AutoDecisionProvider) -> None:
    """Register the callable that decides auto-approve/reject for staged ops."""
    global _auto_decision_provider
    _auto_decision_provider = fn


def reset_auto_decision_provider() -> None:
    """Clear the registered provider. For tests."""
    global _auto_decision_provider
    _auto_decision_provider = None


def register_migration(fn: Migration) -> None:
    """Register a plugin schema migration to run during init_db().

    The callable receives the open aiosqlite connection and must be idempotent
    (guard ADD COLUMN with a PRAGMA table_info check) since init_db runs on every
    startup. Registration order is preserved.
    """
    if fn not in _migrations:
        _migrations.append(fn)


def get_migrations() -> list[Migration]:
    """Return the registered plugin migrations, in registration order."""
    return list(_migrations)


def reset_migrations() -> None:
    """Clear registered migrations. For tests."""
    _migrations.clear()


async def run_auto_decision(op: dict, *, db_path, user_id) -> Optional[dict]:
    """Return an auto-decision for a staged op, or ``None``.

    ``None`` means "no decision" — either no provider is registered (open core
    with no rules engine) or no rule matched. Callers then fall through to the
    normal manual-approval / notify path.

    A non-None result is a dict with ``action`` ('approve' | 'reject') and the
    matched ``rule`` (for audit attribution).
    """
    provider = _auto_decision_provider
    if provider is None:
        return None
    return await provider(op, db_path=db_path, user_id=user_id)


_plugins_loaded = False


def load_plugins(app=None) -> None:
    """Register built-in providers and load external nuvrail plugins.

    Idempotent and exception-safe: a failing plugin is logged, never fatal, so a
    bad optional plugin can't take down the proxies or API. Pass the FastAPI
    ``app`` from the API process so plugins can register routers; proxies call it
    with ``app=None``.
    """
    global _plugins_loaded

    # External plugins via entry points (none in the public build). The
    # nuvrail-enterprise package, when installed, registers the auto-approval
    # rules engine (auto-decision provider) and the /rules router here.
    # Open core has no built-in providers — operations stay manual-approval.
    try:
        eps = importlib.metadata.entry_points(group="nuvrail.plugins")
    except Exception:  # noqa: BLE001  (older importlib API / lookup failure)
        eps = []
    for ep in eps:
        try:
            ep.load()(app)
            logger.info("[extensions] Loaded plugin: %s", ep.name)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[extensions] Failed to load plugin: %s", getattr(ep, "name", "?")
            )

    _plugins_loaded = True
