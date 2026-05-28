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
import pytest
import pytest_asyncio
import aiosqlite

from pathlib import Path

from gateway.audit import (
    GENESIS_HASH,
    _compute_entry_hash,
    insert_audit_event,
    verify_audit_chain,
)
from gateway.state_db import init_db


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
    return dict(
        prev_hash=GENESIS_HASH,
        row_id=1,
        timestamp=1_700_000_000,
        operation_id="op_abc123",
        event="staged",
        actor="ai_agent",
        agent_id="agent_42",
        op_type="move",
        detail='{"foo": "bar"}',
        user_id=7,
    )


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
