"""
Cryptographically chained audit log insertion (issue: hash chaining).

Every audit_log row carries two new columns:
  prev_hash   — entry_hash of the immediately preceding row
  entry_hash  — SHA-256(prev_hash‖id‖timestamp‖operation_id‖event‖actor‖
                         agent_id‖op_type‖detail‖user_id)

Fields are serialised as UTF-8 strings joined by ASCII unit separator \\x1f.
None values → empty string. Integer values → decimal string.

The genesis row (first ever inserted) uses prev_hash = "0" * 64.

An asyncio.Lock serialises all insertions so two concurrent coroutines
can never read the same prev_hash.

``intent_label`` (added later) is stored on the row but deliberately NOT part
of the entry_hash: the canonical hash payload is fixed — extending it would
invalidate the hash of every previously chained row. The field is derived,
informational metadata for filtering; its authoritative copy lives on
staged_operations (reachable via operation_id), which is what the chain
already covers through op_type/detail.

Chain integrity:
  ┌──────────┐    prev_hash        ┌──────────┐    prev_hash        ┌──────────┐
  │  row 1   │ ─────────────────▶  │  row 2   │ ─────────────────▶  │  row 3   │
  │ prev="0…"│                     │ prev=H1  │                     │ prev=H2  │
  │ hash=H1  │                     │ hash=H2  │                     │ hash=H3  │
  └──────────┘                     └──────────┘                     └──────────┘
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import aiosqlite

from gateway.loop_health import record_heartbeat

logger = logging.getLogger(__name__)

# Sentinel prev_hash for the very first row in the chain.
GENESIS_HASH: str = "0" * 64

# Module-level singleton — serialises all audit insertions across callers.
_AUDIT_LOCK: asyncio.Lock = asyncio.Lock()

# ASCII unit separator used to join fields for hashing.
_SEP = "\x1f"


def _compute_entry_hash(
    prev_hash: str,
    row_id: int,
    timestamp: int,
    operation_id: Optional[str],
    event: str,
    actor: Optional[str],
    agent_id: Optional[str],
    op_type: Optional[str],
    detail: Optional[str],
    user_id: Optional[int],
) -> str:
    """Return SHA-256 hex digest of the canonicalised row fields.

    Field order (joined by \\x1f, None → ""):
      prev_hash, id, timestamp, operation_id, event, actor,
      agent_id, op_type, detail, user_id
    """
    def _s(v: object) -> str:
        if v is None:
            return ""
        return str(v)

    payload = _SEP.join([
        _s(prev_hash),
        _s(row_id),
        _s(timestamp),
        _s(operation_id),
        _s(event),
        _s(actor),
        _s(agent_id),
        _s(op_type),
        _s(detail),
        _s(user_id),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def insert_audit_event(
    db: aiosqlite.Connection,
    *,
    timestamp: int,
    event: str,
    actor: Optional[str] = None,
    operation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    op_type: Optional[str] = None,
    detail: Optional[str] = None,
    user_id: Optional[int] = None,
    intent_label: Optional[str] = None,
) -> None:
    """Insert a single audit_log row with cryptographic hash chaining.

    Acquires _AUDIT_LOCK to ensure sequential prev_hash reads.  Does NOT
    call db.commit() — the caller commits as part of its own transaction.

    Args:
        db:           An open aiosqlite.Connection (row_factory=aiosqlite.Row).
        timestamp:    Unix timestamp for the event.
        event:        Event type string (e.g. 'staged', 'approved').
        actor:        Who triggered the event ('ai_agent'|'human'|'system'|'auto_rule').
        operation_id: FK to staged_operations.id (or None).
        agent_id:     Agent identifier string (or None).
        op_type:      Operation type string (or None).
        detail:       JSON string with extra context (or None).
        user_id:      FK to users.id (or None).
        intent_label: Denormalized semantic intent (gateway.intent) — stored
                      for filtering but NOT included in entry_hash (see module
                      docstring).
    """
    async with _AUDIT_LOCK:
        # Read the entry_hash of the most recently inserted hashed row.
        async with db.execute(
            """
            SELECT entry_hash FROM audit_log
            WHERE entry_hash IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ) as cur:
            last_row = await cur.fetchone()

        prev_hash: str = last_row["entry_hash"] if last_row else GENESIS_HASH

        # Insert the row first to obtain its auto-assigned id.
        cursor = await db.execute(
            """
            INSERT INTO audit_log
                (timestamp, operation_id, event, actor, agent_id, op_type, detail,
                 user_id, intent_label, prev_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (timestamp, operation_id, event, actor, agent_id, op_type, detail,
             user_id, intent_label, prev_hash),
        )
        row_id: int = cursor.lastrowid  # type: ignore[assignment]

        # Now compute the entry_hash with the real id baked in.
        entry_hash = _compute_entry_hash(
            prev_hash=prev_hash,
            row_id=row_id,
            timestamp=timestamp,
            operation_id=operation_id,
            event=event,
            actor=actor,
            agent_id=agent_id,
            op_type=op_type,
            detail=detail,
            user_id=user_id,
        )

        await db.execute(
            "UPDATE audit_log SET entry_hash = ? WHERE id = ?",
            (entry_hash, row_id),
        )
        # Caller commits.


async def record_audit_event(
    db_path: Path,
    *,
    timestamp: int,
    event: str,
    actor: Optional[str] = None,
    operation_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    op_type: Optional[str] = None,
    detail: Optional[str] = None,
    user_id: Optional[int] = None,
    intent_label: Optional[str] = None,
) -> None:
    """Open a connection, append one audit_log row, and commit.

    Convenience wrapper around insert_audit_event for the common case where the
    audit write stands alone (the only write in its transaction). When the
    audit must commit atomically with other writes — e.g. inserting the staged
    operation, or marking an account deleted — use insert_audit_event(db, …)
    inside that existing transaction instead.
    """
    from gateway.state_db import get_db  # noqa: PLC0415 — avoid import cycle

    async with get_db(db_path) as db:
        await insert_audit_event(
            db,
            timestamp=timestamp,
            event=event,
            actor=actor,
            operation_id=operation_id,
            agent_id=agent_id,
            op_type=op_type,
            detail=detail,
            user_id=user_id,
            intent_label=intent_label,
        )
        await db.commit()


async def verify_audit_chain(db_path: Path) -> tuple[bool, list[str]]:
    """Walk all hashed audit_log rows and verify the chain integrity.

    Skips rows where entry_hash IS NULL (pre-migration rows inserted before
    hash chaining was introduced).  Starts the chain from the first non-NULL
    row (its prev_hash is taken as-is — we accept whatever prev_hash was
    recorded; we only verify that the entry_hash is correct given that
    prev_hash and the row's own fields).

    Returns:
        (True, [])               — chain is intact
        (False, [error, ...])    — one or more rows are tampered or broken
    """
    errors: list[str] = []

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, timestamp, operation_id, event, actor, agent_id,
                   op_type, detail, user_id, prev_hash, entry_hash
            FROM audit_log
            WHERE entry_hash IS NOT NULL
            ORDER BY id ASC
            """
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return True, []

    expected_prev = rows[0]["prev_hash"]

    for row in rows:
        rid = row["id"]

        # Verify that prev_hash matches what we expect in the chain.
        if row["prev_hash"] != expected_prev:
            errors.append(
                f"row {rid}: prev_hash mismatch "
                f"(expected {expected_prev!r}, got {row['prev_hash']!r})"
            )
            # Continue checking the remaining rows using the stored prev_hash
            # so we report all broken links, not just the first.
            expected_prev = row["entry_hash"]
            continue

        # Recompute entry_hash and compare.
        computed = _compute_entry_hash(
            prev_hash=row["prev_hash"],
            row_id=rid,
            timestamp=row["timestamp"],
            operation_id=row["operation_id"],
            event=row["event"],
            actor=row["actor"],
            agent_id=row["agent_id"],
            op_type=row["op_type"],
            detail=row["detail"],
            user_id=row["user_id"],
        )
        if computed != row["entry_hash"]:
            errors.append(
                f"row {rid}: entry_hash mismatch "
                f"(stored {row['entry_hash']!r}, computed {computed!r})"
            )

        expected_prev = row["entry_hash"]

    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# Tamper-evidence surfacing — the hash chain is only useful if something
# actually verifies it. These expose on-demand + scheduled verification and the
# current chain head (so a client can record it and later prove the log up to
# that point has not been rewritten).
# ---------------------------------------------------------------------------

# Result of the most recent background verification pass, for observability /
# health probes. ``checked_at`` is None until the loop has run once.
_last_verification: dict = {"checked_at": None, "ok": None, "broken_count": 0}


def last_verification_result() -> dict:
    """Return a copy of the most recent background verification result."""
    return dict(_last_verification)


async def get_chain_head(db_path: Path) -> Optional[str]:
    """Return the entry_hash of the most recent hashed audit row (the chain
    head), or None if the chain is empty.

    A client that records this value can later detect any rewrite of the log up
    to this point: re-verifying the chain and re-deriving the head must still
    yield the same hash.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT entry_hash FROM audit_log
            WHERE entry_hash IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ) as cur:
            row = await cur.fetchone()
    return row["entry_hash"] if row else None


async def run_audit_verification_loop(
    db_path: Path,
    interval_seconds: float = 3600.0,
    initial_delay_seconds: float = 45.0,
) -> None:
    """Asyncio background task: periodically verify audit-log chain integrity.

    The hash chain makes tampering *detectable*, but only if something runs the
    check. This loop is that something: on each pass it walks the chain and, on
    any break, logs at ERROR (the signal an alerting pipeline should page on).
    The latest result is also stored for ``last_verification_result()`` / the
    /audit/verify endpoint and health surfacing.

    Run via asyncio.create_task() in the FastAPI lifespan handler. Exits cleanly
    on asyncio.CancelledError.
    """
    logger.info(
        "[audit-verify] Loop starting — interval=%.0fs, initial_delay=%.0fs",
        interval_seconds, initial_delay_seconds,
    )
    try:
        await asyncio.sleep(initial_delay_seconds)
        while True:
            try:
                ok, errors = await verify_audit_chain(db_path)
                _last_verification.update(
                    checked_at=int(time.time()), ok=ok, broken_count=len(errors)
                )
                # The loop completed a run — record a heartbeat. (A broken chain
                # is a separate signal, surfaced via the ERROR log + the
                # /audit/verify endpoint, not a loop-health failure.)
                record_heartbeat("audit-verify", interval_seconds=interval_seconds)
                if not ok:
                    # SECURITY-critical: the append-only guarantee has been
                    # violated. Surface loudly; an alert should fire on this line.
                    logger.error(
                        "[audit-verify] AUDIT LOG INTEGRITY FAILURE — %d broken "
                        "row(s): %s",
                        len(errors), "; ".join(errors[:10]),
                    )
                else:
                    logger.debug("[audit-verify] chain intact")
            except Exception as exc:  # noqa: BLE001 — a sweep error must not kill the loop
                logger.error("[audit-verify] verification run failed: %s", exc)
                record_heartbeat(
                    "audit-verify", interval_seconds=interval_seconds, ok=False,
                    detail=str(exc),
                )
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("[audit-verify] Loop cancelled — shutting down")
        raise
