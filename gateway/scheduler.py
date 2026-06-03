"""
Scheduled (cool-down) execution — Tier 2.

An auto-approval rule may decide ``approve_after`` instead of ``approve``: the
op is left ``pending`` but stamped with ``scheduled_execute_at``. This module
runs the deferred execution once that deadline passes, unless a human approved
('send now') or rejected ('cancel') the op during the window. It is the catch-
it-before-it-sends safety net for outbound mail, which cannot be undone.

This is core, generic machinery — it knows nothing about *why* an op was
scheduled (that lives in the enterprise rules engine). It only executes pending
ops whose deadline has arrived, through the SAME shared executor a human
approval uses.

  ┌─────────────────────────────────────────────────────────┐
  │  find_due_scheduled()                                    │
  │   SELECT * FROM staged_operations                       │
  │   WHERE status='pending'                                │
  │     AND scheduled_execute_at IS NOT NULL                │
  │     AND scheduled_execute_at <= now()                   │
  └──────────────────────┬──────────────────────────────────┘
                         │  (batch, up to 100 per run)
                         ▼
  ┌─────────────────────────────────────────────────────────┐
  │  for each op:                                           │
  │    claim: UPDATE → 'approved' WHERE status='pending'    │
  │           (atomic; rowcount==1 means we won the race     │
  │            against a concurrent manual approve/reject)   │
  │    execute_operation(actor='auto_rule')                 │
  └─────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from gateway.state_db import DB_PATH, get_db

logger = logging.getLogger(__name__)

# Maximum ops to execute in one run (prevents runaway DB lock on large backlogs).
_BATCH_SIZE = 100


async def find_due_scheduled(db_path: Path = DB_PATH) -> list[dict]:
    """Return pending operations whose cool-down deadline has passed."""
    now = int(time.time())
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT *
            FROM staged_operations
            WHERE status = 'pending'
              AND scheduled_execute_at IS NOT NULL
              AND scheduled_execute_at <= ?
            ORDER BY scheduled_execute_at ASC
            LIMIT ?
            """,
            (now, _BATCH_SIZE),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def _claim(op_id: str, db_path: Path) -> bool:
    """Atomically claim a still-pending op for execution.

    Transitions 'pending' -> 'approved' only if it is still pending, so a
    concurrent manual approve/reject (both guarded on status='pending') and this
    loop can never both execute the same op. Returns True if we claimed it.
    """
    now = int(time.time())
    async with get_db(db_path) as db:
        cur = await db.execute(
            """
            UPDATE staged_operations
            SET status = 'approved', decided_at = ?, decided_by = 'auto_rule'
            WHERE id = ? AND status = 'pending'
            """,
            (now, op_id),
        )
        await db.commit()
        return cur.rowcount == 1


async def execute_due_scheduled(db_path: Path = DB_PATH) -> int:
    """Execute all pending operations whose cool-down has elapsed.

    Returns the count actually executed (claimed and run) this pass. Safe to
    call concurrently: each op is claimed atomically before execution.
    """
    due = await find_due_scheduled(db_path=db_path)
    if not due:
        return 0

    logger.info("[scheduler] %d scheduled operation(s) due for execution", len(due))

    # Imported lazily to avoid a circular import (gateway.execution imports
    # gateway.staging, which the scheduler does not, but keep the boundary clean).
    from gateway.execution import ExecutionError, execute_operation  # noqa: PLC0415

    count = 0
    for op in due:
        op_id = op["id"]
        if not await _claim(op_id, db_path):
            # A human approved or rejected it first — nothing to do.
            logger.debug("[scheduler] op=%s no longer pending; skipping", op_id)
            continue
        # Re-read so the executor sees the claimed ('approved') status row.
        row = dict(op)
        row["status"] = "approved"
        try:
            await execute_operation(op_id, row, db_path, actor="auto_rule")
            count += 1
        except ExecutionError as exc:
            # The executor already marked the op 'failed' and wrote an audit row.
            logger.warning("[scheduler] Execution failed for op=%s: %s", op_id, exc)

    return count


async def run_scheduled_execution_loop(
    db_path: Path = DB_PATH,
    interval_seconds: float = 30.0,
    initial_delay_seconds: float = 15.0,
) -> None:
    """Asyncio background task: periodically execute due scheduled operations.

    interval_seconds: how often to check (default: every 30s — cool-downs are
        minutes, so the deadline is honoured to within one interval).
    initial_delay_seconds: delay before the first check after startup.

    Run via asyncio.create_task() in the FastAPI lifespan handler. Exits cleanly
    on asyncio.CancelledError.
    """
    logger.info(
        "[scheduler] Loop starting — interval=%.0fs, initial_delay=%.0fs",
        interval_seconds, initial_delay_seconds,
    )
    try:
        await asyncio.sleep(initial_delay_seconds)
        while True:
            try:
                count = await execute_due_scheduled(db_path=db_path)
                if count > 0:
                    logger.info("[scheduler] Executed %d scheduled operation(s)", count)
            except Exception as exc:  # noqa: BLE001
                logger.error("[scheduler] Unexpected error during run: %s", exc)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("[scheduler] Loop cancelled — shutting down")
        raise
