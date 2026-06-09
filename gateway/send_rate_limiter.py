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

Configuration (env)
--------------------
* ``NUVRAIL_SEND_MAX_PER_WINDOW``  — cap of outbound messages per window per
  agent. Default 100. ``0`` disables the limiter (escape hatch).
* ``NUVRAIL_SEND_WINDOW_SECONDS``  — rolling window length. Default 3600 (1h).
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
    ) -> None:
        self.agent_id = agent_id
        self.used = used
        self.requested = requested
        self.cap = cap
        self.window_seconds = window_seconds
        super().__init__(
            f"Outbound send rate limit exceeded for agent={agent_id!r}: "
            f"{used} already sent + {requested} requested > cap {cap} "
            f"per {window_seconds}s"
        )


@dataclass(frozen=True)
class SendRateConfig:
    """Resolved limiter configuration. ``max_per_window == 0`` ⇒ disabled."""

    max_per_window: int
    window_seconds: int

    @property
    def enabled(self) -> bool:
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
    async with get_db(db_path) as db:
        async with db.execute(sql, (agent_id, since_ts)) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


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
    if not cfg.enabled:
        return

    # A send with no recipients shouldn't reach here, but guard anyway: treat
    # as at least one message so it can never be a zero-cost bypass.
    requested = max(1, recipient_count)
    now_ts = now if now is not None else int(time.time())
    since_ts = now_ts - cfg.window_seconds

    try:
        used = await _count_recent_sends(agent_id, since_ts=since_ts, db_path=db_path)
    except Exception as exc:  # fail closed — never let a count error open the gate
        logger.error(
            "[send-rate] count query failed for agent=%s — refusing send "
            "(fail-closed): %s",
            agent_id, exc,
        )
        raise SendRateLimitExceeded(
            agent_id=agent_id,
            used=-1,
            requested=requested,
            cap=cfg.max_per_window,
            window_seconds=cfg.window_seconds,
        ) from exc

    if used + requested > cfg.max_per_window:
        logger.warning(
            "[SECURITY][send-rate] cap exceeded agent=%s used=%d requested=%d "
            "cap=%d window=%ss — refusing send",
            agent_id, used, requested, cfg.max_per_window, cfg.window_seconds,
        )
        raise SendRateLimitExceeded(
            agent_id=agent_id,
            used=used,
            requested=requested,
            cap=cfg.max_per_window,
            window_seconds=cfg.window_seconds,
        )
