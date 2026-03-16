"""
IMAP write command → structured Operation parser.

Converts raw intercepted IMAP commands into typed Operation records
with human-readable descriptions.

Sub-milestone: 1.1
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedOperation:
    op_type: str
    imap_command: str
    description: str
    message_ids: list[str] = field(default_factory=list)
    folder_from: Optional[str] = None
    folder_to: Optional[str] = None
    flags_add: list[str] = field(default_factory=list)
    flags_remove: list[str] = field(default_factory=list)


def parse_store(tag: str, uid_mode: bool, uid_set: str, flags_op: str, flags: list[str]) -> ParsedOperation:
    """Parse STORE/UID STORE command into a ParsedOperation."""
    # TODO: implement in sub-milestone 1.1
    raise NotImplementedError


def parse_move(tag: str, uid_set: str, destination_folder: str) -> ParsedOperation:
    """Parse MOVE/UID MOVE command into a ParsedOperation."""
    # TODO: implement in sub-milestone 1.1
    raise NotImplementedError


def parse_copy(tag: str, uid_set: str, destination_folder: str) -> ParsedOperation:
    """Parse COPY/UID COPY command into a ParsedOperation."""
    # TODO: implement in sub-milestone 1.1
    raise NotImplementedError


def parse_append(tag: str, folder: str, flags: list[str], message_size: int) -> ParsedOperation:
    """Parse APPEND command into a ParsedOperation."""
    # TODO: implement in sub-milestone 1.1
    raise NotImplementedError


def parse_expunge(tag: str, deleted_uids: list[str]) -> list[ParsedOperation]:
    """
    Parse EXPUNGE into one 'trash' operation per \Deleted-flagged message.
    EXPUNGE is never forwarded upstream.
    """
    # TODO: implement in sub-milestone 1.1
    raise NotImplementedError
