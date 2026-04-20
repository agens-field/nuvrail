"""
Unit tests for IMAP → Operation parser and rich description builder.

Sub-milestone: 1.1
"""
import pytest

from gateway.operation_parser import (
    build_rich_description,
    parse_copy,
    parse_move,
    parse_store,
)


# ---------------------------------------------------------------------------
# build_rich_description — single message
# ---------------------------------------------------------------------------


class TestBuildRichDescriptionSingle:
    """Single-message operations with various metadata combinations."""

    def test_move_with_sender_and_subject(self):
        op = parse_move("A1", "42", "Archive")
        meta = [{"uid": 42, "sender": "billing@acme.com", "subject": "Invoice #1234"}]
        assert build_rich_description(op, meta) == 'Move: "Invoice #1234" from billing@acme.com to Archive'

    def test_move_subject_only(self):
        op = parse_move("A1", "42", "Archive")
        meta = [{"uid": 42, "sender": None, "subject": "Invoice #1234"}]
        assert build_rich_description(op, meta) == 'Move: "Invoice #1234" to Archive'

    def test_move_sender_only(self):
        op = parse_move("A1", "42", "Archive")
        meta = [{"uid": 42, "sender": "billing@acme.com", "subject": None}]
        assert build_rich_description(op, meta) == "Move: from billing@acme.com to Archive"

    def test_mark_read_with_sender_and_subject(self):
        op = parse_store("A1", uid_mode=True, uid_set="99", flags_op="+FLAGS", flags=["\\Seen"])
        meta = [{"uid": 99, "sender": "noreply@substack.com", "subject": "Weekly digest"}]
        assert build_rich_description(op, meta) == 'Mark as read: "Weekly digest" from noreply@substack.com'

    def test_trash_with_metadata(self):
        op = parse_store("A1", uid_mode=True, uid_set="7", flags_op="+FLAGS", flags=["\\Deleted"])
        meta = [{"uid": 7, "sender": "spam@evil.com", "subject": "You won!"}]
        assert build_rich_description(op, meta) == 'Move to Trash: "You won!" from spam@evil.com'

    def test_star_with_metadata(self):
        op = parse_store("A1", uid_mode=True, uid_set="55", flags_op="+FLAGS", flags=["\\Flagged"])
        meta = [{"uid": 55, "sender": "boss@company.com", "subject": "Action required"}]
        assert build_rich_description(op, meta) == 'Star: "Action required" from boss@company.com'

    def test_copy_with_metadata(self):
        op = parse_copy("A1", "88", "Backup")
        meta = [{"uid": 88, "sender": "alice@example.com", "subject": "Hello"}]
        assert build_rich_description(op, meta) == 'Copy: "Hello" from alice@example.com to Backup'

    def test_single_null_metadata_fields_falls_back(self):
        """Both sender and subject are null — fall back to UID-based description."""
        op = parse_move("A1", "42", "Archive")
        meta = [{"uid": 42, "sender": None, "subject": None}]
        # Falls back to the plain parsed_op.description
        assert build_rich_description(op, meta) == "Move 42 to Archive"

    def test_no_metadata_falls_back(self):
        """Empty metadata list — UID-based description unchanged."""
        op = parse_store("A1", uid_mode=True, uid_set="356", flags_op="+FLAGS", flags=["\\Seen"])
        assert build_rich_description(op, []) == "Mark as read: 356"


# ---------------------------------------------------------------------------
# build_rich_description — multiple messages, common sender
# ---------------------------------------------------------------------------


class TestBuildRichDescriptionMultiCommonSender:
    """Multiple messages all from the same sender."""

    def test_move_common_sender(self):
        op = parse_move("A1", "1:5", "Archive")
        meta = [
            {"uid": 1, "sender": "spam@evil.com", "subject": "Deal 1"},
            {"uid": 2, "sender": "spam@evil.com", "subject": "Deal 2"},
            {"uid": 3, "sender": "spam@evil.com", "subject": "Deal 3"},
        ]
        assert build_rich_description(op, meta) == "Move 3 messages from spam@evil.com to Archive"

    def test_trash_common_sender(self):
        op = parse_store("A1", uid_mode=True, uid_set="1:5", flags_op="+FLAGS", flags=["\\Deleted"])
        meta = [
            {"uid": 1, "sender": "newsletter@boring.com", "subject": "Update 1"},
            {"uid": 2, "sender": "newsletter@boring.com", "subject": "Update 2"},
        ]
        assert build_rich_description(op, meta) == "Move to Trash 2 messages from newsletter@boring.com"

    def test_mark_read_common_sender(self):
        op = parse_store("A1", uid_mode=True, uid_set="10,11,12", flags_op="+FLAGS", flags=["\\Seen"])
        meta = [
            {"uid": 10, "sender": "digest@news.com", "subject": "Mon"},
            {"uid": 11, "sender": "digest@news.com", "subject": "Tue"},
            {"uid": 12, "sender": "digest@news.com", "subject": "Wed"},
        ]
        assert build_rich_description(op, meta) == "Mark as read 3 messages from digest@news.com"

    def test_some_senders_none_counts_as_mixed(self):
        """If some messages have no sender, unique_senders will include only non-None.
        If that leaves a single unique sender, it's still treated as common."""
        op = parse_move("A1", "1:2", "Archive")
        meta = [
            {"uid": 1, "sender": "alice@example.com", "subject": "Hi"},
            {"uid": 2, "sender": None, "subject": "No sender"},
        ]
        # unique_senders = {"alice@example.com"} — only one, treated as common
        assert build_rich_description(op, meta) == "Move 2 messages from alice@example.com to Archive"

    def test_all_senders_none_falls_back_to_count(self):
        """No senders at all — unique_senders is empty, treated as mixed."""
        op = parse_move("A1", "1:3", "Archive")
        meta = [
            {"uid": 1, "sender": None, "subject": "A"},
            {"uid": 2, "sender": None, "subject": "B"},
            {"uid": 3, "sender": None, "subject": "C"},
        ]
        # unique_senders is empty → mixed path; first subject shown
        assert build_rich_description(op, meta) == 'Move "A" and 2 more to Archive'


# ---------------------------------------------------------------------------
# build_rich_description — multiple messages, mixed senders
# ---------------------------------------------------------------------------


class TestBuildRichDescriptionMultiMixedSender:
    """Multiple messages from different senders."""

    def test_move_mixed_senders(self):
        op = parse_move("A1", "1:3", "Archive")
        meta = [
            {"uid": 1, "sender": "alice@example.com", "subject": "Re: Invoice"},
            {"uid": 2, "sender": "bob@example.com", "subject": "Hello"},
            {"uid": 3, "sender": "carol@example.com", "subject": "Meeting"},
        ]
        assert build_rich_description(op, meta) == 'Move "Re: Invoice" and 2 more to Archive'

    def test_mixed_no_subject_on_first(self):
        op = parse_move("A1", "1:3", "Archive")
        meta = [
            {"uid": 1, "sender": "alice@example.com", "subject": None},
            {"uid": 2, "sender": "bob@example.com", "subject": "Hello"},
        ]
        # First message has no subject — falls back to count-only
        assert build_rich_description(op, meta) == "Move 2 messages to Archive"

    def test_mark_read_mixed_senders(self):
        op = parse_store("A1", uid_mode=True, uid_set="5,6,7", flags_op="+FLAGS", flags=["\\Seen"])
        meta = [
            {"uid": 5, "sender": "a@a.com", "subject": "First"},
            {"uid": 6, "sender": "b@b.com", "subject": "Second"},
        ]
        assert build_rich_description(op, meta) == 'Mark as read "First" and 1 more'


# ---------------------------------------------------------------------------
# build_rich_description — ops that don't use message metadata
# ---------------------------------------------------------------------------


class TestBuildRichDescriptionNonMessageOps:
    """APPEND, CREATE, RENAME, SMTP — returned unchanged."""

    def test_append_unchanged(self):
        from gateway.operation_parser import parse_append
        op = parse_append("A1", "Drafts", [], 512)
        result = build_rich_description(op, [{"uid": 1, "sender": "x@x.com", "subject": "y"}])
        assert result == op.description

    def test_create_unchanged(self):
        # Fake a create ParsedOperation
        from gateway.operation_parser import ParsedOperation
        op = ParsedOperation(
            tag="A1", op_type="create", imap_command="CREATE Projects",
            description="Create folder Projects", folder_to="Projects"
        )
        result = build_rich_description(op, [{"uid": 1, "sender": "x@x.com", "subject": "y"}])
        assert result == "Create folder Projects"

    def test_rename_unchanged(self):
        from gateway.operation_parser import ParsedOperation
        op = ParsedOperation(
            tag="A1", op_type="rename", imap_command="RENAME Old New",
            description="Rename Old → New", folder_from="Old", folder_to="New"
        )
        result = build_rich_description(op, [])
        assert result == "Rename Old → New"
