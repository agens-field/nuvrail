"""
Unit tests for the per-agent outbound send rate limiter (anti-spam / R13).

What's under test
-----------------
``gateway.send_rate_limiter.enforce_send_rate`` reads executed ``smtp_send``
audit rows for an agent within a rolling window and refuses (raises
``SendRateLimitExceeded``) when the new send would push the agent over its cap.

Test DB layout per case
-----------------------
  init_db(tmp) ──► seed audit_log with executed smtp_send rows
                   (some recent, some outside the window, some other agents)
                        │
                        ▼
            enforce_send_rate(agent, n, window, cap)
                        │
        ┌───────────────┴────────────────┐
   under cap → returns None        over cap → raises SendRateLimitExceeded

Counting is by RECIPIENTS (SUM of detail.recipient_count), not by row, so a
single op to many recipients is weighted as many messages. Legacy rows with no
recipient_count count as 1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.audit import record_audit_event
from gateway.send_rate_limiter import (
    SendRateConfig,
    SendRateLimitExceeded,
    enforce_send_rate,
    load_send_rate_config,
)
from gateway.state_db import init_db


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "send_rate.db"
    await init_db(path)
    return path


async def _seed_send(
    db_path: Path,
    *,
    agent_id: str,
    ts: int,
    recipient_count: int | None,
    event: str = "executed",
    op_type: str = "smtp_send",
) -> None:
    """Write one audit row mimicking an executed (or other) send."""
    detail = (
        json.dumps({"recipient_count": recipient_count})
        if recipient_count is not None
        else None
    )
    await record_audit_event(
        db_path, timestamp=ts, event=event, actor="human",
        operation_id=f"op_{ts}_{agent_id}", agent_id=agent_id,
        op_type=op_type, detail=detail,
    )


_CFG = SendRateConfig(max_per_window=10, window_seconds=3600)


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


async def test_under_cap_allows(db_path: Path) -> None:
    now = 1_000_000
    # 8 recipients sent recently; cap 10. One more (1 recipient) → 9, allowed.
    await _seed_send(db_path, agent_id="a1", ts=now - 10, recipient_count=8)
    await enforce_send_rate("a1", 1, db_path=db_path, config=_CFG, now=now)


async def test_at_cap_boundary_allows_exactly_cap(db_path: Path) -> None:
    now = 1_000_000
    # 9 used + 1 requested == cap 10 → allowed (cap is inclusive).
    await _seed_send(db_path, agent_id="a1", ts=now - 10, recipient_count=9)
    await enforce_send_rate("a1", 1, db_path=db_path, config=_CFG, now=now)


async def test_over_cap_refuses(db_path: Path) -> None:
    now = 1_000_000
    await _seed_send(db_path, agent_id="a1", ts=now - 10, recipient_count=10)
    with pytest.raises(SendRateLimitExceeded) as ei:
        await enforce_send_rate("a1", 1, db_path=db_path, config=_CFG, now=now)
    assert ei.value.used == 10
    assert ei.value.requested == 1
    assert ei.value.cap == 10


async def test_multi_recipient_send_counts_each_recipient(db_path: Path) -> None:
    now = 1_000_000
    # 5 already used; a single op to 6 recipients would make 11 > 10 → refuse.
    await _seed_send(db_path, agent_id="a1", ts=now - 10, recipient_count=5)
    with pytest.raises(SendRateLimitExceeded):
        await enforce_send_rate("a1", 6, db_path=db_path, config=_CFG, now=now)


async def test_old_sends_outside_window_dont_count(db_path: Path) -> None:
    now = 1_000_000
    # 10 recipients but sent 2h ago — outside the 1h window → ignored.
    await _seed_send(db_path, agent_id="a1", ts=now - 7200, recipient_count=10)
    await enforce_send_rate("a1", 5, db_path=db_path, config=_CFG, now=now)


async def test_other_agents_dont_count(db_path: Path) -> None:
    now = 1_000_000
    await _seed_send(db_path, agent_id="other", ts=now - 10, recipient_count=10)
    # a1 has sent nothing → its own send is allowed.
    await enforce_send_rate("a1", 5, db_path=db_path, config=_CFG, now=now)


async def test_non_smtp_and_non_executed_rows_dont_count(db_path: Path) -> None:
    now = 1_000_000
    # A staged (not executed) send and an executed non-smtp op must not count.
    await _seed_send(
        db_path, agent_id="a1", ts=now - 10, recipient_count=10, event="staged"
    )
    await _seed_send(
        db_path, agent_id="a1", ts=now - 10, recipient_count=10, op_type="trash"
    )
    await enforce_send_rate("a1", 5, db_path=db_path, config=_CFG, now=now)


async def test_legacy_row_without_recipient_count_counts_as_one(db_path: Path) -> None:
    now = 1_000_000
    # 9 legacy executed sends, each weighted 1 → 9 used. +1 == cap 10 → ok;
    # +2 → 11 > 10 → refuse.
    for i in range(9):
        await _seed_send(db_path, agent_id="a1", ts=now - i - 1, recipient_count=None)
    await enforce_send_rate("a1", 1, db_path=db_path, config=_CFG, now=now)
    with pytest.raises(SendRateLimitExceeded):
        await enforce_send_rate("a1", 2, db_path=db_path, config=_CFG, now=now)


async def test_disabled_limiter_is_noop(db_path: Path) -> None:
    now = 1_000_000
    await _seed_send(db_path, agent_id="a1", ts=now - 10, recipient_count=1000)
    disabled = SendRateConfig(max_per_window=0, window_seconds=3600)
    # Way over any sane cap, but limiter disabled → allowed.
    await enforce_send_rate("a1", 5000, db_path=db_path, config=disabled, now=now)


async def test_zero_recipient_send_treated_as_one(db_path: Path) -> None:
    now = 1_000_000
    # cap exactly used; a 0-recipient send must not be a free bypass.
    await _seed_send(db_path, agent_id="a1", ts=now - 10, recipient_count=10)
    with pytest.raises(SendRateLimitExceeded):
        await enforce_send_rate("a1", 0, db_path=db_path, config=_CFG, now=now)


async def test_none_agent_id_isolated(db_path: Path) -> None:
    now = 1_000_000
    # Rows with a concrete agent must not count against the NULL-agent bucket.
    await _seed_send(db_path, agent_id="a1", ts=now - 10, recipient_count=10)
    # NULL agent (env-fallback test sends) counts only its own rows.
    await enforce_send_rate(None, 5, db_path=db_path, config=_CFG, now=now)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NUVRAIL_SEND_MAX_PER_WINDOW", raising=False)
    monkeypatch.delenv("NUVRAIL_SEND_WINDOW_SECONDS", raising=False)
    cfg = load_send_rate_config()
    assert cfg.max_per_window == 100
    assert cfg.window_seconds == 3600
    assert cfg.enabled is True


def test_config_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUVRAIL_SEND_MAX_PER_WINDOW", "0")
    cfg = load_send_rate_config()
    assert cfg.enabled is False


def test_config_invalid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo must NOT silently disable anti-spam.
    monkeypatch.setenv("NUVRAIL_SEND_MAX_PER_WINDOW", "not-a-number")
    monkeypatch.setenv("NUVRAIL_SEND_WINDOW_SECONDS", "-5")
    cfg = load_send_rate_config()
    assert cfg.max_per_window == 100
    assert cfg.window_seconds == 3600
