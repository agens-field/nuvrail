"""
Unit tests for state_db sync functions (Milestone 0.4).

Uses tmp_path-isolated SQLite DB — never touches ~/.nuvrail.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.state_db import (
    apply_optimistic_flag_update,
    get_message,
    get_messages_by_uid_set,
    get_or_create_folder,
    init_db,
    remove_messages_from_folder,
    restore_from_snapshot,
    snapshot_messages,
    update_folder_stats,
    upsert_folders_from_list,
    upsert_message,
)


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    """Return a path to a freshly initialised test DB."""
    path = tmp_path / "test.db"
    await init_db(path)
    return path


# ---------------------------------------------------------------------------
# Folder sync tests
# ---------------------------------------------------------------------------


async def test_get_or_create_folder_creates_new(db_path: Path) -> None:
    """A new folder row is created and its id returned."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    assert isinstance(folder_id, int)
    assert folder_id > 0


async def test_get_or_create_folder_idempotent(db_path: Path) -> None:
    """Calling twice returns the same id."""
    id1 = await get_or_create_folder("INBOX", db_path=db_path)
    id2 = await get_or_create_folder("INBOX", db_path=db_path)
    assert id1 == id2


async def test_update_folder_stats(db_path: Path) -> None:
    """EXISTS, UIDVALIDITY etc. are stored correctly."""
    folder_id = await update_folder_stats(
        "INBOX",
        exists_count=47,
        recent_count=2,
        uidvalidity=1234567890,
        uidnext=48,
        unseen_count=5,
        db_path=db_path,
    )
    assert isinstance(folder_id, int)

    from gateway.state_db import get_db

    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row["name"] == "INBOX"
    assert row["exists_count"] == 47
    assert row["recent_count"] == 2
    assert row["uidvalidity"] == 1234567890
    assert row["uidnext"] == 48
    assert row["unseen_count"] == 5


async def test_upsert_folders_from_list(db_path: Path) -> None:
    """Multiple folders are created idempotently."""
    await upsert_folders_from_list(["INBOX", "Sent", "Archive"], db_path=db_path)
    await upsert_folders_from_list(["INBOX", "Trash"], db_path=db_path)  # idempotent

    from gateway.state_db import get_db

    async with get_db(db_path) as db:
        async with db.execute("SELECT name FROM folders ORDER BY name") as cur:
            rows = await cur.fetchall()

    names = {r["name"] for r in rows}
    assert {"INBOX", "Sent", "Archive", "Trash"} <= names


# ---------------------------------------------------------------------------
# Message sync tests
# ---------------------------------------------------------------------------


async def _setup_folder(db_path: Path, name: str = "INBOX") -> int:
    return await get_or_create_folder(name, db_path=db_path)


async def test_upsert_message_creates_row(db_path: Path) -> None:
    """A message row is created with uid, flags, and subject."""
    folder_id = await _setup_folder(db_path)
    await upsert_message(
        folder_id,
        uid=42,
        seq_num=1,
        flags=["\\Seen"],
        subject="Hello world",
        sender="alice@example.com",
        db_path=db_path,
    )
    row = await get_message(folder_id, 42, db_path=db_path)
    assert row is not None
    assert row["uid"] == 42
    assert row["subject"] == "Hello world"
    assert json.loads(row["flags"]) == ["\\Seen"]


async def test_upsert_message_updates_existing(db_path: Path) -> None:
    """Second upsert updates flags but preserves subject."""
    folder_id = await _setup_folder(db_path)
    await upsert_message(folder_id, uid=42, flags=["\\Seen"], subject="Hi", db_path=db_path)
    await upsert_message(folder_id, uid=42, flags=["\\Seen", "\\Answered"], db_path=db_path)

    row = await get_message(folder_id, 42, db_path=db_path)
    assert row is not None
    assert json.loads(row["flags"]) == ["\\Seen", "\\Answered"]
    # Subject preserved (second call passed None)
    assert row["subject"] == "Hi"


async def test_get_message_not_found(db_path: Path) -> None:
    """Returns None for a UID that doesn't exist."""
    folder_id = await _setup_folder(db_path)
    result = await get_message(folder_id, 9999, db_path=db_path)
    assert result is None


# ---------------------------------------------------------------------------
# UID set tests
# ---------------------------------------------------------------------------


async def _seed_messages(db_path: Path, folder_id: int, uids: "list[int]") -> None:
    for i, uid in enumerate(uids, start=1):
        await upsert_message(folder_id, uid=uid, seq_num=i, db_path=db_path)


async def test_get_messages_by_uid_set_single(db_path: Path) -> None:
    """Single UID string '42' returns exactly that row."""
    folder_id = await _setup_folder(db_path)
    await _seed_messages(db_path, folder_id, [40, 42, 45])

    rows = await get_messages_by_uid_set(folder_id, "42", db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["uid"] == 42


async def test_get_messages_by_uid_set_range(db_path: Path) -> None:
    """Range '1:3' returns 3 rows."""
    folder_id = await _setup_folder(db_path)
    await _seed_messages(db_path, folder_id, [1, 2, 3, 4, 5])

    rows = await get_messages_by_uid_set(folder_id, "1:3", db_path=db_path)
    assert len(rows) == 3
    assert {r["uid"] for r in rows} == {1, 2, 3}


async def test_get_messages_by_uid_set_list(db_path: Path) -> None:
    """Comma list '1,3,5' returns exactly those 3 rows."""
    folder_id = await _setup_folder(db_path)
    await _seed_messages(db_path, folder_id, [1, 2, 3, 4, 5])

    rows = await get_messages_by_uid_set(folder_id, "1,3,5", db_path=db_path)
    assert len(rows) == 3
    assert {r["uid"] for r in rows} == {1, 3, 5}


async def test_get_messages_by_uid_set_star(db_path: Path) -> None:
    """'*' returns only the highest UID."""
    folder_id = await _setup_folder(db_path)
    await _seed_messages(db_path, folder_id, [10, 20, 30])

    rows = await get_messages_by_uid_set(folder_id, "*", db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["uid"] == 30


async def test_get_messages_by_uid_set_range_with_star(db_path: Path) -> None:
    """'1:*' returns all rows."""
    folder_id = await _setup_folder(db_path)
    await _seed_messages(db_path, folder_id, [1, 2, 3])

    rows = await get_messages_by_uid_set(folder_id, "1:*", db_path=db_path)
    assert len(rows) == 3


async def test_get_messages_by_uid_set_mixed(db_path: Path) -> None:
    """'1:3,5,10:12' returns correct rows from the seeded set."""
    folder_id = await _setup_folder(db_path)
    await _seed_messages(db_path, folder_id, [1, 2, 3, 5, 9, 10, 11, 12, 15])

    rows = await get_messages_by_uid_set(folder_id, "1:3,5,10:12", db_path=db_path)
    uids = {r["uid"] for r in rows}
    assert uids == {1, 2, 3, 5, 10, 11, 12}


# ---------------------------------------------------------------------------
# Snapshot and optimistic update tests (milestone 1.2)
# ---------------------------------------------------------------------------


async def test_snapshot_messages_captures_flags(db_path: Path) -> None:
    """snapshot_messages returns current flags for matching UIDs."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    await upsert_message(folder_id, 10, seq_num=1, flags=[r"\Seen"], db_path=db_path)
    await upsert_message(folder_id, 11, seq_num=2, flags=[], db_path=db_path)

    snap = await snapshot_messages(folder_id, "10,11", db_path=db_path)

    assert "10" in snap
    assert "11" in snap
    assert r"\Seen" in snap["10"]["flags"]
    assert snap["11"]["flags"] == []
    assert snap["10"]["folder_id"] == folder_id


async def test_snapshot_messages_empty_uid_set(db_path: Path) -> None:
    """snapshot_messages returns empty dict when no messages match."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    snap = await snapshot_messages(folder_id, "999", db_path=db_path)
    assert snap == {}


async def test_snapshot_messages_range(db_path: Path) -> None:
    """snapshot_messages handles range uid_set_str."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    for uid in [1, 2, 3]:
        await upsert_message(folder_id, uid, seq_num=uid, flags=[r"\Seen"], db_path=db_path)

    snap = await snapshot_messages(folder_id, "1:3", db_path=db_path)
    assert len(snap) == 3
    assert all(k in snap for k in ["1", "2", "3"])


async def test_apply_optimistic_flag_update_adds_flags(db_path: Path) -> None:
    """apply_optimistic_flag_update adds flags to existing messages."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    await upsert_message(folder_id, 42, seq_num=1, flags=[], db_path=db_path)

    await apply_optimistic_flag_update(
        folder_id, "42", flags_add=[r"\Seen"], flags_remove=[], db_path=db_path
    )

    msg = await get_message(folder_id, 42, db_path=db_path)
    import json as _json
    flags = _json.loads(msg["flags"])
    assert r"\Seen" in flags


async def test_apply_optimistic_flag_update_removes_flags(db_path: Path) -> None:
    """apply_optimistic_flag_update removes flags from existing messages."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    await upsert_message(folder_id, 42, seq_num=1, flags=[r"\Seen", r"\Flagged"], db_path=db_path)

    await apply_optimistic_flag_update(
        folder_id, "42", flags_add=[], flags_remove=[r"\Flagged"], db_path=db_path
    )

    msg = await get_message(folder_id, 42, db_path=db_path)
    import json as _json
    flags = _json.loads(msg["flags"])
    assert r"\Seen" in flags
    assert r"\Flagged" not in flags


async def test_apply_optimistic_flag_update_no_op_for_unknown_uid(db_path: Path) -> None:
    """apply_optimistic_flag_update silently skips UIDs not in the DB."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    # Should not raise even with no matching messages
    await apply_optimistic_flag_update(
        folder_id, "999", flags_add=[r"\Seen"], flags_remove=[], db_path=db_path
    )


async def test_snapshot_preserves_seq_num(db_path: Path) -> None:
    """snapshot includes seq_num for each UID."""
    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    await upsert_message(folder_id, 7, seq_num=3, flags=[r"\Seen"], db_path=db_path)

    snap = await snapshot_messages(folder_id, "7", db_path=db_path)
    assert snap["7"]["seq_num"] == 3


# ---------------------------------------------------------------------------
# pending_reverts tests (milestone 1.2 — rejection revert)
# ---------------------------------------------------------------------------


async def test_restore_from_snapshot_restores_flags(db_path: Path) -> None:
    """restore_from_snapshot reverts messages table to pre-op flags."""
    import json
    from gateway.staging import create_operation

    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    # Message starts with no flags
    await upsert_message(folder_id, 42, seq_num=1, flags=[], db_path=db_path)

    # Stage a mark-as-read with a snapshot capturing the empty flags
    snap = {"42": {"flags": [], "seq_num": 1, "folder_id": folder_id}}
    op_id = await create_operation(
        op_type="mark_read",
        protocol="imap",
        description="Mark as read",
        snapshot=snap,
        db_path=db_path,
    )

    # Simulate optimistic update: set \Seen
    await apply_optimistic_flag_update(folder_id, "42", flags_add=[r"\Seen"], flags_remove=[], db_path=db_path)
    msg_after_stage = await get_message(folder_id, 42, db_path=db_path)
    assert r"\Seen" in json.loads(msg_after_stage["flags"])

    # Now restore from snapshot (simulates reject)
    from gateway.state_db import restore_from_snapshot
    reverts = await restore_from_snapshot(op_id, db_path=db_path)

    assert len(reverts) == 1
    assert reverts[0]["uid"] == 42
    assert reverts[0]["folder_id"] == folder_id

    # Messages table should be back to empty flags
    msg_after_revert = await get_message(folder_id, 42, db_path=db_path)
    assert json.loads(msg_after_revert["flags"]) == []


async def test_restore_from_snapshot_no_snapshot_returns_empty(db_path: Path) -> None:
    """restore_from_snapshot returns [] for ops without a snapshot."""
    from gateway.staging import create_operation
    from gateway.state_db import restore_from_snapshot

    op_id = await create_operation(
        op_type="move", protocol="imap", description="Move", db_path=db_path
    )
    reverts = await restore_from_snapshot(op_id, db_path=db_path)
    assert reverts == []


async def test_insert_and_get_pending_reverts(db_path: Path) -> None:
    """insert_pending_reverts creates rows; get_pending_reverts returns undelivered."""
    import json
    from gateway.staging import create_operation
    from gateway.state_db import get_pending_reverts, insert_pending_reverts

    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    op_id = await create_operation(
        op_type="mark_read", protocol="imap", description="x", db_path=db_path
    )

    reverts = [
        {"operation_id": op_id, "folder_id": folder_id, "uid": 10, "true_flags": json.dumps([])},
        {"operation_id": op_id, "folder_id": folder_id, "uid": 11, "true_flags": json.dumps([r"\Seen"])},
    ]
    await insert_pending_reverts(op_id, reverts, db_path=db_path)

    rows = await get_pending_reverts(folder_id, db_path=db_path)
    assert len(rows) == 2
    uids = {r["uid"] for r in rows}
    assert uids == {10, 11}
    # All undelivered
    assert all(r.get("delivered_at") is None for r in rows)


async def test_mark_reverts_delivered(db_path: Path) -> None:
    """mark_reverts_delivered sets delivered_at; get_pending_reverts excludes them."""
    import json
    from gateway.staging import create_operation
    from gateway.state_db import get_pending_reverts, insert_pending_reverts, mark_reverts_delivered

    folder_id = await get_or_create_folder("INBOX", db_path=db_path)
    op_id = await create_operation(
        op_type="mark_read", protocol="imap", description="x", db_path=db_path
    )
    reverts = [
        {"operation_id": op_id, "folder_id": folder_id, "uid": 20, "true_flags": json.dumps([])},
    ]
    await insert_pending_reverts(op_id, reverts, db_path=db_path)

    pending = await get_pending_reverts(folder_id, db_path=db_path)
    assert len(pending) == 1

    await mark_reverts_delivered([pending[0]["id"]], db_path=db_path)

    # Should now be empty (delivered)
    still_pending = await get_pending_reverts(folder_id, db_path=db_path)
    assert still_pending == []


async def test_get_pending_reverts_different_folder_not_returned(db_path: Path) -> None:
    """get_pending_reverts only returns rows for the requested folder_id."""
    import json
    from gateway.staging import create_operation
    from gateway.state_db import get_pending_reverts, insert_pending_reverts

    inbox_id = await get_or_create_folder("INBOX", db_path=db_path)
    sent_id = await get_or_create_folder("Sent", db_path=db_path)

    op_id = await create_operation(
        op_type="mark_read", protocol="imap", description="x", db_path=db_path
    )
    reverts = [
        {"operation_id": op_id, "folder_id": inbox_id, "uid": 1, "true_flags": json.dumps([])},
    ]
    await insert_pending_reverts(op_id, reverts, db_path=db_path)

    # Querying for Sent should return nothing
    sent_rows = await get_pending_reverts(sent_id, db_path=db_path)
    assert sent_rows == []

    # Querying for INBOX should return 1
    inbox_rows = await get_pending_reverts(inbox_id, db_path=db_path)
    assert len(inbox_rows) == 1


# ---------------------------------------------------------------------------
# remove_messages_from_folder + MOVE snapshot/restore tests
# ---------------------------------------------------------------------------




async def test_remove_messages_from_folder_single_uid(db_path: Path) -> None:
    """Removing a single UID deletes only that row."""
    folder_id = await _setup_folder(db_path)
    await upsert_message(folder_id, uid=10, seq_num=1, db_path=db_path)
    await upsert_message(folder_id, uid=20, seq_num=2, db_path=db_path)

    await remove_messages_from_folder(folder_id, "10", db_path=db_path)

    remaining = await get_messages_by_uid_set(folder_id, "10,20", db_path=db_path)
    uids = {r["uid"] for r in remaining}
    assert 10 not in uids, "UID 10 should have been removed"
    assert 20 in uids, "UID 20 should still be present"


async def test_remove_messages_from_folder_range(db_path: Path) -> None:
    """Removing a range deletes only the UIDs in that range."""
    folder_id = await _setup_folder(db_path)
    for uid in [1, 2, 3, 4, 5]:
        await upsert_message(folder_id, uid=uid, seq_num=uid, db_path=db_path)

    await remove_messages_from_folder(folder_id, "2:4", db_path=db_path)

    remaining = await get_messages_by_uid_set(folder_id, "1:5", db_path=db_path)
    uids = {r["uid"] for r in remaining}
    assert uids == {1, 5}, f"Expected only UIDs 1 and 5, got {uids}"


async def test_snapshot_captures_sender_subject(db_path: Path) -> None:
    """snapshot_messages includes sender and subject in the snapshot."""
    folder_id = await _setup_folder(db_path)
    await upsert_message(
        folder_id, uid=42, seq_num=1,
        sender="alice@example.com", subject="Q3 report",
        db_path=db_path,
    )

    snap = await snapshot_messages(folder_id, "42", db_path=db_path)
    assert "42" in snap
    assert snap["42"]["sender"] == "alice@example.com"
    assert snap["42"]["subject"] == "Q3 report"


async def test_restore_from_snapshot_reinserts_deleted_message(db_path: Path) -> None:
    """On MOVE rollback: restore_from_snapshot re-inserts a deleted message row."""
    from gateway.staging import create_operation, update_operation_status

    folder_id = await _setup_folder(db_path)
    await upsert_message(
        folder_id, uid=99, seq_num=3,
        flags=["\\Seen"], sender="bob@example.com", subject="Important",
        db_path=db_path,
    )

    # Stage a MOVE op with a full snapshot (including sender/subject)
    snap = await snapshot_messages(folder_id, "99", db_path=db_path)
    op_id = await create_operation(
        op_type="move",
        protocol="imap",
        description="Move 99 to Archive",
        imap_command="UID MOVE 99 Archive",
        message_ids=["99"],
        folder_from="INBOX",
        folder_to="Archive",
        snapshot=snap,
        db_path=db_path,
    )

    # Simulate MOVE optimistic update: delete the row
    await remove_messages_from_folder(folder_id, "99", db_path=db_path)
    gone = await get_messages_by_uid_set(folder_id, "99", db_path=db_path)
    assert gone == [], "Message should be gone after MOVE staging"

    # Reject the operation — restore_from_snapshot should re-insert the row
    from gateway.state_db import restore_from_snapshot
    reverts = await restore_from_snapshot(op_id, db_path=db_path)

    restored = await get_messages_by_uid_set(folder_id, "99", db_path=db_path)
    assert len(restored) == 1, "Message should be re-inserted after MOVE rollback"
    row = restored[0]
    assert row["uid"] == 99
    assert row["sender"] == "bob@example.com"
    assert row["subject"] == "Important"
    import json as _json
    assert "\\Seen" in _json.loads(row["flags"] or "[]")
