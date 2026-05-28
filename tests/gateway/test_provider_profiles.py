"""Tests for gateway/provider_profiles.py — provider detection and normalization helpers."""
from __future__ import annotations

import pytest

from gateway.provider_profiles import (
    GENERIC_PROFILE,
    GMAIL_PROFILE,
    ICLOUD_PROFILE,
    OUTLOOK_PROFILE,
    PendingCopyIntent,
    copy_archive_intent,
    detect_provider,
    should_suppress_append,
)


# ---------------------------------------------------------------------------
# detect_provider
# ---------------------------------------------------------------------------


class TestDetectProvider:
    def test_gmail_imap_host(self) -> None:
        assert detect_provider("imap.gmail.com").name == "Gmail"

    def test_gmail_smtp_host(self) -> None:
        assert detect_provider("smtp.gmail.com").name == "Gmail"

    def test_googlemail(self) -> None:
        assert detect_provider("imap.googlemail.com").name == "Gmail"

    def test_gmail_bare_hostname(self) -> None:
        assert detect_provider("gmail.com").name == "Gmail"

    def test_outlook_office365(self) -> None:
        assert detect_provider("outlook.office365.com").name == "Outlook/Microsoft 365"

    def test_outlook_com(self) -> None:
        assert detect_provider("imap.outlook.com").name == "Outlook/Microsoft 365"

    def test_hotmail(self) -> None:
        assert detect_provider("imap.hotmail.com").name == "Outlook/Microsoft 365"

    def test_live(self) -> None:
        assert detect_provider("imap.live.com").name == "Outlook/Microsoft 365"

    def test_generic_unknown_host(self) -> None:
        assert detect_provider("mail.example.com").name == "Generic IMAP"

    def test_generic_custom_host(self) -> None:
        assert detect_provider("server.mmodahl.com").name == "Generic IMAP"

    def test_icloud_imap_host(self) -> None:
        assert detect_provider("imap.mail.me.com").name == "iCloud Mail"

    def test_icloud_smtp_host(self) -> None:
        assert detect_provider("smtp.mail.me.com").name == "iCloud Mail"

    def test_icloud_mac_com(self) -> None:
        assert detect_provider("imap.mac.com").name == "iCloud Mail"

    def test_case_insensitive(self) -> None:
        assert detect_provider("IMAP.GMAIL.COM").name == "Gmail"

    def test_no_false_positives(self) -> None:
        # 'fakegmail.com' should not match the 'gmail.com' suffix
        result = detect_provider("fakegmail.com")
        assert result.name == "Generic IMAP"

    def test_subdomain_match_not_greedy(self) -> None:
        # 'notgmail.com' is a completely different domain
        result = detect_provider("imap.notgmail.com")
        assert result.name == "Generic IMAP"


# ---------------------------------------------------------------------------
# should_suppress_append
# ---------------------------------------------------------------------------


class TestShouldSuppressAppend:
    def test_gmail_sent_mail_suppressed(self) -> None:
        assert should_suppress_append("[Gmail]/Sent Mail", GMAIL_PROFILE) is True

    def test_gmail_inbox_not_suppressed(self) -> None:
        assert should_suppress_append("INBOX", GMAIL_PROFILE) is False

    def test_gmail_all_mail_not_suppressed(self) -> None:
        assert should_suppress_append("[Gmail]/All Mail", GMAIL_PROFILE) is False

    def test_icloud_sent_messages_suppressed(self) -> None:
        assert should_suppress_append("Sent Messages", ICLOUD_PROFILE) is True

    def test_icloud_inbox_not_suppressed(self) -> None:
        assert should_suppress_append("INBOX", ICLOUD_PROFILE) is False

    def test_generic_sent_not_suppressed(self) -> None:
        # Generic profile does not suppress agent APPENDs to Sent
        assert should_suppress_append("Sent", GENERIC_PROFILE) is False


# ---------------------------------------------------------------------------
# sent_folder — auto-save after SMTP relay
# ---------------------------------------------------------------------------


class TestSentFolder:
    def test_gmail_no_append_needed(self) -> None:
        # Gmail auto-saves on relay; we never APPEND
        assert GMAIL_PROFILE.sent_folder is None

    def test_outlook_no_append_needed(self) -> None:
        # Outlook auto-saves on relay; we never APPEND
        assert OUTLOOK_PROFILE.sent_folder is None

    def test_icloud_appends_to_sent_messages(self) -> None:
        assert ICLOUD_PROFILE.sent_folder == "Sent Messages"

    def test_generic_appends_to_sent(self) -> None:
        assert GENERIC_PROFILE.sent_folder == "Sent"

    def test_detected_icloud_sent_folder(self) -> None:
        profile = detect_provider("imap.mail.me.com")
        assert profile.sent_folder == "Sent Messages"

    def test_detected_gmail_sent_folder_none(self) -> None:
        profile = detect_provider("imap.gmail.com")
        assert profile.sent_folder is None

    def test_detected_generic_sent_folder(self) -> None:
        profile = detect_provider("mail.example.com")
        assert profile.sent_folder == "Sent"

    def test_case_insensitive(self) -> None:
        assert should_suppress_append("[gmail]/sent mail", GMAIL_PROFILE) is True

    def test_outlook_sent_items_suppressed(self) -> None:
        assert should_suppress_append("Sent Items", OUTLOOK_PROFILE) is True

    def test_outlook_sent_suppressed(self) -> None:
        assert should_suppress_append("Sent", OUTLOOK_PROFILE) is True

    def test_outlook_inbox_not_suppressed(self) -> None:
        assert should_suppress_append("INBOX", OUTLOOK_PROFILE) is False

    def test_generic_no_suppression(self) -> None:
        # Generic profile has no suppressed folders
        assert should_suppress_append("[Gmail]/Sent Mail", GENERIC_PROFILE) is False
        assert should_suppress_append("Sent", GENERIC_PROFILE) is False


# ---------------------------------------------------------------------------
# copy_archive_intent
# ---------------------------------------------------------------------------


class TestCopyArchiveIntent:
    def test_gmail_archive_folder(self) -> None:
        result = copy_archive_intent("[Gmail]/All Mail", GMAIL_PROFILE)
        assert result == "[Gmail]/All Mail"

    def test_gmail_trash_folder(self) -> None:
        result = copy_archive_intent("[Gmail]/Trash", GMAIL_PROFILE)
        assert result == "[Gmail]/Trash"

    def test_gmail_user_folder_returns_none(self) -> None:
        assert copy_archive_intent("Work", GMAIL_PROFILE) is None

    def test_gmail_inbox_returns_none(self) -> None:
        assert copy_archive_intent("INBOX", GMAIL_PROFILE) is None

    def test_case_insensitive_archive(self) -> None:
        # The profile's archive_folder is "[Gmail]/All Mail"; match is case-insensitive
        assert copy_archive_intent("[gmail]/all mail", GMAIL_PROFILE) == "[Gmail]/All Mail"

    def test_generic_no_archive_intent(self) -> None:
        # Generic profile has no archive_folder — any COPY is staged normally
        assert copy_archive_intent("[Gmail]/All Mail", GENERIC_PROFILE) is None
        assert copy_archive_intent("Archive", GENERIC_PROFILE) is None

    def test_outlook_trash_recognized(self) -> None:
        assert copy_archive_intent("Deleted Items", OUTLOOK_PROFILE) == "Deleted Items"

    def test_outlook_regular_folder_none(self) -> None:
        assert copy_archive_intent("Work/Projects", OUTLOOK_PROFILE) is None


# ---------------------------------------------------------------------------
# PendingCopyIntent dataclass
# ---------------------------------------------------------------------------


class TestPendingCopyIntent:
    def test_construction(self) -> None:
        intent = PendingCopyIntent(tag="A1", uid_set="123,456", destination="[Gmail]/All Mail")
        assert intent.tag == "A1"
        assert intent.uid_set == "123,456"
        assert intent.destination == "[Gmail]/All Mail"
