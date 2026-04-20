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


# ---------------------------------------------------------------------------
# Rich description builder — combines ParsedOperation with live message metadata
# ---------------------------------------------------------------------------

# Human-readable verb for each op_type, used in bulk descriptions.
_OP_VERB: dict[str, str] = {
    "move": "Move",
    "copy": "Copy",
    "trash": "Move to Trash",
    "mark_read": "Mark as read",
    "mark_unread": "Mark as unread",
    "flag": "Star",
    "unflag": "Unstar",
    "store": "Update flags on",
    "append": "Save draft",
    "create": "Create folder",
    "rename": "Rename folder",
    "smtp_send": "Send email",
}


def build_rich_description(
    parsed_op: "ParsedOperation",
    metadata: "List[dict]",
) -> str:
    """Build a human-readable description enriched with sender/subject metadata.

    metadata is a list of dicts with keys ``uid``, ``sender``, ``subject``
    (as returned by state_db.get_message_metadata_by_uid_set). Empty list
    means no metadata was available — falls back to the plain UID-based
    description on parsed_op.

    Single message:
      'Move to Trash: "Re: Invoice" from billing@acme.com'
      'Mark as read: "Weekly digest" from noreply@substack.com'
      Falls back to: 'Move to Trash: 42'  (when no metadata)

    Multiple messages — common sender:
      'Archive 5 messages from spam@whoever.com'

    Multiple messages — mixed senders:
      'Archive "Re: Invoice" and 4 more'
      Falls back to: 'Archive 5 messages'  (when no metadata at all)

    APPEND/CREATE/RENAME/SMTP ops are returned unchanged (metadata N/A).
    """
    op_type = parsed_op.op_type

    # Ops that don't involve individual messages — return unchanged
    if op_type in ("append", "create", "rename", "smtp_send"):
        return parsed_op.description

    verb = _OP_VERB.get(op_type, op_type.replace("_", " ").capitalize())

    # Determine how many messages this op targets by inspecting the uid_set
    uid_set_str = parsed_op.message_ids[0] if parsed_op.message_ids else ""
    # A rough count: if it's a range like "1:5", we can't know exactly without
    # the full state DB, so fall back to the metadata count we have.
    is_multi_uid_set = ":" in uid_set_str or "," in uid_set_str

    # --- No metadata available: fall back to UID-based description ---
    if not metadata:
        if is_multi_uid_set:
            # We know it's multiple but don't have count — keep original
            return parsed_op.description
        # Single UID, no metadata
        return parsed_op.description

    total = len(metadata)

    # Build folder suffix for move/copy
    folder_suffix = ""
    if op_type in ("move", "copy") and parsed_op.folder_to:
        folder_suffix = f" to {parsed_op.folder_to}"

    if total == 1:
        # Single message with metadata
        msg = metadata[0]
        sender = msg.get("sender") or ""
        subject = msg.get("subject") or ""
        parts: List[str] = []
        if subject:
            parts.append(f'"{subject}"')
        if sender:
            parts.append(f"from {sender}")
        if parts:
            return f"{verb}: {' '.join(parts)}{folder_suffix}"
        # Metadata row exists but both fields are null
        return parsed_op.description

    # Multiple messages
    senders = [m.get("sender") for m in metadata]
    unique_senders = {s for s in senders if s}  # strip None

    if len(unique_senders) == 1:
        # All from the same sender
        sender = next(iter(unique_senders))
        return f"{verb} {total} messages from {sender}{folder_suffix}"
    else:
        # Mixed senders — show first message subject + overflow count
        first = metadata[0]
        first_subject = first.get("subject") or ""
        remaining = total - 1
        if first_subject:
            return f'{verb} "{first_subject}" and {remaining} more{folder_suffix}'
        else:
            return f"{verb} {total} messages{folder_suffix}"



