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
import json
import os
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from gateway.audit import (
    GENESIS_HASH,
    _compute_entry_hash,
    anchor_chain_head,
    get_chain_head,
    insert_audit_event,
    last_verification_result,
    read_last_anchor,
    record_audit_event,
    run_audit_verification_loop,
    verify_against_anchor,
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


# ---------------------------------------------------------------------------
# External chain-head anchoring — tail-truncation detection
#
# The in-DB chain walk (verify_audit_chain) cannot detect deletion of the
# most-recent rows: a truncated chain is still internally consistent. These
# tests prove the external-anchor path closes that gap.
#
#   normal path : anchor head H3 → verify clean (H3 still in chain)
#   attack path : truncate tail so H3 gone → verify against anchor DETECTS it
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def anchor_path(tmp_path: Path) -> Path:
    """Path to an external anchor sink file (does not exist until written)."""
    return tmp_path / "anchors" / "audit-anchor.jsonl"


async def _seed_rows(db_conn, db_path, n: int, base_ts: int = 5000) -> str:
    """Insert n hashed rows, commit each, return the resulting chain head."""
    for i in range(n):
        await insert_audit_event(
            db_conn, timestamp=base_ts + i, event="staged", actor="ai_agent",
        )
        await db_conn.commit()
    return await get_chain_head(db_path)


async def _truncate_tail(db_path: Path, n: int) -> None:
    """Delete the n most-recent hashed rows (simulate an attacker's tail cut)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM audit_log WHERE id IN "
            "(SELECT id FROM audit_log WHERE entry_hash IS NOT NULL "
            " ORDER BY id DESC LIMIT ?)",
            (n,),
        )
        await db.commit()


async def test_anchor_disabled_returns_none(db_conn, db_path):
    """With no configured/passed sink, anchoring is a no-op."""
    await _seed_rows(db_conn, db_path, 2)
    # No env, no explicit path → disabled.
    assert await anchor_chain_head(db_path, anchor_path=None) is None or \
        os.environ.get("NUVRAIL_AUDIT_ANCHOR_PATH")  # guard if env set in CI


async def test_anchor_writes_appendonly_record(db_conn, db_path, anchor_path):
    """anchor_chain_head appends one JSONL record carrying the current head."""
    head = await _seed_rows(db_conn, db_path, 3)
    rec = await anchor_chain_head(db_path, anchor_path=anchor_path)
    assert rec is not None
    assert rec["chain_head"] == head
    assert rec["row_count"] == 3
    # File exists and holds exactly one line.
    lines = anchor_path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["chain_head"] == head


async def test_anchor_is_append_only(db_conn, db_path, anchor_path):
    """Successive anchors append; earlier records are never rewritten."""
    await _seed_rows(db_conn, db_path, 2)
    r1 = await anchor_chain_head(db_path, anchor_path=anchor_path)
    await _seed_rows(db_conn, db_path, 1, base_ts=6000)  # one more row → new head
    r2 = await anchor_chain_head(db_path, anchor_path=anchor_path)
    assert r1["chain_head"] != r2["chain_head"]
    lines = anchor_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["chain_head"] == r1["chain_head"]
    assert read_last_anchor(anchor_path)["chain_head"] == r2["chain_head"]


async def test_anchor_empty_chain_is_noop(db_path, anchor_path):
    """Nothing to anchor on an empty chain; no file is created."""
    assert await anchor_chain_head(db_path, anchor_path=anchor_path) is None
    assert not anchor_path.exists()


async def test_read_last_anchor_none_when_missing(anchor_path):
    """No sink file → no last anchor."""
    assert read_last_anchor(anchor_path) is None


async def test_read_last_anchor_skips_malformed_trailing_line(
    db_conn, db_path, anchor_path
):
    """A torn/partial last line must not mask a valid earlier anchor."""
    await _seed_rows(db_conn, db_path, 2)
    rec = await anchor_chain_head(db_path, anchor_path=anchor_path)
    # Simulate a partial write appended after a clean anchor.
    with open(anchor_path, "a", encoding="utf-8") as fh:
        fh.write('{"anchored_at": 123, "chain_he')  # truncated JSON, no newline
    last = read_last_anchor(anchor_path)
    assert last is not None
    assert last["chain_head"] == rec["chain_head"]


async def test_verify_against_anchor_clean_when_tail_intact(
    db_conn, db_path, anchor_path
):
    """Normal path: anchored head still present → tail intact, no divergence."""
    await _seed_rows(db_conn, db_path, 3)
    await anchor_chain_head(db_path, anchor_path=anchor_path)
    ok, reason = await verify_against_anchor(db_path, anchor_path=anchor_path)
    assert ok is True
    assert reason is None


async def test_verify_against_anchor_detects_tail_truncation(
    db_conn, db_path, anchor_path
):
    """THE gap-closing test: truncating the tail is undetectable by the in-DB
    chain walk but IS detected against the external anchor."""
    await _seed_rows(db_conn, db_path, 3)
    await anchor_chain_head(db_path, anchor_path=anchor_path)

    # Attacker deletes the most-recent row (tail truncation).
    await _truncate_tail(db_path, 1)

    # The in-DB chain walk still reports intact — this is the gap.
    chain_ok, chain_errors = await verify_audit_chain(db_path)
    assert chain_ok is True, (
        "precondition: in-DB walk cannot see tail truncation; "
        f"unexpected errors: {chain_errors}"
    )

    # The external anchor comparison catches it.
    anchor_ok, reason = await verify_against_anchor(db_path, anchor_path=anchor_path)
    assert anchor_ok is False
    assert reason is not None and "tail truncation" in reason


async def test_verify_against_anchor_noop_without_anchor(db_conn, db_path, anchor_path):
    """No anchor recorded yet → nothing to prove, not a failure."""
    await _seed_rows(db_conn, db_path, 2)
    ok, reason = await verify_against_anchor(db_path, anchor_path=anchor_path)
    assert ok is True
    assert reason is None


async def test_anchor_sink_path_reads_env(monkeypatch, tmp_path):
    """anchor_sink_path resolves NUVRAIL_AUDIT_ANCHOR_PATH at call time."""
    from gateway.audit import anchor_sink_path
    monkeypatch.delenv("NUVRAIL_AUDIT_ANCHOR_PATH", raising=False)
    assert anchor_sink_path() is None
    target = tmp_path / "env-anchor.jsonl"
    monkeypatch.setenv("NUVRAIL_AUDIT_ANCHOR_PATH", str(target))
    assert anchor_sink_path() == target
