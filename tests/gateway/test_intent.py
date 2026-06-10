"""
Unit tests for semantic intent derivation (gateway.intent).
"""

from gateway.intent import classify_folder, derive_intent
from gateway.operation_parser import (
    parse_append,
    parse_copy,
    parse_move,
    parse_store,
)
from gateway.provider_profiles import (
    GENERIC_PROFILE,
    GMAIL_PROFILE,
    ICLOUD_PROFILE,
    OUTLOOK_PROFILE,
)


# ---------------------------------------------------------------------------
# classify_folder
# ---------------------------------------------------------------------------


class TestClassifyFolder:
    def test_inbox_is_always_exact(self):
        assert classify_folder("INBOX") == ("inbox", 1.0)
        assert classify_folder("inbox", GMAIL_PROFILE) == ("inbox", 1.0)

    def test_profile_match_full_confidence(self):
        assert classify_folder("[Gmail]/All Mail", GMAIL_PROFILE) == ("archive", 1.0)
        assert classify_folder("[Gmail]/Trash", GMAIL_PROFILE) == ("trash", 1.0)
        assert classify_folder("[Gmail]/Spam", GMAIL_PROFILE) == ("junk", 1.0)
        assert classify_folder("Deleted Items", OUTLOOK_PROFILE) == ("trash", 1.0)
        assert classify_folder("Junk Email", OUTLOOK_PROFILE) == ("junk", 1.0)
        assert classify_folder("Deleted Messages", ICLOUD_PROFILE) == ("trash", 1.0)

    def test_profile_match_case_insensitive(self):
        assert classify_folder("[gmail]/all mail", GMAIL_PROFILE) == ("archive", 1.0)

    def test_name_heuristic_without_profile(self):
        assert classify_folder("Archive") == ("archive", 0.8)
        assert classify_folder("Trash", GENERIC_PROFILE) == ("trash", 0.8)
        assert classify_folder("Spam") == ("junk", 0.8)
        assert classify_folder("Junk") == ("junk", 0.8)
        assert classify_folder("Drafts") == ("drafts", 0.8)

    def test_name_heuristic_last_path_segment(self):
        # "/"-delimited and "."-delimited namespaces both classify
        assert classify_folder("[Gmail]/Drafts") == ("drafts", 0.8)
        assert classify_folder("INBOX.Trash") == ("trash", 0.8)
        assert classify_folder("INBOX/Archive") == ("archive", 0.8)

    def test_quoted_folder_names(self):
        assert classify_folder('"Deleted Items"', OUTLOOK_PROFILE) == ("trash", 1.0)

    def test_unknown_folder(self):
        assert classify_folder("Receipts") == (None, 0.0)
        assert classify_folder(None) == (None, 0.0)
        assert classify_folder("") == (None, 0.0)


# ---------------------------------------------------------------------------
# derive_intent — moves (#1 archive, #2 delete, #3 spam/restore/unarchive)
# ---------------------------------------------------------------------------


class TestDeriveIntentMove:
    def test_move_to_gmail_all_mail_is_archive(self):
        op = parse_move("A1", "42", "[Gmail]/All Mail")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="INBOX") == ("archive", 1.0)

    def test_move_to_archive_named_folder_is_heuristic_archive(self):
        op = parse_move("A1", "42", "Archive")
        assert derive_intent(op, GENERIC_PROFILE, folder_from="INBOX") == ("archive", 0.8)

    def test_move_to_trash_folder_is_delete(self):
        op = parse_move("A1", "42", "[Gmail]/Trash")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="INBOX") == ("delete", 1.0)

    def test_move_to_outlook_deleted_items_is_delete(self):
        op = parse_move("A1", "42", "Deleted Items")
        assert derive_intent(op, OUTLOOK_PROFILE, folder_from="INBOX") == ("delete", 1.0)

    def test_move_to_junk_is_mark_spam(self):
        op = parse_move("A1", "42", "[Gmail]/Spam")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="INBOX") == ("mark_spam", 1.0)

    def test_move_to_spam_named_folder_heuristic(self):
        op = parse_move("A1", "42", "Spam")
        assert derive_intent(op, None, folder_from="INBOX") == ("mark_spam", 0.8)

    def test_move_from_junk_to_inbox_is_not_spam(self):
        op = parse_move("A1", "42", "INBOX")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="[Gmail]/Spam") == ("not_spam", 1.0)

    def test_move_from_trash_is_restore(self):
        op = parse_move("A1", "42", "INBOX")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="[Gmail]/Trash") == (
            "restore_from_trash",
            1.0,
        )

    def test_move_from_trash_to_user_folder_is_restore(self):
        op = parse_move("A1", "42", "Receipts")
        assert derive_intent(op, OUTLOOK_PROFILE, folder_from="Deleted Items") == (
            "restore_from_trash",
            1.0,
        )

    def test_move_from_archive_to_inbox_is_unarchive(self):
        op = parse_move("A1", "42", "INBOX")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="[Gmail]/All Mail") == (
            "unarchive",
            1.0,
        )

    def test_destination_role_wins_over_source(self):
        # Trash → Archive is an archive, not a restore
        op = parse_move("A1", "42", "[Gmail]/All Mail")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="[Gmail]/Trash") == (
            "archive",
            1.0,
        )

    def test_plain_move_between_user_folders_no_intent(self):
        op = parse_move("A1", "42", "Receipts")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="INBOX") == (None, None)

    def test_heuristic_confidence_is_min_of_both_ends(self):
        # Junk source matched by name (0.8), INBOX destination exact (1.0)
        op = parse_move("A1", "42", "INBOX")
        assert derive_intent(op, None, folder_from="Junk") == ("not_spam", 0.8)


# ---------------------------------------------------------------------------
# derive_intent — store ops
# ---------------------------------------------------------------------------


class TestDeriveIntentStore:
    def test_store_deleted_is_delete(self):
        op = parse_store("A1", uid_mode=True, uid_set="7", flags_op="+FLAGS", flags=["\\Deleted"])
        assert op.op_type == "trash"
        assert derive_intent(op, GMAIL_PROFILE) == ("delete", 1.0)

    def test_store_answered_is_mark_answered(self):
        op = parse_store("A1", uid_mode=True, uid_set="7", flags_op="+FLAGS", flags=["\\Answered"])
        assert op.op_type == "store"
        assert derive_intent(op, None) == ("mark_answered", 1.0)

    def test_mark_read_has_no_extra_intent(self):
        op = parse_store("A1", uid_mode=True, uid_set="7", flags_op="+FLAGS", flags=["\\Seen"])
        assert derive_intent(op, GMAIL_PROFILE) == (None, None)

    def test_generic_store_unknown_flag_no_intent(self):
        op = parse_store("A1", uid_mode=True, uid_set="7", flags_op="+FLAGS", flags=["$Label1"])
        assert derive_intent(op, None) == (None, None)


# ---------------------------------------------------------------------------
# derive_intent — append (#4)
# ---------------------------------------------------------------------------


class TestDeriveIntentAppend:
    def test_append_to_drafts_is_save_draft(self):
        op = parse_append("A1", "Drafts", [], 512)
        assert derive_intent(op, None) == ("save_draft", 0.8)

    def test_append_with_draft_flag_is_save_draft_full_confidence(self):
        op = parse_append("A1", "SomeFolder", ["\\Draft"], 512)
        assert derive_intent(op, None) == ("save_draft", 1.0)

    def test_append_to_other_folder_is_import(self):
        op = parse_append("A1", "Receipts", [], 512)
        assert derive_intent(op, GMAIL_PROFILE) == ("import_message", 1.0)


# ---------------------------------------------------------------------------
# derive_intent — ops with no intent
# ---------------------------------------------------------------------------


class TestDeriveIntentNone:
    def test_copy_has_no_intent(self):
        op = parse_copy("A1", "42", "[Gmail]/All Mail")
        assert derive_intent(op, GMAIL_PROFILE, folder_from="INBOX") == (None, None)
