"""
Unit tests for IMAP command line parser (Milestone 0.2).

Covers: basic commands, UID prefix, FETCH variants, STORE variants, SEARCH,
LIST/LSUB, MOVE/COPY, APPEND literals, LOGIN, AUTHENTICATE, edge cases.

Fixtures derived from real MXrouting session captures.
"""

import pytest
from gateway.imap_parser import parse_line


# ---------------------------------------------------------------------------
# Parametrized happy-path tests
# ---------------------------------------------------------------------------

# Each entry: (line, expected_tag, expected_command, expected_uid, partial_args_check)
# partial_args_check is a callable(args: list[str]) -> bool, or None to skip.

PARSE_CASES: list[tuple] = [
    # ── Basic no-arg commands ─────────────────────────────────────────────────
    (
        "A001 NOOP",
        "A001", "NOOP", False, lambda a: a == [],
    ),
    (
        "A002 CAPABILITY",
        "A002", "CAPABILITY", False, lambda a: a == [],
    ),
    (
        "A003 LOGOUT",
        "A003", "LOGOUT", False, lambda a: a == [],
    ),
    (
        "A004 EXPUNGE",
        "A004", "EXPUNGE", False, lambda a: a == [],
    ),
    # ── SELECT / EXAMINE ─────────────────────────────────────────────────────
    (
        "A005 SELECT INBOX",
        "A005", "SELECT", False, lambda a: a == ["INBOX"],
    ),
    (
        "A006 EXAMINE INBOX",
        "A006", "EXAMINE", False, lambda a: a == ["INBOX"],
    ),
    (
        'A007 SELECT "Sent Items"',
        "A007", "SELECT", False, lambda a: a == ["Sent Items"],
    ),
    # ── FETCH variants ───────────────────────────────────────────────────────
    (
        "A010 FETCH 1 FLAGS",
        "A010", "FETCH", False, lambda a: a[:2] == ["1", "FLAGS"],
    ),
    (
        "A011 FETCH 1:* (FLAGS UID)",
        "A011", "FETCH", False, lambda a: a == ["1:*", "(FLAGS UID)"],
    ),
    (
        "A012 UID FETCH 1:* (FLAGS BODY[HEADER])",
        "A012", "FETCH", True, lambda a: a == ["1:*", "(FLAGS BODY[HEADER])"],
    ),
    (
        "A013 FETCH 1 BODY[HEADER.FIELDS (From Subject Date)]",
        "A013", "FETCH", False,
        lambda a: a == ["1", "BODY[HEADER.FIELDS (From Subject Date)]"],
    ),
    (
        "A014 FETCH 1:5 (RFC822.SIZE FLAGS INTERNALDATE)",
        "A014", "FETCH", False,
        lambda a: a == ["1:5", "(RFC822.SIZE FLAGS INTERNALDATE)"],
    ),
    (
        "A015 UID FETCH 1:* ALL",
        "A015", "FETCH", True, lambda a: a == ["1:*", "ALL"],
    ),
    # ── STORE variants ───────────────────────────────────────────────────────
    (
        r"A020 STORE 1 +FLAGS (\Seen)",
        "A020", "STORE", False, lambda a: a == ["1", "+FLAGS", r"(\Seen)"],
    ),
    (
        r"A021 STORE 1:3 -FLAGS (\Flagged)",
        "A021", "STORE", False, lambda a: a == ["1:3", "-FLAGS", r"(\Flagged)"],
    ),
    (
        r"A022 UID STORE 1 +FLAGS.SILENT (\Deleted)",
        "A022", "STORE", True,
        lambda a: a == ["1", "+FLAGS.SILENT", r"(\Deleted)"],
    ),
    # ── SEARCH variants ──────────────────────────────────────────────────────
    (
        "A030 SEARCH ALL",
        "A030", "SEARCH", False, lambda a: a == ["ALL"],
    ),
    (
        "A031 SEARCH UNSEEN",
        "A031", "SEARCH", False, lambda a: a == ["UNSEEN"],
    ),
    (
        'A032 SEARCH FROM "sender@example.com"',
        "A032", "SEARCH", False, lambda a: a == ["FROM", "sender@example.com"],
    ),
    (
        "A033 UID SEARCH FLAGGED SINCE 1-Jan-2024",
        "A033", "SEARCH", True, lambda a: a[:3] == ["FLAGGED", "SINCE", "1-Jan-2024"],
    ),
    # ── LIST / LSUB ──────────────────────────────────────────────────────────
    (
        'A040 LIST "" "*"',
        "A040", "LIST", False, lambda a: a == ["", "*"],
    ),
    (
        'A041 LIST "" "INBOX"',
        "A041", "LIST", False, lambda a: a == ["", "INBOX"],
    ),
    (
        'A042 LSUB "" "*"',
        "A042", "LSUB", False, lambda a: a == ["", "*"],
    ),
    # ── MOVE / COPY ──────────────────────────────────────────────────────────
    (
        "A050 UID MOVE 1:5 Archive",
        "A050", "MOVE", True, lambda a: a == ["1:5", "Archive"],
    ),
    (
        "A051 UID COPY 3 Sent",
        "A051", "COPY", True, lambda a: a == ["3", "Sent"],
    ),
    (
        "A052 COPY 1,2,3 Trash",
        "A052", "COPY", False, lambda a: a == ["1,2,3", "Trash"],
    ),
    # ── LOGIN ─────────────────────────────────────────────────────────────────
    (
        "A060 LOGIN user@example.com password123",
        "A060", "LOGIN", False,
        lambda a: a == ["user@example.com", "password123"],
    ),
    (
        'A061 LOGIN user@example.com "my quoted password"',
        "A061", "LOGIN", False,
        lambda a: a == ["user@example.com", "my quoted password"],
    ),
    # ── AUTHENTICATE ─────────────────────────────────────────────────────────
    (
        "A070 AUTHENTICATE PLAIN",
        "A070", "AUTHENTICATE", False, lambda a: a == ["PLAIN"],
    ),
    # ── APPEND non-sync literal ───────────────────────────────────────────────
    (
        r"A080 APPEND Drafts (\Seen) {500+}",
        "A080", "APPEND", False,
        lambda a: a == ["Drafts", r"(\Seen)", "{500+}"],
    ),
    # ── STATUS ───────────────────────────────────────────────────────────────
    (
        "A090 STATUS INBOX (MESSAGES UNSEEN)",
        "A090", "STATUS", False,
        lambda a: a == ["INBOX", "(MESSAGES UNSEEN)"],
    ),
    # ── CREATE / RENAME ──────────────────────────────────────────────────────
    (
        "A100 CREATE MyFolder",
        "A100", "CREATE", False, lambda a: a == ["MyFolder"],
    ),
    (
        "A101 RENAME OldName NewName",
        "A101", "RENAME", False, lambda a: a == ["OldName", "NewName"],
    ),
    # ── Edge cases ───────────────────────────────────────────────────────────
    (
        "A110 SELECT ()",  # empty parens
        "A110", "SELECT", False, lambda a: a == ["()"],
    ),
    (
        "A111 FETCH 1 BODY[1.MIME]",  # bracket with numeric section
        "A111", "FETCH", False, lambda a: a == ["1", "BODY[1.MIME]"],
    ),
    (
        'A112 LIST "" INBOX*',  # unquoted wildcard
        "A112", "LIST", False, lambda a: a == ["", "INBOX*"],
    ),
    (
        "* OK still valid with star tag",
        "*", "OK", False, None,
    ),
    (
        "A114 UID EXPUNGE 1:5",  # UID EXPUNGE — uid=True, cmd=EXPUNGE
        "A114", "EXPUNGE", True, lambda a: a == ["1:5"],
    ),
]


@pytest.mark.parametrize("line,exp_tag,exp_cmd,exp_uid,check_args", PARSE_CASES)
def test_parse_line(
    line: str,
    exp_tag: str,
    exp_cmd: str,
    exp_uid: bool,
    check_args,
) -> None:
    result = parse_line(line)
    assert result is not None, f"Expected ParsedCommand, got None for: {line!r}"
    assert result.tag == exp_tag, f"tag mismatch: {result.tag!r} != {exp_tag!r}"
    assert result.command == exp_cmd, f"command mismatch: {result.command!r} != {exp_cmd!r}"
    assert result.uid is exp_uid, f"uid mismatch: {result.uid} != {exp_uid}"
    assert result.raw == line, "raw should preserve the input line"
    if check_args is not None:
        assert check_args(result.args), f"args check failed: {result.args!r} for line: {line!r}"


# ---------------------------------------------------------------------------
# Sync literal → None
# ---------------------------------------------------------------------------

SYNC_LITERAL_CASES = [
    r"A007 APPEND Drafts (\Seen) {500}",
    "A008 APPEND INBOX {1234}",
    r"A009 APPEND Sent (\Answered) {0}",
]


@pytest.mark.parametrize("line", SYNC_LITERAL_CASES)
def test_sync_literal_returns_none(line: str) -> None:
    """Lines ending with {N} (sync literals) must return None."""
    result = parse_line(line)
    assert result is None, f"Expected None for sync literal, got: {result!r}"


# ---------------------------------------------------------------------------
# Non-sync literal → returned normally
# ---------------------------------------------------------------------------

NON_SYNC_LITERAL_CASES = [
    (r"A080 APPEND Drafts (\Seen) {500+}", "{500+}"),
    ("A081 APPEND INBOX {42+}", "{42+}"),
]


@pytest.mark.parametrize("line,expected_literal", NON_SYNC_LITERAL_CASES)
def test_non_sync_literal_returned(line: str, expected_literal: str) -> None:
    """Lines ending with {N+} (non-sync literals) must return a ParsedCommand."""
    result = parse_line(line)
    assert result is not None, f"Expected ParsedCommand for non-sync literal: {line!r}"
    assert result.args[-1] == expected_literal, (
        f"Last arg should be literal token, got: {result.args!r}"
    )


# ---------------------------------------------------------------------------
# UID flag assertions
# ---------------------------------------------------------------------------

UID_TRUE_CASES = [
    "A1 UID FETCH 1:* (FLAGS)",
    "A2 uid SEARCH ALL",
    "A3 UID STORE 1 +FLAGS (\\Seen)",
    "A4 UID MOVE 1:5 Archive",
    "A5 UID COPY 3 Sent",
    "A6 UID EXPUNGE 1:5",
]

UID_FALSE_CASES = [
    "A1 FETCH 1:* (FLAGS)",
    "A2 SEARCH ALL",
    "A3 STORE 1 +FLAGS (\\Seen)",
]


@pytest.mark.parametrize("line", UID_TRUE_CASES)
def test_uid_true(line: str) -> None:
    result = parse_line(line)
    assert result is not None
    assert result.uid is True, f"Expected uid=True for: {line!r}"


@pytest.mark.parametrize("line", UID_FALSE_CASES)
def test_uid_false(line: str) -> None:
    result = parse_line(line)
    assert result is not None
    assert result.uid is False, f"Expected uid=False for: {line!r}"


# ---------------------------------------------------------------------------
# Quoted-string edge cases
# ---------------------------------------------------------------------------

def test_quoted_empty_string() -> None:
    """Empty quoted strings should parse as empty-string args."""
    result = parse_line('A001 LIST "" "*"')
    assert result is not None
    assert result.args == ["", "*"]


def test_quoted_with_spaces() -> None:
    """Quoted strings with spaces must be a single arg."""
    result = parse_line('A002 LOGIN user@x.com "pass word here"')
    assert result is not None
    assert result.args == ["user@x.com", "pass word here"]


def test_backslash_in_quoted() -> None:
    """Backslash escapes inside quoted strings should be handled."""
    result = parse_line('A003 LOGIN user "pass\\"word"')
    assert result is not None
    assert result.args[1] == 'pass"word'


# ---------------------------------------------------------------------------
# Bracket + paren combo (BODY[HEADER.FIELDS (...)]) — tricky case
# ---------------------------------------------------------------------------

def test_body_header_fields_with_parens() -> None:
    """BODY[HEADER.FIELDS (From Subject)] must be a single token."""
    line = "A006 FETCH 1 BODY[HEADER.FIELDS (From Subject)]"
    result = parse_line(line)
    assert result is not None
    assert result.command == "FETCH"
    assert result.args == ["1", "BODY[HEADER.FIELDS (From Subject)]"]


def test_body_peek_header_fields() -> None:
    """BODY.PEEK[HEADER.FIELDS (From Subject Date)] must be a single token."""
    line = "A007 FETCH 1 BODY.PEEK[HEADER.FIELDS (From Subject Date)]"
    result = parse_line(line)
    assert result is not None
    assert result.args == ["1", "BODY.PEEK[HEADER.FIELDS (From Subject Date)]"]


# ---------------------------------------------------------------------------
# Command normalisation
# ---------------------------------------------------------------------------

def test_command_normalised_to_uppercase() -> None:
    """Command must be uppercased regardless of input case."""
    result = parse_line("a001 select INBOX")
    assert result is not None
    assert result.command == "SELECT"


def test_uid_keyword_case_insensitive() -> None:
    """UID keyword should be detected case-insensitively."""
    result = parse_line("a002 uid fetch 1 FLAGS")
    assert result is not None
    assert result.uid is True
    assert result.command == "FETCH"


# ---------------------------------------------------------------------------
# Real MXrouting fixture lines (captured session)
# ---------------------------------------------------------------------------
# Commands sent during a real IMAP session to blizzard.mxrouting.net:993

MXROUTING_FIXTURES = [
    # LIST "" "*"
    ('MX01 LIST "" "*"', "MX01", "LIST", False, ["", "*"]),
    # SELECT INBOX
    ("MX02 SELECT INBOX", "MX02", "SELECT", False, ["INBOX"]),
    # FETCH 1:3 (FLAGS)
    ("MX03 FETCH 1:3 (FLAGS)", "MX03", "FETCH", False, ["1:3", "(FLAGS)"]),
    # FETCH 1 BODY[HEADER]
    ("MX04 FETCH 1 BODY[HEADER]", "MX04", "FETCH", False, ["1", "BODY[HEADER]"]),
    # SEARCH ALL
    ("MX05 SEARCH ALL", "MX05", "SEARCH", False, ["ALL"]),
    # STATUS INBOX (MESSAGES UNSEEN)
    ("MX06 STATUS INBOX (MESSAGES UNSEEN)", "MX06", "STATUS", False, ["INBOX", "(MESSAGES UNSEEN)"]),
    # NOOP
    ("MX07 NOOP", "MX07", "NOOP", False, []),
    # EXAMINE INBOX
    ("MX08 EXAMINE INBOX", "MX08", "EXAMINE", False, ["INBOX"]),
    # LOGOUT
    ("MX09 LOGOUT", "MX09", "LOGOUT", False, []),
]


@pytest.mark.parametrize("line,exp_tag,exp_cmd,exp_uid,exp_args", MXROUTING_FIXTURES)
def test_mxrouting_fixture(line, exp_tag, exp_cmd, exp_uid, exp_args) -> None:
    result = parse_line(line)
    assert result is not None
    assert result.tag == exp_tag
    assert result.command == exp_cmd
    assert result.uid is exp_uid
    assert result.args == exp_args
