"""
Body scrubber — Issue #29.

Nulls out ``body_preview`` within the ``smtp_envelope`` JSON column of
``staged_operations`` after an operation reaches a terminal state AND
``NUVRAIL_BODY_SCRUB_DAYS`` days have passed (default: 7).

This limits how long email body content is retained in the local DB for
operations that have already been decided, reducing exposure in the event of
a DB compromise.

Processing flow:

  ┌────────────────────────────────────────────────────────────────────┐
  │  find_scrubable_operations()                                       │
  │    SELECT id, smtp_envelope FROM staged_operations                 │
  │    WHERE status IN (terminal)                                      │
  │      AND smtp_envelope IS NOT NULL                                 │
  │      AND body_scrubbed_at IS NULL                                  │
  │      AND COALESCE(decided_at, created_at) <= now - scrub_cutoff    │
  └───────────────────────────────┬────────────────────────────────────┘
                                  │ (batch up to 200 per run)
                                  ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │  for each op:                                                      │
  │    parse smtp_envelope JSON                                        │
  │    set body_preview = null                                         │
  │    UPDATE staged_operations SET                                    │
  │      smtp_envelope = <scrubbed JSON>,                              │
  │      body_scrubbed_at = now                                        │
  │    INSERT audit_log event='body_scrubbed' actor='system'           │
  └────────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from gateway.audit import insert_audit_event
from gateway.state_db import DB_PATH, get_db

logger = logging.getLogger(__name__)

# Days to retain body_preview after terminal state.
_SCRUB_DAYS: float = float(os.environ.get("NUVRAIL_BODY_SCRUB_DAYS", "7"))

# Terminal statuses eligible for scrubbing.
_TERMINAL_STATUSES = ("approved", "rejected", "executed", "failed", "expired")

# Max rows per run to prevent long DB locks on large backlogs.
_BATCH_SIZE = 200


def _scrub_cutoff() -> int:
    """Unix timestamp before which terminal ops are eligible for scrubbing."""
    return int(time.time() - _SCRUB_DAYS * 86400)


async def find_scrubable_operations(db_path: Path = DB_PATH) -> list[dict]:
    """Return terminal ops with unscrubbed body_preview old enough to scrub."""
    cutoff = _scrub_cutoff()
    placeholders = ",".join("?" * len(_TERMINAL_STATUSES))
    async with get_db(db_path) as db:
        async with db.execute(
            f"""
            SELECT id, smtp_envelope, agent_id, op_type
            FROM staged_operations
            WHERE status IN ({placeholders})
              AND smtp_envelope IS NOT NULL
              AND body_scrubbed_at IS NULL
              AND COALESCE(decided_at, created_at) <= ?
            ORDER BY COALESCE(decided_at, created_at) ASC
            LIMIT ?
            """,  # noqa: S608
            (*_TERMINAL_STATUSES, cutoff, _BATCH_SIZE),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _scrub_one(op: dict, db_path: Path) -> None:
    """Scrub body_preview from one operation's smtp_envelope and log the event."""
    op_id: str = op["id"]
    now = int(time.time())

    # Parse and scrub the envelope.
    try:
        envelope = json.loads(op["smtp_envelope"])
    except (json.JSONDecodeError, TypeError):
        # Unparseable envelope — null it entirely so it can't leak anything.
        scrubbed_envelope = None
        logger.warning("[scrubber] op=%s: smtp_envelope unparseable, nulling entirely", op_id)
    else:
        envelope["body_preview"] = None
        envelope["body"] = None        # full RFC 2822 body added after issue was filed
        scrubbed_envelope = json.dumps(envelope)

    async with get_db(db_path) as db:
        await db.execute(
            """
            UPDATE staged_operations
            SET smtp_envelope = ?, body_scrubbed_at = ?
            WHERE id = ?
            """,
            (scrubbed_envelope, now, op_id),
        )
        await insert_audit_event(
            db, timestamp=now, event='body_scrubbed', actor='system',
            operation_id=op_id, agent_id=op.get('agent_id'), op_type=op.get('op_type'),
        )
        await db.commit()

    logger.info("[scrubber] op=%s body_preview scrubbed", op_id)


async def scrub_expired_body_previews(db_path: Path = DB_PATH) -> int:
    """Scrub body_preview from all eligible terminal operations.

    Returns the count of rows scrubbed in this run.
    Safe to call concurrently — UPDATE is conditional on body_scrubbed_at IS NULL.
    """
    ops = await find_scrubable_operations(db_path=db_path)
    if not ops:
        return 0

    logger.info("[scrubber] Found %d operation(s) with body_preview to scrub", len(ops))
    count = 0
    for op in ops:
        try:
            await _scrub_one(op, db_path)
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("[scrubber] Failed to scrub op=%s: %s", op["id"], exc)

    return count


async def run_scrubber_loop(
    db_path: Path = DB_PATH,
    interval_seconds: float = 3600.0,
    initial_delay_seconds: float = 90.0,
) -> None:
    """Asyncio background task: periodically scrub stale body previews.

    interval_seconds: how often to check (default: every hour)
    initial_delay_seconds: delay before first check after startup (default: 90s)

    Run via asyncio.create_task() in the FastAPI lifespan handler.
    Exits cleanly on asyncio.CancelledError.
    """
    logger.info(
        "[scrubber] Loop starting — interval=%.0fs, initial_delay=%.0fs, scrub_days=%.1f",
        interval_seconds, initial_delay_seconds, _SCRUB_DAYS,
    )
    try:
        await asyncio.sleep(initial_delay_seconds)
        while True:
            try:
                count = await scrub_expired_body_previews(db_path=db_path)
                if count > 0:
                    logger.info("[scrubber] Scrubbed %d operation(s) this run", count)
                else:
                    logger.debug("[scrubber] No eligible operations to scrub")
            except Exception as exc:  # noqa: BLE001
                logger.error("[scrubber] Unexpected error during scrub run: %s", exc)

            await asyncio.sleep(interval_seconds)

    except asyncio.CancelledError:
        logger.info("[scrubber] Loop cancelled — shutting down")
        raise
