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


# ---------------------------------------------------------------------------
# SPECIAL-USE (RFC 6154) — server-declared folder roles
# ---------------------------------------------------------------------------


class TestRoleFromListAttributes:
    def test_standard_attributes(self):
        from gateway.intent import role_from_list_attributes

        assert role_from_list_attributes(["\\HasNoChildren", "\\Trash"]) == "trash"
        assert role_from_list_attributes(["\\Archive"]) == "archive"
        assert role_from_list_attributes(["\\Junk"]) == "junk"
        assert role_from_list_attributes(["\\Drafts"]) == "drafts"
        assert role_from_list_attributes(["\\Sent"]) == "sent"

    def test_gmail_all_maps_to_archive(self):
        from gateway.intent import role_from_list_attributes

        assert role_from_list_attributes(["\\HasNoChildren", "\\All"]) == "archive"

    def test_case_insensitive(self):
        from gateway.intent import role_from_list_attributes

        assert role_from_list_attributes(["\\TRASH"]) == "trash"

    def test_no_special_use(self):
        from gateway.intent import role_from_list_attributes

        assert role_from_list_attributes(["\\HasNoChildren", "\\NoSelect"]) is None
        assert role_from_list_attributes([]) is None

    def test_flagged_virtual_mailbox_ignored(self):
        from gateway.intent import role_from_list_attributes

        assert role_from_list_attributes(["\\Flagged"]) is None


class TestClassifyFolderSpecialUse:
    def test_special_use_classifies_localized_names(self):
        # German Trash folder no heuristic would catch
        roles = {"papierkorb": "trash"}
        assert classify_folder("Papierkorb", None, roles) == ("trash", 1.0)

    def test_special_use_full_confidence_without_profile(self):
        roles = {"my archive 2024": "archive"}
        assert classify_folder("My Archive 2024", GENERIC_PROFILE, roles) == ("archive", 1.0)

    def test_special_use_outranks_name_heuristic(self):
        # Server says a folder literally named "Archive" is actually Junk
        roles = {"archive": "junk"}
        assert classify_folder("Archive", None, roles) == ("junk", 1.0)

    def test_inbox_still_wins(self):
        assert classify_folder("INBOX", None, {"inbox": "archive"}) == ("inbox", 1.0)

    def test_no_mapping_falls_back_to_heuristics(self):
        assert classify_folder("Trash", None, {}) == ("trash", 0.8)


class TestSendIntent:
    """derive_send_intent — reply/forward detection for outbound SMTP sends."""

    def test_in_reply_to_is_reply(self):
        from gateway.intent import derive_send_intent

        assert derive_send_intent(
            "Re: Invoice", "<parent@acme.com>", None, original_found=True
        ) == ("reply", 1.0)

    def test_unmatched_reply_lower_confidence(self):
        from gateway.intent import derive_send_intent

        assert derive_send_intent(
            "Re: Invoice", "<parent@acme.com>", None, original_found=False
        ) == ("reply", 0.8)

    def test_references_plus_re_prefix_is_reply(self):
        from gateway.intent import derive_send_intent

        assert derive_send_intent(
            "Re: Hello", None, "<a@x> <b@y>", original_found=True
        ) == ("reply", 1.0)

    def test_references_without_re_prefix_not_reply(self):
        from gateway.intent import derive_send_intent

        # References alone (e.g. a client that threads everything) isn't enough
        assert derive_send_intent("Hello", None, "<a@x>", original_found=False) == (
            None,
            None,
        )

    def test_fwd_prefix_is_forward(self):
        from gateway.intent import derive_send_intent

        assert derive_send_intent("Fwd: Invoice", None, None, original_found=False) == (
            "forward",
            0.8,
        )
        assert derive_send_intent("FW: Invoice", None, "<a@x>", original_found=True) == (
            "forward",
            1.0,
        )

    def test_forward_prefix_wins_over_reply_headers(self):
        from gateway.intent import derive_send_intent

        # Forwarding a reply: subject says Fwd, headers still reference the thread
        assert derive_send_intent(
            "Fwd: Re: Invoice", "<parent@acme.com>", None, original_found=False
        ) == ("forward", 0.8)

    def test_plain_send_no_intent(self):
        from gateway.intent import derive_send_intent

        assert derive_send_intent("Hello there", None, None, original_found=False) == (
            None,
            None,
        )

    def test_resend_subject_not_a_reply(self):
        from gateway.intent import derive_send_intent

        # "Regarding..." must not match the Re: prefix
        assert derive_send_intent("Regarding the invoice", None, None, False) == (
            None,
            None,
        )


class TestSubjectPrefixStripping:
    def test_strips_stacked_prefixes(self):
        from gateway.intent import strip_subject_prefixes

        assert strip_subject_prefixes("Re: Fwd: Re: Invoice #1234") == "Invoice #1234"

    def test_strips_counter_form(self):
        from gateway.intent import strip_subject_prefixes

        assert strip_subject_prefixes("Re[2]: Invoice") == "Invoice"

    def test_plain_subject_unchanged(self):
        from gateway.intent import strip_subject_prefixes

        assert strip_subject_prefixes("Regarding the invoice") == "Regarding the invoice"
        assert strip_subject_prefixes(None) == ""


class TestExtractMessageIds:
    def test_multiple_ids(self):
        from gateway.intent import extract_message_ids

        assert extract_message_ids("<a@x> <b@y>") == ["<a@x>", "<b@y>"]

    def test_none_and_garbage(self):
        from gateway.intent import extract_message_ids

        assert extract_message_ids(None) == []
        assert extract_message_ids("not a message id") == []


class TestDeriveIntentSpecialUse:
    def test_move_to_localized_trash_is_delete(self):
        roles = {"papierkorb": "trash"}
        op = parse_move("A1", "42", "Papierkorb")
        assert derive_intent(op, None, folder_from="INBOX", special_use=roles) == (
            "delete",
            1.0,
        )

    def test_move_from_localized_junk_to_inbox_is_not_spam(self):
        roles = {"spamverdacht": "junk"}
        op = parse_move("A1", "42", "INBOX")
        assert derive_intent(op, None, folder_from="Spamverdacht", special_use=roles) == (
            "not_spam",
            1.0,
        )

    def test_append_to_special_use_drafts(self):
        roles = {"entwürfe": "drafts"}
        op = parse_append("A1", "Entwürfe", [], 512)
        assert derive_intent(op, None, special_use=roles) == ("save_draft", 1.0)

    def test_move_to_special_use_sent_has_no_intent(self):
        # 'sent' is stored but carries no move semantics
        roles = {"gesendet": "sent"}
        op = parse_move("A1", "42", "Gesendet")
        assert derive_intent(op, None, folder_from="INBOX", special_use=roles) == (
            None,
            None,
        )
