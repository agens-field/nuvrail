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


# ---------------------------------------------------------------------------
# Regression: CLOSE must be BLOCKED, not forwarded (covert-EXPUNGE guard)
# ---------------------------------------------------------------------------
#
# RFC 3501 §6.4.2: CLOSE permanently expunges every \Deleted message in the
# selected mailbox, THEN returns to the authenticated state. If CLOSE is
# forwarded upstream it silently expunges mail the human's other clients
# flagged \Deleted — a covert EXPUNGE path that breaks the core invariant
# "EXPUNGE never reaches upstream." It must classify as 'blocked' so the proxy
# answers OK locally and never forwards it.

def test_close_is_blocked() -> None:
    """CLOSE side-effect-expunges (RFC 3501) — it must never be forwarded."""
    assert classify(_cmd("CLOSE")) == "blocked", (
        "CLOSE expunges \\Deleted messages upstream per RFC 3501 §6.4.2; "
        "classifying it as anything but 'blocked' is a covert EXPUNGE path."
    )


def test_close_is_in_blocked_set() -> None:
    """Guard the enumeration itself, not just the classify() result."""
    assert "CLOSE" in BLOCKED_COMMANDS


def test_uid_close_is_blocked() -> None:
    """UID prefix must not let CLOSE slip through to 'read'."""
    assert classify(_cmd("CLOSE", uid=True)) == "blocked"


# ---------------------------------------------------------------------------
# UNSELECT (RFC 3691) is the SAFE way to leave a mailbox — no expunge → read
# ---------------------------------------------------------------------------

def test_unselect_is_read() -> None:
    """UNSELECT closes the mailbox WITHOUT expunging (RFC 3691) — safe to forward."""
    assert classify(_cmd("UNSELECT")) == "read"


# ---------------------------------------------------------------------------
# Invariant guard: no expunge-capable verb may be classified 'read'
# ---------------------------------------------------------------------------
#
# Any RFC IMAP command that can expunge \Deleted mail as a side effect must be
# in BLOCKED_COMMANDS. This test is the backstop against a future edit
# accidentally moving one of these into READ_COMMANDS.

@pytest.mark.parametrize("command", ["EXPUNGE", "CLOSE"])
def test_expunge_capable_commands_never_read(command: str) -> None:
    assert command not in READ_COMMANDS
    assert command not in WRITE_COMMANDS
    assert classify(_cmd(command)) == "blocked", (
        f"{command} can expunge upstream and must be 'blocked', not forwarded."
    )
