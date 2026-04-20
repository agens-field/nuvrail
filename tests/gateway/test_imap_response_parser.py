"""
Unit tests for imap_response_parser (Milestone 0.4).

Tests are purely in-process — no DB, no network.
"""
from __future__ import annotations

from gateway.imap_response_parser import (
    _RE_FETCH_LITERAL,
    extract_headers_from_rfc822,
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


# ---------------------------------------------------------------------------
# _RE_FETCH_LITERAL — regex for detecting RFC822 literal FETCH lines
# ---------------------------------------------------------------------------


def test_fetch_literal_regex_matches_rfc822() -> None:
    """Standard UID FETCH RFC822 response with literal size."""
    line = "* 5 FETCH (UID 386 RFC822 {12345})"
    m = _RE_FETCH_LITERAL.match(line)
    assert m is not None
    assert int(m.group(1)) == 5    # seq_num
    assert int(m.group(2)) == 386  # uid
    assert int(m.group(3)) == 12345  # literal size


def test_fetch_literal_regex_matches_body_bracket() -> None:
    """BODY[] variant also triggers the literal path."""
    line = "* 3 FETCH (UID 99 BODY[] {8192})"
    m = _RE_FETCH_LITERAL.match(line)
    assert m is not None
    assert int(m.group(2)) == 99
    assert int(m.group(3)) == 8192


def test_fetch_literal_regex_matches_with_flags() -> None:
    """UID and FLAGS can appear together before RFC822."""
    line = "* 2 FETCH (UID 42 FLAGS (\\Seen) RFC822 {500})"
    m = _RE_FETCH_LITERAL.match(line)
    assert m is not None
    assert int(m.group(2)) == 42


def test_fetch_literal_regex_no_match_without_uid() -> None:
    """Lines without UID in the FETCH payload don't match."""
    line = "* 5 FETCH (RFC822 {12345})"
    m = _RE_FETCH_LITERAL.match(line)
    assert m is None


def test_fetch_literal_regex_no_match_envelope_fetch() -> None:
    """Normal ENVELOPE fetch (no literal) doesn't match."""
    line = '* 1 FETCH (UID 42 FLAGS (\\Seen) ENVELOPE ("date" "subject" NIL NIL NIL NIL NIL NIL NIL NIL))'
    m = _RE_FETCH_LITERAL.match(line)
    assert m is None


# ---------------------------------------------------------------------------
# extract_headers_from_rfc822
# ---------------------------------------------------------------------------


def _make_rfc822(from_: str, subject: str, body: str = "Hello") -> bytes:
    """Build a minimal RFC822 message as bytes."""
    return (
        f"From: {from_}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Mon, 1 Jan 2024 12:00:00 +0000\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode()


def test_extract_headers_plain() -> None:
    """Plain ASCII From/Subject are returned as-is."""
    data = _make_rfc822("Alice <alice@example.com>", "Hello there")
    sender, subject = extract_headers_from_rfc822(data)
    assert sender == "Alice <alice@example.com>"
    assert subject == "Hello there"


def test_extract_headers_encoded_subject() -> None:
    """RFC2047 encoded-word subjects are decoded."""
    # =?UTF-8?Q?Re=3A_Invoice?= decodes to "Re: Invoice"
    data = _make_rfc822("billing@acme.com", "=?UTF-8?Q?Re=3A_Invoice?=")
    sender, subject = extract_headers_from_rfc822(data)
    assert subject == "Re: Invoice"
    assert sender == "billing@acme.com"


def test_extract_headers_encoded_sender() -> None:
    """RFC2047 encoded-word display names in From are decoded."""
    data = _make_rfc822("=?UTF-8?Q?Alice_Smith?= <alice@example.com>", "Hi")
    sender, subject = extract_headers_from_rfc822(data)
    assert sender is not None
    assert "alice@example.com" in sender


def test_extract_headers_missing_fields() -> None:
    """Missing From/Subject returns None for each missing field."""
    data = b"Date: Mon, 1 Jan 2024 12:00:00 +0000\r\n\r\nBody"
    sender, subject = extract_headers_from_rfc822(data)
    assert sender is None
    assert subject is None


def test_extract_headers_truncated_no_blank_line() -> None:
    """Truncated input without blank line is handled gracefully."""
    # Simulate only reading first N bytes of a large message
    data = b"From: alice@example.com\r\nSubject: Test\r\n"
    sender, subject = extract_headers_from_rfc822(data)
    assert sender == "alice@example.com"
    assert subject == "Test"


def test_extract_headers_empty_bytes() -> None:
    """Empty input returns (None, None) without raising."""
    sender, subject = extract_headers_from_rfc822(b"")
    assert sender is None
    assert subject is None


def test_extract_headers_multiline_subject() -> None:
    """Folded (multi-line) Subject header is unfolded correctly."""
    data = (
        b"From: alice@example.com\r\n"
        b"Subject: This is a very long subject\r\n"
        b" that wraps onto the next line\r\n"
        b"\r\n"
        b"Body"
    )
    sender, subject = extract_headers_from_rfc822(data)
    assert subject is not None
    assert "long subject" in subject
    assert "wraps" in subject
