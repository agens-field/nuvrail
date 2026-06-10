"""
Unit tests for batch summaries (gateway.batch_summary) — the one-line
"commit message" shown as a multi-op batch's headline.
"""
from __future__ import annotations

import json

from gateway.batch_summary import build_batch_summary, count_uid_set


def _op(
    op_type: str,
    intent_label: str | None = None,
    message_ids: list[str] | None = None,
    folder_from: str | None = "INBOX",
    folder_to: str | None = None,
) -> dict:
    """Build a staged_operations-row-shaped dict (message_ids JSON-encoded,
    as it comes out of SQLite)."""
    return {
        "op_type": op_type,
        "intent_label": intent_label,
        "message_ids": json.dumps(message_ids) if message_ids is not None else None,
        "folder_from": folder_from,
        "folder_to": folder_to,
    }


# ---------------------------------------------------------------------------
# count_uid_set
# ---------------------------------------------------------------------------


class TestCountUidSet:
    def test_single_uid(self):
        assert count_uid_set("42") == 1

    def test_range(self):
        assert count_uid_set("1:5") == 5

    def test_comma_list_with_range(self):
        assert count_uid_set("1:3,7,9") == 5

    def test_open_range_counts_one(self):
        assert count_uid_set("12:*") == 1

    def test_empty(self):
        assert count_uid_set("") == 0
        assert count_uid_set(None) == 0


# ---------------------------------------------------------------------------
# build_batch_summary — uniform batches collapse to a verb phrase
# ---------------------------------------------------------------------------


class TestUniformBatch:
    def test_all_archive_collapses(self):
        ops = [
            _op("move", "archive", ["1:5"], folder_to="[Gmail]/All Mail"),
            _op("move", "archive", ["9"], folder_to="[Gmail]/All Mail"),
        ]
        assert build_batch_summary(ops) == "Archive 6 messages"

    def test_all_moves_with_common_destination(self):
        ops = [
            _op("move", None, ["1:5"], folder_to="Receipts"),
            _op("move", None, ["7,8"], folder_to="Receipts"),
        ]
        assert build_batch_summary(ops) == "Move 7 messages to Receipts"

    def test_all_moves_mixed_destinations_no_suffix(self):
        ops = [
            _op("move", None, ["1"], folder_to="Receipts"),
            _op("move", None, ["2"], folder_to="Travel"),
        ]
        assert build_batch_summary(ops) == "Move 2 messages"

    def test_all_deletes(self):
        ops = [
            _op("trash", "delete", ["1,2"]),
            _op("move", "delete", ["3"], folder_to="[Gmail]/Trash"),
        ]
        assert build_batch_summary(ops) == "Move to Trash 3 messages"

    def test_single_message_singular_noun(self):
        ops = [
            _op("mark_read", None, ["1"]),
            _op("mark_read", None, ["1"]),
        ]
        # Two ops, two messages
        assert build_batch_summary(ops) == "Mark as read 2 messages"

    def test_folder_ops_use_fragment_form(self):
        ops = [
            _op("create", None, None, folder_from=None, folder_to="Projects"),
            _op("create", None, None, folder_from=None, folder_to="Archive2024"),
        ]
        assert build_batch_summary(ops) == "2 folders created"


# ---------------------------------------------------------------------------
# build_batch_summary — mixed batches get a triage headline
# ---------------------------------------------------------------------------


class TestMixedBatch:
    def test_inbox_triage_headline_with_fragments(self):
        ops = [
            _op("move", "archive", ["1:12"], folder_to="[Gmail]/All Mail"),
            _op("mark_read", None, ["20:22"]),
            _op("move", None, ["30,31"], folder_to="Receipts"),
        ]
        assert build_batch_summary(ops) == (
            "Inbox triage — 12 archived, 3 marked read, 2 moved to Receipts"
        )

    def test_fragments_ordered_by_count(self):
        ops = [
            _op("mark_read", None, ["1"]),
            _op("move", "archive", ["10:14"], folder_to="[Gmail]/All Mail"),
        ]
        assert build_batch_summary(ops) == "Inbox triage — 5 archived, 1 marked read"

    def test_mixed_source_folders_generic_headline(self):
        ops = [
            _op("move", "archive", ["1"], folder_from="INBOX", folder_to="[Gmail]/All Mail"),
            _op("mark_read", None, ["2"], folder_from="Newsletters"),
        ]
        assert build_batch_summary(ops).startswith("Mailbox triage — ")

    def test_named_folder_headline(self):
        ops = [
            _op("trash", "delete", ["1,2"], folder_from="Newsletters"),
            _op("mark_read", None, ["3"], folder_from="Newsletters"),
        ]
        assert build_batch_summary(ops) == (
            "Newsletters triage — 2 deleted, 1 marked read"
        )

    def test_intent_preferred_over_op_type(self):
        # A delete-intent MOVE and a trash STORE land in the same bucket
        ops = [
            _op("move", "delete", ["1"], folder_to="[Gmail]/Trash"),
            _op("trash", "delete", ["2"]),
            _op("flag", None, ["3"]),
        ]
        assert build_batch_summary(ops) == "Inbox triage — 2 deleted, 1 starred"

    def test_unknown_label_falls_back_gracefully(self):
        ops = [
            _op("mystery_op", None, ["1"]),
            _op("mark_read", None, ["2"]),
        ]
        summary = build_batch_summary(ops)
        assert "1 mystery op" in summary
        assert "1 marked read" in summary


# ---------------------------------------------------------------------------
# edges
# ---------------------------------------------------------------------------


class TestEdges:
    def test_empty_list(self):
        assert build_batch_summary([]) == ""

    def test_op_without_message_ids_counts_as_one(self):
        ops = [
            _op("move", "archive", None, folder_to="[Gmail]/All Mail"),
            _op("move", "archive", ["2"], folder_to="[Gmail]/All Mail"),
        ]
        assert build_batch_summary(ops) == "Archive 2 messages"
