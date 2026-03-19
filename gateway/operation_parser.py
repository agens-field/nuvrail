"""
IMAP write command → structured Operation parser.

Converts raw intercepted IMAP commands into typed Operation records
with human-readable descriptions.

Sub-milestone: 1.0
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ParsedOperation:
    tag: str
    op_type: str
    imap_command: str
    description: str
    message_ids: List[str] = field(default_factory=list)
    folder_from: Optional[str] = None
    folder_to: Optional[str] = None
    flags_add: List[str] = field(default_factory=list)
    flags_remove: List[str] = field(default_factory=list)


def parse_store(
    tag: str, uid_mode: bool, uid_set: str, flags_op: str, flags: List[str]
) -> ParsedOperation:
    """Parse STORE/UID STORE command into a ParsedOperation.

    Flag operation dispatch:
      +FLAGS \\Deleted  → op_type='trash'
      +FLAGS \\Seen     → op_type='mark_read'
      +FLAGS \\Flagged  → op_type='flag'
      -FLAGS \\Flagged  → op_type='unflag'
      -FLAGS \\Seen     → op_type='mark_unread'
      anything else    → op_type='store'
    """
    # Normalise: strip backslashes for comparison, lower-case
    normalised_flags = [f.lstrip("\\").lower() for f in flags]
    op = flags_op.upper().replace(".SILENT", "")  # handle +FLAGS.SILENT etc.
    cmd_str = f"{'UID ' if uid_mode else ''}STORE {uid_set} {flags_op} ({' '.join(flags)})"
    msg_ids = [uid_set]

    if op == "+FLAGS":
        if "deleted" in normalised_flags:
            return ParsedOperation(
                tag=tag,
                op_type="trash",
                imap_command=cmd_str,
                description=f"Move to Trash: {uid_set}",
                message_ids=msg_ids,
                flags_add=flags,
            )
        if "seen" in normalised_flags:
            return ParsedOperation(
                tag=tag,
                op_type="mark_read",
                imap_command=cmd_str,
                description=f"Mark as read: {uid_set}",
                message_ids=msg_ids,
                flags_add=flags,
            )
        if "flagged" in normalised_flags:
            return ParsedOperation(
                tag=tag,
                op_type="flag",
                imap_command=cmd_str,
                description=f"Star: {uid_set}",
                message_ids=msg_ids,
                flags_add=flags,
            )
    elif op == "-FLAGS":
        if "flagged" in normalised_flags:
            return ParsedOperation(
                tag=tag,
                op_type="unflag",
                imap_command=cmd_str,
                description=f"Unstar: {uid_set}",
                message_ids=msg_ids,
                flags_remove=flags,
            )
        if "seen" in normalised_flags:
            return ParsedOperation(
                tag=tag,
                op_type="mark_unread",
                imap_command=cmd_str,
                description=f"Mark as unread: {uid_set}",
                message_ids=msg_ids,
                flags_remove=flags,
            )

    # Generic fallback
    return ParsedOperation(
        tag=tag,
        op_type="store",
        imap_command=cmd_str,
        description=f"Flag update on {uid_set}: {flags_op} {flags}",
        message_ids=msg_ids,
        flags_add=flags if op == "+FLAGS" else [],
        flags_remove=flags if op == "-FLAGS" else [],
    )


def parse_move(tag: str, uid_set: str, destination_folder: str) -> ParsedOperation:
    """Parse MOVE/UID MOVE command into a ParsedOperation."""
    cmd_str = f"MOVE {uid_set} {destination_folder}"
    return ParsedOperation(
        tag=tag,
        op_type="move",
        imap_command=cmd_str,
        description=f"Move {uid_set} to {destination_folder}",
        message_ids=[uid_set],
        folder_to=destination_folder,
    )


def parse_copy(tag: str, uid_set: str, destination_folder: str) -> ParsedOperation:
    """Parse COPY/UID COPY command into a ParsedOperation."""
    cmd_str = f"COPY {uid_set} {destination_folder}"
    return ParsedOperation(
        tag=tag,
        op_type="copy",
        imap_command=cmd_str,
        description=f"Copy {uid_set} to {destination_folder}",
        message_ids=[uid_set],
        folder_to=destination_folder,
    )


def parse_append(tag: str, folder: str, flags: List[str], message_size: int) -> ParsedOperation:
    """Parse APPEND command into a ParsedOperation."""
    cmd_str = f"APPEND {folder} ({' '.join(flags)}) {{{message_size}}}"
    return ParsedOperation(
        tag=tag,
        op_type="append",
        imap_command=cmd_str,
        description=f"Save draft to {folder} ({message_size} bytes)",
        folder_to=folder,
        flags_add=flags,
    )


def parse_expunge(tag: str, deleted_uids: List[str]) -> List[ParsedOperation]:
    """Parse EXPUNGE into one 'trash' operation per \\Deleted-flagged message.

    EXPUNGE is never forwarded upstream — each deleted UID becomes a staged
    'trash' operation.
    """
    cmd_str = "EXPUNGE"
    return [
        ParsedOperation(
            tag=tag,
            op_type="trash",
            imap_command=cmd_str,
            description=f"Move to Trash: {uid}",
            message_ids=[uid],
        )
        for uid in deleted_uids
    ]
