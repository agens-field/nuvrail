"""
Unit tests for IMAP → Operation parser and rich description builder.

Sub-milestone: 1.1
"""

from gateway.operation_parser import (
    build_rich_description,
    parse_append,
    parse_copy,
    parse_move,
    parse_store,
    unquote_mailbox,
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


# ---------------------------------------------------------------------------
# parse_store — compound flags and \Answered (#5)
# ---------------------------------------------------------------------------


class TestParseStoreCompoundFlags:
    def test_add_seen_and_flagged_describes_both(self):
        op = parse_store(
            "A1", uid_mode=True, uid_set="42", flags_op="+FLAGS",
            flags=["\\Seen", "\\Flagged"],
        )
        assert op.op_type == "mark_read"  # first recognised flag wins op_type
        assert op.description == "Mark as read and star: 42"
        assert op.flags_add == ["\\Seen", "\\Flagged"]

    def test_remove_seen_and_flagged_describes_both(self):
        op = parse_store(
            "A1", uid_mode=True, uid_set="42", flags_op="-FLAGS",
            flags=["\\Seen", "\\Flagged"],
        )
        assert op.op_type == "mark_unread"
        assert op.description == "Mark as unread and unstar: 42"
        assert op.flags_remove == ["\\Seen", "\\Flagged"]

    def test_deleted_dominates_compound(self):
        op = parse_store(
            "A1", uid_mode=True, uid_set="42", flags_op="+FLAGS",
            flags=["\\Seen", "\\Deleted"],
        )
        assert op.op_type == "trash"
        assert op.description == "Move to Trash: 42"

    def test_add_answered_is_mark_as_replied(self):
        op = parse_store(
            "A1", uid_mode=True, uid_set="42", flags_op="+FLAGS",
            flags=["\\Answered"],
        )
        # No dedicated op_type — executes as a generic flag replay
        assert op.op_type == "store"
        assert op.description == "Mark as replied: 42"
        assert op.flags_add == ["\\Answered"]

    def test_single_flag_behaviour_unchanged(self):
        op = parse_store("A1", uid_mode=True, uid_set="9", flags_op="+FLAGS", flags=["\\Seen"])
        assert op.op_type == "mark_read"
        assert op.description == "Mark as read: 9"

    def test_unknown_flag_falls_back_to_generic(self):
        op = parse_store("A1", uid_mode=True, uid_set="9", flags_op="+FLAGS", flags=["$Label1"])
        assert op.op_type == "store"
        assert op.description == "Flag update on 9: +FLAGS ($Label1)"

    def test_compound_verb_used_in_rich_description(self):
        op = parse_store(
            "A1", uid_mode=True, uid_set="42", flags_op="+FLAGS",
            flags=["\\Seen", "\\Flagged"],
        )
        meta = [{"uid": 42, "sender": "boss@company.com", "subject": "Action required"}]
        assert build_rich_description(op, meta) == (
            'Mark as read and star: "Action required" from boss@company.com'
        )


# ---------------------------------------------------------------------------
# parse_append — draft vs import (#4)
# ---------------------------------------------------------------------------


class TestParseAppend:
    def test_append_to_drafts_is_save_draft(self):
        from gateway.operation_parser import parse_append
        op = parse_append("A1", "Drafts", [], 512)
        assert op.description == "Save draft to Drafts (512 bytes)"

    def test_append_to_gmail_drafts_is_save_draft(self):
        from gateway.operation_parser import parse_append
        op = parse_append("A1", "[Gmail]/Drafts", [], 512)
        assert op.description == "Save draft to [Gmail]/Drafts (512 bytes)"

    def test_append_with_draft_flag_is_save_draft(self):
        from gateway.operation_parser import parse_append
        op = parse_append("A1", "Pending", ["\\Draft"], 512)
        assert op.description == "Save draft to Pending (512 bytes)"

    def test_append_to_other_folder_is_add_message(self):
        from gateway.operation_parser import parse_append
        op = parse_append("A1", "Receipts", [], 2048)
        assert op.description == "Add message to Receipts (2048 bytes)"


# ---------------------------------------------------------------------------
# build_rich_description — intent-aware verbs (#1/#2/#3)
# ---------------------------------------------------------------------------


class TestBuildRichDescriptionWithIntent:
    def test_archive_intent_replaces_move_verb_and_drops_destination(self):
        op = parse_move("A1", "42", "[Gmail]/All Mail")
        meta = [{"uid": 42, "sender": "billing@acme.com", "subject": "Invoice #1234"}]
        assert build_rich_description(op, meta, "archive") == (
            'Archive: "Invoice #1234" from billing@acme.com'
        )

    def test_delete_intent_on_move_reads_as_trash(self):
        op = parse_move("A1", "7", "[Gmail]/Trash")
        meta = [{"uid": 7, "sender": "spam@evil.com", "subject": "You won!"}]
        assert build_rich_description(op, meta, "delete") == (
            'Move to Trash: "You won!" from spam@evil.com'
        )

    def test_mark_spam_intent(self):
        op = parse_move("A1", "7", "Junk Email")
        meta = [{"uid": 7, "sender": "spam@evil.com", "subject": "You won!"}]
        assert build_rich_description(op, meta, "mark_spam") == (
            'Mark as spam: "You won!" from spam@evil.com'
        )

    def test_archive_intent_multi_common_sender(self):
        op = parse_move("A1", "1:5", "[Gmail]/All Mail")
        meta = [
            {"uid": 1, "sender": "news@x.com", "subject": "A"},
            {"uid": 2, "sender": "news@x.com", "subject": "B"},
            {"uid": 3, "sender": "news@x.com", "subject": "C"},
        ]
        assert build_rich_description(op, meta, "archive") == (
            "Archive 3 messages from news@x.com"
        )

    def test_intent_fallback_when_no_metadata(self):
        op = parse_move("A1", "1:5", "[Gmail]/All Mail")
        assert build_rich_description(op, [], "archive") == "Archive: 1:5"

    def test_restore_keeps_destination_unless_inbox(self):
        op = parse_move("A1", "42", "Receipts")
        meta = [{"uid": 42, "sender": "shop@x.com", "subject": "Order"}]
        assert build_rich_description(op, meta, "restore_from_trash") == (
            'Restore from Trash: "Order" from shop@x.com to Receipts'
        )

    def test_restore_to_inbox_drops_destination(self):
        op = parse_move("A1", "42", "INBOX")
        meta = [{"uid": 42, "sender": "shop@x.com", "subject": "Order"}]
        assert build_rich_description(op, meta, "restore_from_trash") == (
            'Restore from Trash: "Order" from shop@x.com'
        )

    def test_no_intent_preserves_existing_behaviour(self):
        op = parse_move("A1", "42", "Archive")
        meta = [{"uid": 42, "sender": "billing@acme.com", "subject": "Invoice #1234"}]
        assert build_rich_description(op, meta, None) == (
            'Move: "Invoice #1234" from billing@acme.com to Archive'
        )


# ---------------------------------------------------------------------------
# unquote_mailbox — strip IMAP quoted-string syntax so folder_to/_from hold the
# LOGICAL mailbox name (execution re-quotes via aioimaplib; storing the quoted
# form double-quotes and Dovecot answers [TRYCREATE] Mailbox doesn't exist).
# ---------------------------------------------------------------------------


class TestUnquoteMailbox:
    def test_bare_name_unchanged(self):
        assert unquote_mailbox("Drafts") == "Drafts"

    def test_surrounding_quotes_stripped(self):
        assert unquote_mailbox('"Drafts"') == "Drafts"

    def test_name_with_spaces(self):
        assert unquote_mailbox('"[Gmail]/All Mail"') == "[Gmail]/All Mail"

    def test_inner_escaped_quote_unescaped(self):
        # IMAP quoted-string: \" is a literal double-quote inside the name.
        assert unquote_mailbox('"weird\\"name"') == 'weird"name'

    def test_inner_escaped_backslash_unescaped(self):
        assert unquote_mailbox('"a\\\\b"') == "a\\b"

    def test_empty_quoted_string(self):
        assert unquote_mailbox('""') == ""

    def test_lone_quote_not_a_pair(self):
        # A single leading quote is not a surrounding pair — left as-is.
        assert unquote_mailbox('"Drafts') == '"Drafts'

    def test_unbalanced_trailing_quote(self):
        assert unquote_mailbox('Drafts"') == 'Drafts"'


class TestParsersStoreLogicalMailboxName:
    """Regression: parsers must store the UNQUOTED mailbox name so execution's
    single quoting step is correct (double-quoting caused [TRYCREATE])."""

    def test_append_strips_quotes(self):
        op = parse_append("A2", '"Drafts"', ["\\Draft"], 123)
        assert op.folder_to == "Drafts"

    def test_append_bare_unchanged(self):
        op = parse_append("A2", "Drafts", ["\\Draft"], 123)
        assert op.folder_to == "Drafts"

    def test_move_strips_quotes(self):
        op = parse_move("A1", "5", '"Trash"')
        assert op.folder_to == "Trash"

    def test_move_bare_unchanged(self):
        op = parse_move("A1", "5", "Trash")
        assert op.folder_to == "Trash"

    def test_copy_strips_quotes(self):
        op = parse_copy("A1", "5", '"Archive"')
        assert op.folder_to == "Archive"
