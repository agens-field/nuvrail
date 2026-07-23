"""
Per-sender outbound send rate limiter — anti-spam control (R13).

Why this exists
---------------
Agents — and auto-approval rules — dispatch real email through the upstream
relay. Without a ceiling, a single misbehaving agent (or a bad auto-send rule)
can blast unlimited mail, creating spam / CAN-SPAM / CASL exposure. The ToS
§4 "no spam" clause needs a *technical* enforcement point, not just words.

This module is that point. It is enforced in ``gateway.execution`` immediately
before the SMTP relay, on BOTH the human-approval path and the auto-rule path,
because both flow through ``execute_operation``.

                 execute_operation(smtp)
                          │
                          ▼
            ┌──────────────────────────────────┐
            │ enforce_send_rate(agent_id, n)    │
            │                                   │
            │  used = COUNT(*) of executed      │   durable: read from the
            │    smtp_send audit rows for this  │   hash-chained audit_log,
            │    agent within the window        │   NOT an in-memory counter
            │                                   │
            │  if used + n > cap:               │
            │     record 'send_rate_exceeded'   │   security + audit trail
            │     raise SendRateLimitExceeded   │   → fail CLOSED
            └──────────────────────────────────┘
                          │ allowed
                          ▼
                   aiosmtplib relay
                          │
                          ▼
                 audit 'executed' (smtp_send)  ← this is what future
                                                 windows count against

Design decisions (see also docs/anti-spam.md)
---------------------------------------------
* DURABLE, not in-memory. ``AuthAbuseProtector`` is in-memory because auth
  brute-force is ephemeral — losing the window on restart is harmless. Send
  abuse is the opposite: the whole point is a ceiling that survives our
  frequent deploys/restarts. We count from the existing ``audit_log``
  (``event='executed'`` + ``op_type='smtp_send'`` + ``agent_id`` + timestamp),
  which is already indexed on ``agent_id``. No new table, no new write path —
  the audit log is the source of truth. Survives the SQLite→Postgres migration
  unchanged.

* COUNT RECIPIENTS, not operations. One op addressed to 40 recipients is 40
  outbound messages. A spam ceiling must measure messages, so the cost of a
  send is ``len(recipients)``.

* FAIL CLOSED. If the count query errors, refuse the send. A security control
  that fails open is not a control.

* PUBLIC CORE. Basic anti-spam belongs in the free, self-hostable core — every
  operator gets a sane outbound ceiling. Enterprise plans may later *raise* or
  tune it per-tier; the floor lives here.

Scopes (defence in depth)
-------------------------
A per-agent hourly cap alone is not enough: a user with N agents could send
N×cap/hour, and an abuser staying just under the hourly cap could still send a
large volume slowly. So three ceilings are enforced together, each fail-closed:

  1. per-AGENT   / window (hour)   — the original runaway-agent ceiling
  2. per-ACCOUNT / window (hour)   — summed across all the user's agents
  3. per-ACCOUNT / day             — bounds slow-and-wide abuse under the hourly cap

The account-scoped checks resolve the owning user from the agent and sum
recipients across every agent that user owns (audit_log → agent_credentials).
They are skipped only when the owner can't be resolved (e.g. unit tests using a
synthetic agent id) — production sends always carry a real agent.

Configuration (env)
--------------------
* ``NUVRAIL_SEND_MAX_PER_WINDOW``          — per-agent cap per window. Default 100.
* ``NUVRAIL_SEND_WINDOW_SECONDS``          — rolling window length. Default 3600 (1h).
* ``NUVRAIL_ACCOUNT_SEND_MAX_PER_WINDOW``  — per-account cap per window. Default 300.
* ``NUVRAIL_ACCOUNT_SEND_MAX_PER_DAY``     — per-account cap per 24h. Default 1000.
Any cap of ``0`` disables that specific ceiling (escape hatch).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from gateway.state_db import get_db

logger = logging.getLogger(__name__)

# Default: 100 outbound messages / hour / agent. Conservative enough to stop a
# runaway agent quickly; generous enough not to break a legitimate single
# Phase-0 user. Both knobs are overridable via env.
_DEFAULT_MAX_PER_WINDOW = 100
_DEFAULT_WINDOW_SECONDS = 3600
# Account-scoped ceilings (summed across all of a user's agents).
_DEFAULT_ACCOUNT_MAX_PER_WINDOW = 300
_DEFAULT_ACCOUNT_MAX_PER_DAY = 1000
_DAY_SECONDS = 86400

# The audit event recorded when an agent is throttled.
SEND_RATE_EXCEEDED_EVENT = "send_rate_exceeded"


class SendRateLimitExceeded(Exception):
    """Raised when an agent's outbound send would exceed its window cap.

    Carries the numbers so the caller (and audit log) can report exactly why
    the send was refused.
    """

    def __init__(
        self,
        *,
        agent_id: str | None,
        used: int,
        requested: int,
        cap: int,
        window_seconds: int,
        scope: str = "agent",
    ) -> None:
        self.agent_id = agent_id
        self.used = used
        self.requested = requested
        self.cap = cap
        self.window_seconds = window_seconds
        self.scope = scope  # 'agent' | 'account' | 'account-daily'
        super().__init__(
            f"Outbound send rate limit exceeded ({scope}) for agent={agent_id!r}: "
            f"{used} already sent + {requested} requested > cap {cap} "
            f"per {window_seconds}s"
        )


@dataclass(frozen=True)
class SendRateConfig:
    """Resolved limiter configuration. Any cap of ``0`` disables that ceiling.

    ``account_*`` default to 0 here so a hand-constructed config (e.g. in a unit
    test) is per-agent only unless it opts in; production defaults come from
    ``load_send_rate_config`` below.
    """

    max_per_window: int
    window_seconds: int
    account_max_per_window: int = 0
    account_max_per_day: int = 0

    @property
    def enabled(self) -> bool:
        """True if the per-agent ceiling is active."""
        return self.max_per_window > 0


def load_send_rate_config() -> SendRateConfig:
    """Read limiter config from the environment, with conservative defaults.

    Invalid/negative values fall back to the default rather than disabling the
    control — a typo must not silently switch anti-spam off.
    """

    def _int_env(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            val = int(raw)
        except ValueError:
            logger.warning(
                "[send-rate] invalid %s=%r — using default %d", name, raw, default
            )
            return default
        if val < 0:
            logger.warning(
                "[send-rate] negative %s=%d — using default %d", name, val, default
            )
            return default
        return val

    return SendRateConfig(
        max_per_window=_int_env("NUVRAIL_SEND_MAX_PER_WINDOW", _DEFAULT_MAX_PER_WINDOW),
        window_seconds=max(
            1, _int_env("NUVRAIL_SEND_WINDOW_SECONDS", _DEFAULT_WINDOW_SECONDS)
        ),
        account_max_per_window=_int_env(
            "NUVRAIL_ACCOUNT_SEND_MAX_PER_WINDOW", _DEFAULT_ACCOUNT_MAX_PER_WINDOW
        ),
        account_max_per_day=_int_env(
            "NUVRAIL_ACCOUNT_SEND_MAX_PER_DAY", _DEFAULT_ACCOUNT_MAX_PER_DAY
        ),
    )


async def _count_recent_sends(
    agent_id: str | None, *, since_ts: int, db_path: Path
) -> int:
    """Count outbound messages this agent has already sent within the window.

    Counts executed ``smtp_send`` audit rows whose ``detail`` records a
    recipient count, summing recipients (not rows) so a single op to many
    recipients is weighted correctly. Rows without a recorded recipient count
    (e.g. ops staged before this field existed) count as 1 — the conservative
    floor for "a send happened".
    """
    # We store recipient_count in the executed audit detail JSON; SUM it.
    # COALESCE handles legacy rows / NULL detail → weight 1 per executed send.
    sql = """
        SELECT COALESCE(
            SUM(
                CASE
                    WHEN json_valid(detail)
                         AND json_extract(detail, '$.recipient_count') IS NOT NULL
                    THEN json_extract(detail, '$.recipient_count')
                    ELSE 1
                END
            ), 0)
        FROM audit_log
        WHERE event = 'executed'
          AND op_type = 'smtp_send'
          AND agent_id IS ?
          AND timestamp >= ?
    """
    async with get_db(db_path) as db, db.execute(sql, (agent_id, since_ts)) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def _count_recent_sends_for_user(
    user_id: int, *, since_ts: int, db_path: Path
) -> int:
    """Like ``_count_recent_sends`` but summed across ALL of a user's agents.

    Joins executed ``smtp_send`` audit rows to ``agent_credentials`` on
    ``agent_id`` to scope by owning user, so the account ceiling can't be
    side-stepped by spreading sends across multiple agents.
    """
    sql = """
        SELECT COALESCE(
            SUM(
                CASE
                    WHEN json_valid(a.detail)
                         AND json_extract(a.detail, '$.recipient_count') IS NOT NULL
                    THEN json_extract(a.detail, '$.recipient_count')
                    ELSE 1
                END
            ), 0)
        FROM audit_log a
        JOIN agent_credentials ac ON a.agent_id = ac.id
        WHERE a.event = 'executed'
          AND a.op_type = 'smtp_send'
          AND ac.user_id = ?
          AND a.timestamp >= ?
    """
    async with get_db(db_path) as db, db.execute(sql, (user_id, since_ts)) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def _resolve_owner_user_id(
    agent_id: str | None, *, db_path: Path
) -> int | None:
    """Return the user_id that owns ``agent_id``, or None if unresolvable.

    None means the account-scoped ceilings are skipped (e.g. synthetic agent ids
    in unit tests). Production sends always carry a real agent.
    """
    if agent_id is None:
        return None
    async with get_db(db_path) as db, db.execute(
        "SELECT user_id FROM agent_credentials WHERE id = ?", (agent_id,)
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


async def enforce_send_rate(
    agent_id: str | None,
    recipient_count: int,
    *,
    db_path: Path,
    config: SendRateConfig | None = None,
    now: int | None = None,
) -> None:
    """Refuse the send if it would push this agent over its window cap.

    Counts recipients (messages), not operations. Fails CLOSED: any error
    reading the count refuses the send. No-op when the limiter is disabled
    (``NUVRAIL_SEND_MAX_PER_WINDOW=0``).

    Raises:
        SendRateLimitExceeded: the send would exceed the cap. The caller is
            responsible for marking the operation failed + writing the
            ``send_rate_exceeded`` audit row (so it shares the failure path).
    """
    cfg = config or load_send_rate_config()

    # A send with no recipients shouldn't reach here, but guard anyway: treat
    # as at least one message so it can never be a zero-cost bypass.
    requested = max(1, recipient_count)
    now_ts = now if now is not None else int(time.time())

    async def _check(scope: str, cap: int, window_seconds: int, counter) -> None:
        """Enforce one ceiling. Fail closed: a count error refuses the send."""
        if cap <= 0:
            return  # this ceiling disabled
        since_ts = now_ts - window_seconds
        try:
            used = await counter(since_ts)
        except Exception as exc:  # fail closed — a count error never opens the gate
            logger.error(
                "[send-rate] %s count query failed for agent=%s — refusing send "
                "(fail-closed): %s", scope, agent_id, exc,
            )
            raise SendRateLimitExceeded(
                agent_id=agent_id, used=-1, requested=requested, cap=cap,
                window_seconds=window_seconds, scope=scope,
            ) from exc
        if used + requested > cap:
            logger.warning(
                "[SECURITY][send-rate] %s cap exceeded agent=%s used=%d "
                "requested=%d cap=%d window=%ss — refusing send",
                scope, agent_id, used, requested, cap, window_seconds,
            )
            raise SendRateLimitExceeded(
                agent_id=agent_id, used=used, requested=requested, cap=cap,
                window_seconds=window_seconds, scope=scope,
            )

    # 1. Per-agent / window — the original runaway-agent ceiling.
    await _check(
        "agent", cfg.max_per_window, cfg.window_seconds,
        lambda since: _count_recent_sends(agent_id, since_ts=since, db_path=db_path),
    )

    # 2 & 3. Account-scoped ceilings — only when the owning user is resolvable
    # (skipped for synthetic agent ids in unit tests).
    if cfg.account_max_per_window > 0 or cfg.account_max_per_day > 0:
        user_id = await _resolve_owner_user_id(agent_id, db_path=db_path)
        if user_id is not None:
            await _check(
                "account", cfg.account_max_per_window, cfg.window_seconds,
                lambda since: _count_recent_sends_for_user(
                    user_id, since_ts=since, db_path=db_path
                ),
            )
            await _check(
                "account-daily", cfg.account_max_per_day, _DAY_SECONDS,
                lambda since: _count_recent_sends_for_user(
                    user_id, since_ts=since, db_path=db_path
                ),
            )
