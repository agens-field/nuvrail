"""
Tests for cryptographic hash chaining on audit_log (gateway/audit.py).

Coverage:
  - _compute_entry_hash is deterministic
  - _compute_entry_hash changes when any field changes
  - Genesis hash used for first inserted row
  - Row N's prev_hash equals row N-1's entry_hash
  - verify_audit_chain passes on a clean database
  - verify_audit_chain detects field tampering
  - verify_audit_chain detects prev_hash break
  - Rows with None optional fields still chain correctly
  - Pre-migration rows (entry_hash IS NULL) are skipped by verify_audit_chain

Test DB layout:
  Each test creates an isolated in-memory (or tmp file) SQLite DB via
  gateway.state_db.init_db so schema migrations run, then exercises the
  gateway.audit module directly.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from gateway.audit import (
    GENESIS_HASH,
    _compute_entry_hash,
    get_chain_head,
    insert_audit_event,
    last_verification_result,
    record_audit_event,
    run_audit_verification_loop,
    verify_audit_chain,
)
from gateway.state_db import get_db, init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """Return path to an initialised temporary SQLite DB."""
    path = tmp_path / "test_audit.db"
    await init_db(path)
    return path


@pytest_asyncio.fixture
async def db_conn(db_path: Path):
    """Open a bare aiosqlite connection with row_factory for direct use."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ---------------------------------------------------------------------------
# _compute_entry_hash
# ---------------------------------------------------------------------------


def _base_fields() -> dict:
    return {
        "prev_hash": GENESIS_HASH,
        "row_id": 1,
        "timestamp": 1_700_000_000,
        "operation_id": "op_abc123",
        "event": "staged",
        "actor": "ai_agent",
        "agent_id": "agent_42",
        "op_type": "move",
        "detail": '{"foo": "bar"}',
        "user_id": 7,
    }


def test_compute_entry_hash_is_deterministic():
    """Same inputs always produce the same hash."""
    f = _base_fields()
    h1 = _compute_entry_hash(**f)
    h2 = _compute_entry_hash(**f)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_compute_entry_hash_changes_on_any_field():
    """Changing any single field produces a different hash."""
    f = _base_fields()
    base_hash = _compute_entry_hash(**f)

    mutations = [
        ("prev_hash",    "a" * 64),
        ("row_id",       99),
        ("timestamp",    9_999_999_999),
        ("operation_id", "op_zzz"),
        ("event",        "expired"),
        ("actor",        "human"),
        ("agent_id",     "agent_99"),
        ("op_type",      "store"),
        ("detail",       '{"other": true}'),
        ("user_id",      999),
    ]

    for field, new_val in mutations:
        changed = {**f, field: new_val}
        h = _compute_entry_hash(**changed)
        assert h != base_hash, f"Hash did not change when {field!r} was mutated"


# ---------------------------------------------------------------------------
# insert_audit_event — genesis & chain linking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_genesis_hash_used_for_first_row(db_conn, db_path):
    """The very first inserted row must have prev_hash == GENESIS_HASH."""
    await insert_audit_event(
        db_conn, timestamp=1000, event="staged", actor="ai_agent",
    )
    await db_conn.commit()

    async with db_conn.execute(
        "SELECT prev_hash FROM audit_log ORDER BY id ASC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()

    assert row is not None
    assert row["prev_hash"] == GENESIS_HASH


@pytest.mark.asyncio
async def test_chain_links_prev_to_entry_hash(db_conn, db_path):
    """Row N's prev_hash must equal row N-1's entry_hash."""
    for i in range(5):
        await insert_audit_event(
            db_conn, timestamp=1000 + i, event="staged", actor="ai_agent",
        )
        await db_conn.commit()

    async with db_conn.execute(
        "SELECT id, prev_hash, entry_hash FROM audit_log ORDER BY id ASC"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 5

    # First row: prev_hash is GENESIS_HASH
    assert rows[0]["prev_hash"] == GENESIS_HASH

    # Each subsequent row: prev_hash == previous entry_hash
    for i in range(1, len(rows)):
        assert rows[i]["prev_hash"] == rows[i - 1]["entry_hash"], (
            f"Row {rows[i]['id']} prev_hash != row {rows[i-1]['id']} entry_hash"
        )


# ---------------------------------------------------------------------------
# verify_audit_chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_chain_passes_on_clean_db(db_conn, db_path):
    """Insert 5 events; chain verification should return (True, [])."""
    for i in range(5):
        await insert_audit_event(
            db_conn, timestamp=2000 + i, event="staged", actor="ai_agent",
            operation_id=f"op_{i:06d}",
        )
        await db_conn.commit()

    ok, errors = await verify_audit_chain(db_path)
    assert ok is True
    assert errors == []


@pytest.mark.asyncio
async def test_verify_chain_detects_tamper(db_conn, db_path):
    """Directly modifying a row's field must cause verify_audit_chain to fail."""
    for i in range(3):
        await insert_audit_event(
            db_conn, timestamp=3000 + i, event="staged", actor="ai_agent",
        )
        await db_conn.commit()

    # Tamper with row 2's detail field directly.
    await db_conn.execute(
        "UPDATE audit_log SET detail = '{\"tampered\": true}' WHERE id = 2"
    )
    await db_conn.commit()

    ok, errors = await verify_audit_chain(db_path)
    assert ok is False
    assert any("2" in e for e in errors), f"Expected error mentioning row 2; got: {errors}"


# ---------------------------------------------------------------------------
# get_chain_head — current chain head for client-side anchoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chain_head_none_on_empty_db(db_path):
    """An empty audit log has no chain head."""
    assert await get_chain_head(db_path) is None


@pytest.mark.asyncio
async def test_get_chain_head_tracks_latest_entry_hash(db_conn, db_path):
    """The head equals the entry_hash of the most recently inserted hashed row."""
    for i in range(3):
        await insert_audit_event(
            db_conn, timestamp=4000 + i, event="staged", actor="ai_agent",
        )
        await db_conn.commit()

    async with db_conn.execute(
        "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ) as cur:
        latest = (await cur.fetchone())["entry_hash"]

    assert await get_chain_head(db_path) == latest


# ---------------------------------------------------------------------------
# run_audit_verification_loop — scheduled tamper-evidence sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verification_loop_records_result_and_alerts_on_tamper(
    db_conn, db_path, caplog
):
    """One sweep records the result; a broken chain logs at ERROR (the alert)."""
    import logging

    for i in range(3):
        await insert_audit_event(
            db_conn, timestamp=5000 + i, event="staged", actor="ai_agent",
        )
        await db_conn.commit()
    await db_conn.execute(
        "UPDATE audit_log SET detail = '{\"tampered\": true}' WHERE id = 2"
    )
    await db_conn.commit()

    # Run the loop just long enough for one immediate sweep, then cancel it.
    with caplog.at_level(logging.ERROR, logger="gateway.audit"):
        task = asyncio.create_task(
            run_audit_verification_loop(
                db_path, interval_seconds=3600, initial_delay_seconds=0
            )
        )
        # Wait (with real time slices) until the first sweep records a result.
        for _ in range(100):
            await asyncio.sleep(0.02)
            if last_verification_result()["checked_at"] is not None:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    result = last_verification_result()
    assert result["ok"] is False
    assert result["broken_count"] >= 1
    assert any("INTEGRITY FAILURE" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_verify_chain_detects_prev_hash_break(db_conn, db_path):
    """Directly altering a prev_hash mid-chain must cause verify_audit_chain to fail."""
    for i in range(4):
        await insert_audit_event(
            db_conn, timestamp=4000 + i, event="staged", actor="ai_agent",
        )
        await db_conn.commit()

    # Break the chain: set row 3's prev_hash to something wrong.
    await db_conn.execute(
        "UPDATE audit_log SET prev_hash = ? WHERE id = 3",
        ("b" * 64,),
    )
    await db_conn.commit()

    ok, errors = await verify_audit_chain(db_path)
    assert ok is False
    assert len(errors) >= 1


# ---------------------------------------------------------------------------
# Null / optional fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_fields_handled_consistently(db_conn, db_path):
    """Rows with all-None optional fields must still chain correctly."""
    for i in range(3):
        await insert_audit_event(
            db_conn, timestamp=5000 + i, event="expired", actor=None,
            operation_id=None, agent_id=None, op_type=None, detail=None, user_id=None,
        )
        await db_conn.commit()

    ok, errors = await verify_audit_chain(db_path)
    assert ok is True, f"Chain verification failed: {errors}"


# ---------------------------------------------------------------------------
# Pre-migration rows (entry_hash IS NULL) are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_rows_without_hash_skipped(db_conn, db_path):
    """verify_audit_chain must skip rows where entry_hash IS NULL."""
    # Insert a "legacy" row directly, bypassing insert_audit_event.
    await db_conn.execute(
        """
        INSERT INTO audit_log (timestamp, event, actor, prev_hash, entry_hash)
        VALUES (999, 'legacy_event', 'system', NULL, NULL)
        """
    )
    await db_conn.commit()

    # Now insert two proper hashed rows.
    for i in range(2):
        await insert_audit_event(
            db_conn, timestamp=6000 + i, event="staged", actor="ai_agent",
        )
        await db_conn.commit()

    ok, errors = await verify_audit_chain(db_path)
    assert ok is True, f"Chain verification should pass; errors: {errors}"

    # Sanity: only 2 rows should have entry_hash set.
    async with db_conn.execute(
        "SELECT COUNT(*) AS cnt FROM audit_log WHERE entry_hash IS NOT NULL"
    ) as cur:
        row = await cur.fetchone()
    assert row["cnt"] == 2


# ---------------------------------------------------------------------------
# record_audit_event — standalone convenience wrapper
# ---------------------------------------------------------------------------


async def test_record_audit_event_persists_and_commits(db_path: Path) -> None:
    """record_audit_event opens its own connection, writes a row, and commits."""
    await record_audit_event(
        db_path, timestamp=7000, event="executed", actor="human",
        operation_id="op_abc", op_type="move",
    )
    # Visible from a fresh connection → it committed.
    async with get_db(db_path) as db, db.execute(
        "SELECT event, actor, operation_id, op_type, entry_hash "
        "FROM audit_log WHERE operation_id = 'op_abc'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["event"] == "executed"
    assert row["actor"] == "human"
    assert row["op_type"] == "move"
    assert row["entry_hash"] is not None  # hash chaining still applied


async def test_record_audit_event_chains_with_insert(db_path: Path) -> None:
    """Rows written via record_audit_event chain cleanly with insert_audit_event."""
    await record_audit_event(db_path, timestamp=7100, event="staged", actor="ai_agent")
    await record_audit_event(db_path, timestamp=7200, event="executed", actor="human")
    ok, errors = await verify_audit_chain(db_path)
    assert ok is True, f"Chain should verify; errors: {errors}"


# ---------------------------------------------------------------------------
# intent_label — stored but excluded from the entry hash
# ---------------------------------------------------------------------------


async def test_intent_label_stored_and_chain_verifies(db_path: Path) -> None:
    """Rows with intent_label persist it and still verify; mixing with
    intent-less rows (the pre-existing format) keeps the chain intact."""
    await record_audit_event(
        db_path, timestamp=8000, event="staged", actor="ai_agent",
        operation_id="op_int01", op_type="move", intent_label="archive",
    )
    await record_audit_event(db_path, timestamp=8100, event="executed", actor="human")
    await record_audit_event(
        db_path, timestamp=8200, event="rejected", actor="human",
        operation_id="op_int02", op_type="trash", intent_label="delete",
    )

    async with get_db(db_path) as db, db.execute(
        "SELECT intent_label FROM audit_log WHERE operation_id = 'op_int01'"
    ) as cur:
        row = await cur.fetchone()
    assert row["intent_label"] == "archive"

    ok, errors = await verify_audit_chain(db_path)
    assert ok is True, f"Chain should verify with intent_label rows; errors: {errors}"


async def test_intent_label_does_not_affect_entry_hash(db_path: Path) -> None:
    """Tampering with intent_label must not break the chain (it is informational
    metadata excluded from the canonical hash payload — see gateway/audit.py),
    while tampering with a hashed field still does."""
    await record_audit_event(
        db_path, timestamp=9000, event="staged", actor="ai_agent",
        operation_id="op_int03", op_type="move", intent_label="archive",
    )
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE audit_log SET intent_label = 'delete' WHERE operation_id = 'op_int03'"
        )
        await db.commit()
    ok, errors = await verify_audit_chain(db_path)
    assert ok is True, f"intent_label is not hashed; chain must still verify: {errors}"
