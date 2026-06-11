"""
Background-loop heartbeats — liveness/health for the asyncio worker loops.

The expiry / scrubber / retention / scheduler / audit-verify loops each catch
their own exceptions and only ``logger.error`` on failure. That covers a *work*
error, but not the loop **dying** (task cancelled, an error escaping the inner
try, an ``await`` that never returns). A silently-dead scrubber means message
bodies linger past the promised 7 days with no signal; a dead retention loop
means the 12-month GDPR purge silently stops. Both break privacy promises.

This module is the missing signal: every loop records a heartbeat each iteration
via ``record_heartbeat``. ``get_loop_health`` reports, per loop, when it last ran
/ last succeeded and whether it has gone **stale** (no heartbeat in well over its
own interval). The /health endpoint surfaces this so monitoring can alert on a
loop that stopped — without affecting the liveness status code (a stale worker
must not cause fly.io to kill an otherwise-healthy API process).

In-process and best-effort by design: heartbeats live in module memory, scoped
to one process, and recording one must never be able to break the loop it tracks.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# A loop is considered stale once it has missed this many of its own intervals.
# 3× tolerates a slow run / one skipped tick without crying wolf, while still
# catching a loop that has actually stopped.
_DEFAULT_STALE_FACTOR = 3.0

# Floor for the staleness deadline so very fast loops (e.g. the 30s scheduler)
# don't flap stale over a brief hiccup.
_MIN_STALE_SECONDS = 120.0


@dataclass
class _Heartbeat:
    interval_seconds: float
    last_run_at: int | None = None
    last_success_at: int | None = None
    last_ok: bool = True
    last_detail: str | None = None
    runs: int = 0


_HEARTBEATS: dict[str, _Heartbeat] = {}


def record_heartbeat(
    name: str,
    *,
    interval_seconds: float,
    ok: bool = True,
    detail: str | None = None,
    now: int | None = None,
) -> None:
    """Record that loop ``name`` just completed an iteration.

    Call once per loop iteration (on both the success and the handled-error
    path) so a present-but-failing loop is distinguishable from a dead one.
    Never raises — a telemetry failure must not take down the loop.
    """
    try:
        ts = now if now is not None else int(time.time())
        hb = _HEARTBEATS.get(name)
        if hb is None:
            hb = _Heartbeat(interval_seconds=interval_seconds)
            _HEARTBEATS[name] = hb
        hb.interval_seconds = interval_seconds
        hb.last_run_at = ts
        hb.last_ok = ok
        hb.last_detail = detail
        hb.runs += 1
        if ok:
            hb.last_success_at = ts
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("[loop-health] failed to record heartbeat for %r", name, exc_info=True)


def _stale_deadline(interval_seconds: float, stale_factor: float) -> float:
    return max(_MIN_STALE_SECONDS, interval_seconds * stale_factor)


def get_loop_health(
    *, now: int | None = None, stale_factor: float = _DEFAULT_STALE_FACTOR
) -> dict:
    """Return a summary of every registered loop's heartbeat.

    ``ok`` is True only if every loop has run recently and its last run
    succeeded. A loop with no heartbeat yet (hasn't reached its first run) is
    reported but not counted stale until its first deadline elapses.
    """
    ts = now if now is not None else int(time.time())
    loops: dict[str, dict] = {}
    overall_ok = True
    for name, hb in _HEARTBEATS.items():
        reference = hb.last_success_at if hb.last_success_at is not None else hb.last_run_at
        age = (ts - reference) if reference is not None else None
        deadline = _stale_deadline(hb.interval_seconds, stale_factor)
        stale = age is None or age > deadline
        loops[name] = {
            "last_run_at": hb.last_run_at,
            "last_success_at": hb.last_success_at,
            "age_seconds": age,
            "interval_seconds": hb.interval_seconds,
            "stale_after_seconds": deadline,
            "last_ok": hb.last_ok,
            "stale": stale,
            "runs": hb.runs,
        }
        if stale or not hb.last_ok:
            overall_ok = False
    return {"ok": overall_ok, "loops": loops}


def reset_heartbeats() -> None:
    """Clear all heartbeats. For tests."""
    _HEARTBEATS.clear()
