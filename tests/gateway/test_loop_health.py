"""
Unit tests for gateway.loop_health — background-loop heartbeat registry.

Covers: recording heartbeats, success vs handled-error, staleness detection
against each loop's own interval, the min-staleness floor, and overall ok roll-up.
"""
from __future__ import annotations

import pytest

from gateway import loop_health


@pytest.fixture(autouse=True)
def _clean_registry():
    loop_health.reset_heartbeats()
    yield
    loop_health.reset_heartbeats()


def test_unknown_loop_absent() -> None:
    assert loop_health.get_loop_health()["loops"] == {}
    assert loop_health.get_loop_health()["ok"] is True


def test_fresh_heartbeat_not_stale() -> None:
    loop_health.record_heartbeat("scrubber", interval_seconds=3600, now=1000)
    health = loop_health.get_loop_health(now=1000)
    hb = health["loops"]["scrubber"]
    assert hb["stale"] is False
    assert hb["last_success_at"] == 1000
    assert hb["age_seconds"] == 0
    assert health["ok"] is True


def test_goes_stale_after_three_intervals() -> None:
    loop_health.record_heartbeat("scrubber", interval_seconds=3600, now=1000)
    # 2 intervals later → still fresh; just past 3 intervals → stale.
    assert loop_health.get_loop_health(now=1000 + 2 * 3600)["loops"]["scrubber"]["stale"] is False
    stale = loop_health.get_loop_health(now=1000 + 3 * 3600 + 1)
    assert stale["loops"]["scrubber"]["stale"] is True
    assert stale["ok"] is False


def test_min_staleness_floor_for_fast_loops() -> None:
    # Scheduler runs every 30s; 3x = 90s would flap. Floor is 120s.
    loop_health.record_heartbeat("scheduler", interval_seconds=30, now=1000)
    assert loop_health.get_loop_health(now=1000 + 100)["loops"]["scheduler"]["stale"] is False
    assert loop_health.get_loop_health(now=1000 + 121)["loops"]["scheduler"]["stale"] is True


def test_handled_error_keeps_liveness_but_flags_not_ok() -> None:
    # The loop ran (alive) but its work errored: last_ok False, last_success unset.
    loop_health.record_heartbeat(
        "retention", interval_seconds=86400, ok=False, detail="boom", now=1000
    )
    health = loop_health.get_loop_health(now=1000)
    hb = health["loops"]["retention"]
    assert hb["stale"] is False          # it DID run — not dead
    assert hb["last_ok"] is False
    assert hb["last_success_at"] is None
    assert health["ok"] is False         # but overall not ok (work failing)


def test_success_after_error_clears_not_ok() -> None:
    loop_health.record_heartbeat("retention", interval_seconds=86400, ok=False, now=1000)
    loop_health.record_heartbeat("retention", interval_seconds=86400, ok=True, now=1010)
    health = loop_health.get_loop_health(now=1010)
    assert health["loops"]["retention"]["last_ok"] is True
    assert health["loops"]["retention"]["last_success_at"] == 1010
    assert health["ok"] is True


def test_runs_counter_increments() -> None:
    for i in range(3):
        loop_health.record_heartbeat("expiry", interval_seconds=3600, now=1000 + i)
    assert loop_health.get_loop_health(now=1002)["loops"]["expiry"]["runs"] == 3


def test_one_stale_loop_makes_overall_not_ok() -> None:
    # Check well past retention's 3-day staleness deadline (3 * 86400).
    check_at = 1 + 3 * 86400 + 1
    loop_health.record_heartbeat("expiry", interval_seconds=3600, now=check_at)  # fresh
    loop_health.record_heartbeat("retention", interval_seconds=86400, now=1)     # ancient
    health = loop_health.get_loop_health(now=check_at)
    assert health["loops"]["expiry"]["stale"] is False
    assert health["loops"]["retention"]["stale"] is True
    assert health["ok"] is False
