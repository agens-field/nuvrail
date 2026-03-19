"""
Unit tests for imap_response_parser (Milestone 0.4).

Tests are purely in-process — no DB, no network.
"""
from __future__ import annotations

from gateway.imap_response_parser import (
    parse_fetch_line,
    parse_list_response,
    parse_select_response,
)


# ---------------------------------------------------------------------------
# parse_select_response
# ---------------------------------------------------------------------------


def test_parse_select_exists() -> None:
    """'* 47 EXISTS' → exists=47."""
    info = parse_select_response(["* 47 EXISTS"])
    assert info.exists == 47


def test_parse_select_uidvalidity() -> None:
    """'* OK [UIDVALIDITY 12345]' → uidvalidity=12345."""
    info = parse_select_response(["* OK [UIDVALIDITY 12345]"])
    assert info.uidvalidity == 12345


def test_parse_select_uidnext() -> None:
    """'* OK [UIDNEXT 48]' → uidnext=48."""
    info = parse_select_response(["* OK [UIDNEXT 48]"])
    assert info.uidnext == 48


def test_parse_select_full() -> None:
    """All fields present are parsed correctly."""
    lines = [
        "* 47 EXISTS",
        "* 2 RECENT",
        "* OK [UIDVALIDITY 1234567890]",
        "* OK [UIDNEXT 48]",
        "* OK [UNSEEN 5] Message 5 is first unseen.",
    ]
    info = parse_select_response(lines)
    assert info.exists == 47
    assert info.recent == 2
    assert info.uidvalidity == 1234567890
    assert info.uidnext == 48
    assert info.unseen == 5


def test_parse_select_ignores_unrelated_lines() -> None:
    """Unrecognised lines are silently skipped; known fields still parsed."""
    lines = [
        "* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)",
        "* 47 EXISTS",
        "* OK [PERMANENTFLAGS (\\Seen \\Answered \\*)]",
    ]
    info = parse_select_response(lines)
    assert info.exists == 47
    assert info.recent is None


# ---------------------------------------------------------------------------
# parse_list_response
# ---------------------------------------------------------------------------


def test_parse_list_simple() -> None:
    """INBOX and Sent are parsed correctly."""
    lines = [
        '* LIST (\\HasNoChildren) "/" "INBOX"',
        '* LIST (\\HasNoChildren) "/" "Sent"',
    ]
    folders = parse_list_response(lines)
    assert folders == ["INBOX", "Sent"]


def test_parse_list_quoted_with_space() -> None:
    """Folder names with spaces (quoted) are preserved."""
    lines = ['* LIST (\\HasNoChildren) "/" "My Folder"']
    folders = parse_list_response(lines)
    assert folders == ["My Folder"]


def test_parse_list_unquoted() -> None:
    """Unquoted folder name is returned as-is."""
    lines = ['* LIST (\\HasNoChildren) "/" INBOX']
    folders = parse_list_response(lines)
    assert folders == ["INBOX"]


def test_parse_list_skips_non_list_lines() -> None:
    """Non-LIST lines produce no output."""
    lines = ["* 47 EXISTS", "A001 OK LIST completed"]
    folders = parse_list_response(lines)
    assert folders == []


# ---------------------------------------------------------------------------
# parse_fetch_line
# ---------------------------------------------------------------------------


def test_parse_fetch_flags_only() -> None:
    """FLAGS (\\Seen) is parsed."""
    info = parse_fetch_line("* 1 FETCH (FLAGS (\\Seen))")
    assert info is not None
    assert info.flags == ["\\Seen"]


def test_parse_fetch_uid_and_flags() -> None:
    """UID 42 + FLAGS are both extracted."""
    info = parse_fetch_line("* 1 FETCH (UID 42 FLAGS (\\Seen))")
    assert info is not None
    assert info.uid == 42
    assert "\\Seen" in info.flags


def test_parse_fetch_empty_flags() -> None:
    """FLAGS () → empty list."""
    info = parse_fetch_line("* 12 FETCH (UID 53 FLAGS ())")
    assert info is not None
    assert info.flags == []


def test_parse_fetch_seq_num() -> None:
    """seq_num is extracted from '* 5 FETCH'."""
    info = parse_fetch_line("* 5 FETCH (UID 47 FLAGS (\\Seen \\Answered))")
    assert info is not None
    assert info.seq_num == 5


def test_parse_fetch_non_fetch_line_returns_none() -> None:
    """Non-FETCH line '* 47 EXISTS' returns None."""
    result = parse_fetch_line("* 47 EXISTS")
    assert result is None


def test_parse_fetch_size() -> None:
    """RFC822.SIZE is parsed into info.size."""
    info = parse_fetch_line("* 5 FETCH (UID 47 FLAGS (\\Seen) RFC822.SIZE 2048)")
    assert info is not None
    assert info.size == 2048


def test_parse_fetch_multiple_flags() -> None:
    """Multiple flags are all captured."""
    info = parse_fetch_line("* 3 FETCH (UID 99 FLAGS (\\Seen \\Answered \\Flagged))")
    assert info is not None
    assert set(info.flags) == {"\\Seen", "\\Answered", "\\Flagged"}


def test_parse_fetch_returns_none_for_unmatched() -> None:
    """A line with {N} literal indicator is skipped (returns None or has no payload)."""
    result = parse_fetch_line("* 3 FETCH (BODY[] {1234}")
    # The regex won't match because the outer parens are unbalanced/incomplete
    # so it should return None
    assert result is None


def test_parse_fetch_envelope_subject_and_sender() -> None:
    """ENVELOPE subject and sender are parsed when present."""
    line = (
        '* 1 FETCH (UID 42 FLAGS (\\Seen) '
        'ENVELOPE ("Mon, 1 Jan 2024 12:00:00 +0000" "Hello there" '
        '(("Alice Smith" NIL "alice" "example.com")) '
        '(("Alice Smith" NIL "alice" "example.com")) NIL NIL NIL NIL NIL NIL))'
    )
    info = parse_fetch_line(line)
    assert info is not None
    assert info.uid == 42
    assert info.subject == "Hello there"
    assert info.date_str == "Mon, 1 Jan 2024 12:00:00 +0000"
    assert info.sender is not None
    assert "alice@example.com" in info.sender
