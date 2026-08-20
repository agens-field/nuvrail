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
import json
import logging
import os
import time
from pathlib import Path

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
    operation_id: str | None,
    event: str,
    actor: str | None,
    agent_id: str | None,
    op_type: str | None,
    detail: str | None,
    user_id: int | None,
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
    actor: str | None = None,
    operation_id: str | None = None,
    agent_id: str | None = None,
    op_type: str | None = None,
    detail: str | None = None,
    user_id: int | None = None,
    intent_label: str | None = None,
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
    actor: str | None = None,
    operation_id: str | None = None,
    agent_id: str | None = None,
    op_type: str | None = None,
    detail: str | None = None,
    user_id: int | None = None,
    intent_label: str | None = None,
) -> None:
    """Open a connection, append one audit_log row, and commit.

    Convenience wrapper around insert_audit_event for the common case where the
    audit write stands alone (the only write in its transaction). When the
    audit must commit atomically with other writes — e.g. inserting the staged
    operation, or marking an account deleted — use insert_audit_event(db, …)
    inside that existing transaction instead.
    """
    from gateway.state_db import get_db

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


async def get_chain_head(db_path: Path) -> str | None:
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


# ---------------------------------------------------------------------------
# External chain-head anchoring — tail-truncation detection.
#
# The hash chain makes forgery and MIDDLE-row deletion detectable, because
# verify_audit_chain walks prev_hash -> entry_hash links and any altered or
# removed interior row breaks a link. But it CANNOT detect TAIL truncation:
# deleting the most-recent N rows leaves a shorter chain that is still
# internally consistent (verify_audit_chain trusts rows[0].prev_hash as its
# start and never learns that later rows once existed). An attacker with DB
# write access can therefore silently drop the tail of the log.
#
# The fix is to record the chain head OUTSIDE the DB, to an append-only sink on
# separate storage. Because each anchored head is the entry_hash of a specific
# row, and every row's entry_hash is bound to its own id + fields, a previously
# anchored head can only reappear in the live chain if that exact row (and, by
# the prev_hash links, every row before it) is still present. So:
#
#   last anchored head still reachable in live chain  -> tail intact
#   last anchored head NOT found in live chain        -> TAIL TRUNCATION
#
#   anchor sink (append-only, separate storage)          live DB chain
#   ┌────────────────────────────────────────┐          ┌──────┬──────┬──────┐
#   │ {ts, head=H2, row_count=2}              │   H2 ∈?  │ r1   │ r2   │ r3   │
#   │ {ts, head=H3, row_count=3}   ◀── last   │  ──────▶ │ H1   │ H2   │ H3   │
#   └────────────────────────────────────────┘   H3 ∈?  └──────┴──────┴──────┘
#                                                          truncate r3 -> H3 gone
#                                                          -> divergence flagged
#
# Config-driven and OFF by default: self-hosters are not forced into an
# external dependency. Enable by setting NUVRAIL_AUDIT_ANCHOR_PATH to a file on
# append-only / WORM storage.
# ---------------------------------------------------------------------------

# Environment key naming the append-only anchor sink. Unset/empty -> anchoring
# disabled (the default), so no external storage dependency is imposed.
_ANCHOR_PATH_ENV = "NUVRAIL_AUDIT_ANCHOR_PATH"


def anchor_sink_path() -> Path | None:
    """Return the configured external anchor sink path, or None if disabled.

    Read at call time (not import time) so tests and operators can toggle it
    via the environment without reimporting the module.
    """
    raw = os.environ.get(_ANCHOR_PATH_ENV, "").strip()
    return Path(raw) if raw else None


def _append_anchor_line(path: Path, record: dict) -> None:
    """Append one JSONL anchor record to the sink (blocking; run off-loop).

    Split out from ``anchor_chain_head`` so the async caller can offload this
    blocking disk write via ``asyncio.to_thread`` (ASYNC230). Append-only:
    O_APPEND of a single line — never truncates or rewrites the file, so a
    WORM/append-only target keeps the full anchor history.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


async def anchor_chain_head(
    db_path: Path,
    anchor_path: Path | None = None,
) -> dict | None:
    """Append the current chain head to the external append-only anchor sink.

    Writes one JSON object per line (JSONL): ``{"anchored_at", "chain_head",
    "row_count"}``. Append-only by construction — we only ever O_APPEND a line,
    never rewrite the file — so a WORM/append-only-mounted target keeps the
    full anchor history even against an attacker who can write the DB.

    ``row_count`` is stored for human diagnostics only; tail-truncation
    detection relies on the cryptographic head, not the count.

    Args:
        db_path:     Path to the audit DB.
        anchor_path: Override for the sink path; defaults to ``anchor_sink_path()``.

    Returns:
        The anchored record dict, or None if anchoring is disabled (no path) or
        the chain is empty (nothing to anchor).
    """
    path = anchor_path if anchor_path is not None else anchor_sink_path()
    if path is None:
        return None

    head = await get_chain_head(db_path)
    if head is None:
        # Empty chain — nothing to anchor yet.
        return None

    async with aiosqlite.connect(db_path) as db, db.execute(
        "SELECT COUNT(*) AS n FROM audit_log WHERE entry_hash IS NOT NULL"
    ) as cur:
        row_count = (await cur.fetchone())[0]

    record = {
        "anchored_at": int(time.time()),
        "chain_head": head,
        "row_count": row_count,
    }

    # Blocking file I/O is offloaded to a thread so the event loop is never
    # stalled on disk (ASYNC230): the sink may live on slow/remote WORM storage.
    await asyncio.to_thread(_append_anchor_line, path, record)

    return record


def read_last_anchor(anchor_path: Path | None = None) -> dict | None:
    """Return the most recent anchor record from the sink, or None.

    None means anchoring is disabled, the sink does not exist yet, or it holds
    no valid records. Malformed trailing lines (e.g. a partial write) are
    skipped so a torn last line cannot mask a valid earlier anchor.
    """
    path = anchor_path if anchor_path is not None else anchor_sink_path()
    if path is None or not path.exists():
        return None

    last: dict | None = None
    with open(path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("chain_head"), str):
                last = obj
    return last


async def verify_against_anchor(
    db_path: Path,
    anchor_path: Path | None = None,
) -> tuple[bool, str | None]:
    """Detect tail truncation by comparing the live chain against the last anchor.

    The last anchored head is the entry_hash of a row that existed when we
    anchored. If the live chain still contains a hashed row whose entry_hash
    equals that head, the tail up to that point is intact. If it does NOT, the
    most-recent rows have been truncated (or otherwise rewritten so that head
    no longer exists) — the exact gap the in-DB chain walk cannot catch.

    Returns:
        (True,  None)      — anchoring disabled/no anchor yet, or tail intact.
        (False, reason)    — divergence detected; reason names the missing head.

    This intentionally does NOT re-verify interior links — that is
    verify_audit_chain's job. Run both for full coverage.
    """
    last = read_last_anchor(anchor_path)
    if last is None:
        # No anchor to compare against — not a failure, just nothing to prove.
        return True, None

    anchored_head = last["chain_head"]

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT 1 FROM audit_log WHERE entry_hash = ? LIMIT 1",
            (anchored_head,),
        ) as cur:
            present = await cur.fetchone()

    if present is None:
        return False, (
            f"tail truncation: last anchored chain head {anchored_head!r} "
            f"(anchored_at={last.get('anchored_at')}, "
            f"row_count={last.get('row_count')}) is absent from the live chain"
        )
    return True, None


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
                # Tail-truncation check against the external anchor (no-op when
                # anchoring is disabled). This catches deletions the in-DB
                # chain walk structurally cannot — see verify_against_anchor.
                anchor_ok, anchor_reason = await verify_against_anchor(db_path)
                _last_verification.update(
                    checked_at=int(time.time()),
                    ok=(ok and anchor_ok),
                    broken_count=len(errors) + (0 if anchor_ok else 1),
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
                if not anchor_ok:
                    # SECURITY-critical: tail truncation detected against the
                    # external anchor. Alert on this line too.
                    logger.error(
                        "[audit-verify] AUDIT LOG TAIL-TRUNCATION DETECTED — %s",
                        anchor_reason,
                    )
                if ok and anchor_ok:
                    logger.debug("[audit-verify] chain intact")
                    # Only re-anchor a verified-intact head, so we never anchor
                    # a head derived from an already-broken chain. No-op when
                    # anchoring is disabled (anchor_chain_head returns None).
                    try:
                        anchored = await anchor_chain_head(db_path)
                        if anchored is not None:
                            logger.debug(
                                "[audit-verify] anchored chain head %s (rows=%d)",
                                anchored["chain_head"][:12], anchored["row_count"],
                            )
                    except Exception as exc:  # anchoring must never crash the loop
                        logger.error(
                            "[audit-verify] anchor write failed: %s", exc
                        )
            except Exception as exc:
                logger.error("[audit-verify] verification run failed: %s", exc)
                record_heartbeat(
                    "audit-verify", interval_seconds=interval_seconds, ok=False,
                    detail=str(exc),
                )
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("[audit-verify] Loop cancelled — shutting down")
        raise
