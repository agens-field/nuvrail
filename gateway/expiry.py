"""
Operation expiry — Milestone Tier 1.

Pending operations that have not been approved or rejected within 48 hours
(configurable via NUVRAIL_EXPIRY_HOURS) are automatically:
  1. Status set to 'expired'  (distinct from 'rejected' for auditability)
  2. Local state reverted     (snapshot restored, pending_reverts queued)
  3. Audit log entry written  (event='expired', actor='system')

This module provides:
  expire_stale_operations(db_path) — run once, expire all overdue pending ops
  run_expiry_loop(db_path, interval_seconds) — asyncio task for continuous use

Processing flow per expired operation:

  ┌─────────────────────────────────────────────────────────┐
  │  find_expired_pending()                                 │
  │   SELECT * FROM staged_operations                       │
  │   WHERE status='pending' AND expires_at < now()         │
  └──────────────────────┬──────────────────────────────────┘
                         │  (batch, up to 100 per run)
                         ▼
  ┌─────────────────────────────────────────────────────────┐
  │  for each op:                                           │
  │    restore_from_snapshot(op_id)  → reverts messages     │
  │    insert_pending_reverts(...)   → queues FETCH inject  │
  │    UPDATE status → 'expired'                            │
  │    INSERT audit_log event='expired' actor='system'      │
  └─────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from gateway.audit import insert_audit_event
from gateway.state_db import DB_PATH, get_db, insert_pending_reverts, restore_from_snapshot

logger = logging.getLogger(__name__)

# How many hours before a pending op is considered expired.
# Overridable via environment for testing (e.g. set to 0.001 for ~4 seconds).
_EXPIRY_HOURS: float = float(os.environ.get("NUVRAIL_EXPIRY_HOURS", "48"))

# Maximum ops to expire in one run (prevents runaway DB lock on large backlogs)
_BATCH_SIZE = 100


async def find_expired_pending(db_path: Path = DB_PATH) -> list[dict]:
    """Return pending operations whose expires_at has passed."""
    now = int(time.time())
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT id, op_type, protocol, description, expires_at, snapshot
            FROM staged_operations
            WHERE status = 'pending' AND expires_at <= ?
            ORDER BY expires_at ASC
            LIMIT ?
            """,
            (now, _BATCH_SIZE),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _expire_one(op_id: str, db_path: Path) -> None:
    """Expire a single pending operation: revert state, update status, log."""
    now = int(time.time())

    # 1. Restore local state from snapshot (same path as rejection revert)
    try:
        reverts = await restore_from_snapshot(op_id, db_path=db_path)
        if reverts:
            await insert_pending_reverts(op_id, reverts, db_path=db_path)
            logger.info(
                "[expiry] op=%s: restored snapshot, queued %d pending_reverts",
                op_id, len(reverts),
            )
    except Exception as exc:  # noqa: BLE001
        # Revert failure is non-fatal — we still mark the op expired
        logger.warning("[expiry] op=%s: snapshot revert failed (non-fatal): %s", op_id, exc)

    # 2. Update status and write audit entry in one transaction
    async with get_db(db_path) as db:
        await db.execute(
            """
            UPDATE staged_operations
            SET status = 'expired', decided_at = ?, decided_by = 'system'
            WHERE id = ? AND status = 'pending'
            """,
            (now, op_id),
        )
        # Fetch agent_id, op_type, intent for the audit row (denormalized for fast queries).
        async with db.execute(
            "SELECT agent_id, op_type, intent_label FROM staged_operations WHERE id = ?",
            (op_id,),
        ) as cur:
            op_row = await cur.fetchone()
        _agent_id = op_row["agent_id"] if op_row else None
        _op_type = op_row["op_type"] if op_row else None
        _intent = op_row["intent_label"] if op_row else None
        await insert_audit_event(
            db, timestamp=now, event='expired', actor='system',
            operation_id=op_id, agent_id=_agent_id, op_type=_op_type,
            intent_label=_intent,
        )
        await db.commit()

    logger.info("[expiry] op=%s marked expired", op_id)


async def expire_stale_operations(db_path: Path = DB_PATH) -> int:
    """
    Find and expire all pending operations past their expires_at timestamp.

    Returns the count of operations expired in this run.
    Safe to call concurrently — the UPDATE WHERE status='pending' is atomic.
    """
    expired_ops = await find_expired_pending(db_path=db_path)
    if not expired_ops:
        return 0

    logger.info("[expiry] Found %d overdue pending operation(s) to expire", len(expired_ops))

    count = 0
    for op in expired_ops:
        try:
            await _expire_one(op["id"], db_path)
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("[expiry] Failed to expire op=%s: %s", op["id"], exc)

    return count


async def run_expiry_loop(
    db_path: Path = DB_PATH,
    interval_seconds: float = 3600.0,
    initial_delay_seconds: float = 60.0,
) -> None:
    """
    Asyncio background task: periodically expire stale pending operations.

    interval_seconds: how often to check (default: every hour)
    initial_delay_seconds: delay before first check after startup (default: 60s)

    Run this as an asyncio background task via asyncio.create_task() in the
    FastAPI lifespan handler.

    Exits cleanly on asyncio.CancelledError.
    """
    logger.info(
        "[expiry] Loop starting — interval=%.0fs, initial_delay=%.0fs",
        interval_seconds, initial_delay_seconds,
    )
    try:
        # Short wait at startup so the DB is fully initialised before first run
        await asyncio.sleep(initial_delay_seconds)

        while True:
            try:
                count = await expire_stale_operations(db_path=db_path)
                if count > 0:
                    logger.info("[expiry] Expired %d operation(s) this run", count)
                else:
                    logger.debug("[expiry] No overdue operations found")
            except Exception as exc:  # noqa: BLE001
                logger.error("[expiry] Unexpected error during expiry run: %s", exc)

            await asyncio.sleep(interval_seconds)

    except asyncio.CancelledError:
        logger.info("[expiry] Loop cancelled — shutting down")
        raise
