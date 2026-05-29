"""
Tests for Sent-folder discovery helpers in gateway/execution.py.

Covers:
  _parse_list_line()      — LIST response line parser
  _discover_sent_folder() — RFC 6154 + name-probe logic (mocked IMAP client)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.execution import _discover_sent_folder, _parse_list_line


# ---------------------------------------------------------------------------
# _parse_list_line
# ---------------------------------------------------------------------------


class TestParseListLine:
    def test_quoted_sent_folder(self) -> None:
        flags, folder = _parse_list_line(b'(\\Sent \\HasNoChildren) "." "Sent"')
        assert "\\sent" in flags
        assert folder == "Sent"

    def test_gmail_sent_mail(self) -> None:
        flags, folder = _parse_list_line(b'(\\Sent \\HasNoChildren) "/" "[Gmail]/Sent Mail"')
        assert "\\sent" in flags
        assert folder == "[Gmail]/Sent Mail"

    def test_icloud_sent_messages(self) -> None:
        flags, folder = _parse_list_line(b'(\\Sent \\HasNoChildren) "." "Sent Messages"')
        assert "\\sent" in flags
        assert folder == "Sent Messages"

    def test_outlook_sent_items(self) -> None:
        flags, folder = _parse_list_line(b'(\\Sent) "." "Sent Items"')
        assert "\\sent" in flags
        assert folder == "Sent Items"

    def test_nil_delimiter(self) -> None:
        flags, folder = _parse_list_line(b'(\\Sent \\HasNoChildren) NIL "Sent"')
        assert "\\sent" in flags
        assert folder == "Sent"

    def test_bare_unquoted_folder(self) -> None:
        flags, folder = _parse_list_line(b"(\\HasNoChildren) \".\" INBOX")
        assert "\\sent" not in flags
        assert folder == "INBOX"

    def test_inbox_no_sent_flag(self) -> None:
        flags, folder = _parse_list_line(b'(\\HasNoChildren) "." "INBOX"')
        assert "\\sent" not in flags
        assert folder == "INBOX"

    def test_multiple_flags(self) -> None:
        flags, folder = _parse_list_line(
            b'(\\HasNoChildren \\Sent \\Subscribed) "." "Sent"'
        )
        assert "\\sent" in flags
        assert "\\hasnochildren" in flags
        assert folder == "Sent"

    def test_empty_flags(self) -> None:
        flags, folder = _parse_list_line(b'() "." "INBOX"')
        assert flags == set()
        assert folder == "INBOX"

    def test_garbled_line_returns_none(self) -> None:
        flags, folder = _parse_list_line(b"* OK this is not a LIST line")
        assert flags == set()
        assert folder is None

    def test_empty_bytes(self) -> None:
        flags, folder = _parse_list_line(b"")
        assert flags == set()
        assert folder is None

    def test_case_insensitive_flag(self) -> None:
        # Some servers emit lowercase flags
        flags, folder = _parse_list_line(b'(\\sent \\hasnochildren) "." "Sent"')
        assert "\\sent" in flags
        assert folder == "Sent"


# ---------------------------------------------------------------------------
# _discover_sent_folder  (mocked IMAP client)
# ---------------------------------------------------------------------------


def _make_client(list_responses: dict[tuple[str, str], tuple[str, list[Any]]]) -> MagicMock:
    """Build a mock aioimaplib client whose list() returns controlled responses."""
    client = MagicMock()

    async def mock_list(ref: str, pattern: str) -> tuple[str, list[Any]]:
        return list_responses.get((ref, pattern), ("OK", [b"OK LIST completed"]))

    client.list = mock_list
    return client


class TestDiscoverSentFolder:
    @pytest.mark.asyncio
    async def test_finds_via_sent_flag(self) -> None:
        client = _make_client({
            ("", "*"): ("OK", [
                b'(\\HasNoChildren) "." "INBOX"',
                b'(\\Sent \\HasNoChildren) "." "Sent"',
                b'(\\Drafts \\HasNoChildren) "." "Drafts"',
                b"OK LIST completed",
            ]),
        })
        result = await _discover_sent_folder(client)
        assert result == "Sent"

    @pytest.mark.asyncio
    async def test_finds_gmail_sent_mail(self) -> None:
        client = _make_client({
            ("", "*"): ("OK", [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\Sent \\HasNoChildren) "/" "[Gmail]/Sent Mail"',
                b"OK LIST completed",
            ]),
        })
        result = await _discover_sent_folder(client)
        assert result == "[Gmail]/Sent Mail"

    @pytest.mark.asyncio
    async def test_falls_back_to_name_probe_sent(self) -> None:
        """Server doesn't set \\Sent flag — probe finds 'Sent' by name."""
        client = _make_client({
            ("", "*"): ("OK", [
                b'(\\HasNoChildren) "." "INBOX"',
                b'(\\HasNoChildren) "." "Sent"',   # no \\Sent flag
                b"OK LIST completed",
            ]),
            ("", "Sent"): ("OK", [
                b'(\\HasNoChildren) "." "Sent"',
                b"OK LIST completed",
            ]),
        })
        result = await _discover_sent_folder(client)
        assert result == "Sent"

    @pytest.mark.asyncio
    async def test_falls_back_to_sent_items(self) -> None:
        """Server uses 'Sent Items' but doesn't flag it."""
        client = _make_client({
            ("", "*"): ("OK", [b"OK LIST completed"]),
            ("", "Sent"): ("OK", [b"OK LIST completed"]),  # doesn't exist
            ("", "Sent Items"): ("OK", [
                b'(\\HasNoChildren) "." "Sent Items"',
                b"OK LIST completed",
            ]),
        })
        result = await _discover_sent_folder(client)
        assert result == "Sent Items"

    @pytest.mark.asyncio
    async def test_returns_none_when_nothing_found(self) -> None:
        """Server is completely uncooperative — return None."""
        client = _make_client({
            ("", "*"): ("OK", [b"OK LIST completed"]),
            ("", "Sent"): ("OK", [b"OK LIST completed"]),
            ("", "Sent Items"): ("OK", [b"OK LIST completed"]),
            ("", "Sent Messages"): ("OK", [b"OK LIST completed"]),
            ("", "INBOX.Sent"): ("OK", [b"OK LIST completed"]),
        })
        result = await _discover_sent_folder(client)
        assert result is None

    @pytest.mark.asyncio
    async def test_first_sent_flag_wins(self) -> None:
        """When multiple folders have \\Sent, return the first one."""
        client = _make_client({
            ("", "*"): ("OK", [
                b'(\\Sent \\HasNoChildren) "." "Sent"',
                b'(\\Sent \\HasNoChildren) "." "Sent Items"',
                b"OK LIST completed",
            ]),
        })
        result = await _discover_sent_folder(client)
        assert result == "Sent"

    @pytest.mark.asyncio
    async def test_list_failure_falls_through_to_probe(self) -> None:
        """If LIST fails (non-OK), skip to name probe."""
        responses = {
            ("", "*"): ("NO", [b"NO Permission denied"]),
            ("", "Sent"): ("OK", [
                b'(\\HasNoChildren) "." "Sent"',
                b"OK LIST completed",
            ]),
        }
        client = _make_client(responses)
        result = await _discover_sent_folder(client)
        assert result == "Sent"
