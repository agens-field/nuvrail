"""
Unit tests for IMAP command classifier (Milestone 0.2).

Covers: every READ, WRITE, BLOCKED command; fallthrough for AUTHENTICATE/LOGIN
and unknown commands; UID prefix does not affect classification.
"""

import pytest
from gateway.command_router import (
    BLOCKED_COMMANDS,
    READ_COMMANDS,
    WRITE_COMMANDS,
    classify,
)
from gateway.imap_parser import ParsedCommand


def _cmd(command: str, uid: bool = False) -> ParsedCommand:
    """Helper: build a minimal ParsedCommand for classification testing."""
    return ParsedCommand(tag="A001", command=command, uid=uid, args=[], raw="")


# ---------------------------------------------------------------------------
# All READ_COMMANDS → "read"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", sorted(READ_COMMANDS))
def test_read_commands(command: str) -> None:
    assert classify(_cmd(command)) == "read", f"Expected 'read' for {command!r}"


# ---------------------------------------------------------------------------
# All WRITE_COMMANDS → "write"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", sorted(WRITE_COMMANDS))
def test_write_commands(command: str) -> None:
    assert classify(_cmd(command)) == "write", f"Expected 'write' for {command!r}"


# ---------------------------------------------------------------------------
# All BLOCKED_COMMANDS → "blocked"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", sorted(BLOCKED_COMMANDS))
def test_blocked_commands(command: str) -> None:
    assert classify(_cmd(command)) == "blocked", f"Expected 'blocked' for {command!r}"


# ---------------------------------------------------------------------------
# AUTHENTICATE and LOGIN → "read" (fallthrough — they are connection setup)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["AUTHENTICATE", "LOGIN"])
def test_auth_commands_fall_through_to_read(command: str) -> None:
    """AUTH commands are not in any set; they must fall through to 'read'."""
    assert classify(_cmd(command)) == "read", (
        f"Expected 'read' (fallthrough) for {command!r}"
    )


# ---------------------------------------------------------------------------
# Unknown commands → "read"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "XALERT",           # fictional extension
    "UNKNOWN_CMD",      # unknown
    "COMPRESS",         # RFC 4978 — not in current sets
    "GETQUOTA",         # RFC 2087 — not in current sets
    "SETQUOTA",         # RFC 2087 — write-ish but not in current sets
    "",                 # malformed / empty
])
def test_unknown_commands_default_to_read(command: str) -> None:
    """Unknown commands must pass through rather than crash the proxy."""
    assert classify(_cmd(command)) == "read", (
        f"Expected 'read' (fallthrough) for unknown command {command!r}"
    )


# ---------------------------------------------------------------------------
# UID prefix does not change classification
# ---------------------------------------------------------------------------

UID_CLASSIFICATION_CASES: list[tuple[str, str]] = [
    # (command, expected_classification)
    ("FETCH", "read"),
    ("SEARCH", "read"),
    ("STORE", "write"),
    ("MOVE", "write"),
    ("COPY", "write"),
    ("EXPUNGE", "blocked"),
]


@pytest.mark.parametrize("command,expected", UID_CLASSIFICATION_CASES)
def test_uid_prefix_does_not_change_classification(command: str, expected: str) -> None:
    """UID FETCH is still 'read', UID STORE is still 'write', etc."""
    result = classify(_cmd(command, uid=True))
    assert result == expected, (
        f"UID {command}: expected {expected!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Explicit spot-checks for a few important commands
# ---------------------------------------------------------------------------

def test_select_is_read() -> None:
    assert classify(_cmd("SELECT")) == "read"


def test_examine_is_read() -> None:
    assert classify(_cmd("EXAMINE")) == "read"


def test_logout_is_read() -> None:
    assert classify(_cmd("LOGOUT")) == "read"


def test_store_is_write() -> None:
    assert classify(_cmd("STORE")) == "write"


def test_append_is_write() -> None:
    assert classify(_cmd("APPEND")) == "write"


def test_expunge_is_blocked() -> None:
    assert classify(_cmd("EXPUNGE")) == "blocked"


def test_delete_is_blocked() -> None:
    assert classify(_cmd("DELETE")) == "blocked"
