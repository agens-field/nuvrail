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
    # Compound verb phrase for rich descriptions (e.g. "Mark as read and star").
    # None → build_rich_description falls back to the per-op_type verb.
    verb: Optional[str] = None


# Recognised flag semantics for STORE: normalised flag →
# ((op_type, verb) when adding, (op_type, verb) when removing).
# \Answered has no dedicated op_type — it stays a generic 'store' (executed as
# a plain flag replay) but gets a human verb and a mark_answered intent.
# Listed in display-priority order: the first recognised flag decides op_type
# when several are set in one command.
_STORE_FLAG_SEMANTICS: list[tuple[str, tuple[str, str], tuple[str, str]]] = [
    ("seen", ("mark_read", "Mark as read"), ("mark_unread", "Mark as unread")),
    ("flagged", ("flag", "Star"), ("unflag", "Unstar")),
    ("answered", ("store", "Mark as replied"), ("store", "Unmark as replied")),
]


def parse_store(
    tag: str, uid_mode: bool, uid_set: str, flags_op: str, flags: List[str]
) -> ParsedOperation:
    """Parse STORE/UID STORE command into a ParsedOperation.

    Flag operation dispatch:
      +FLAGS \\Deleted  → op_type='trash' (dominates any other flags present)
      +FLAGS \\Seen     → op_type='mark_read'
      +FLAGS \\Flagged  → op_type='flag'
      -FLAGS \\Flagged  → op_type='unflag'
      -FLAGS \\Seen     → op_type='mark_unread'
      anything else    → op_type='store'

    Compound commands keep the first recognised flag's op_type but describe
    every recognised flag: +FLAGS (\\Seen \\Flagged) → "Mark as read and star".
    """
    # Normalise: strip backslashes for comparison, lower-case
    normalised_flags = [f.lstrip("\\").lower() for f in flags]
    op = flags_op.upper().replace(".SILENT", "")  # handle +FLAGS.SILENT etc.
    cmd_str = f"{'UID ' if uid_mode else ''}STORE {uid_set} {flags_op} ({' '.join(flags)})"
    msg_ids = [uid_set]

    if op == "+FLAGS" and "deleted" in normalised_flags:
        return ParsedOperation(
            tag=tag,
            op_type="trash",
            imap_command=cmd_str,
            description=f"Move to Trash: {uid_set}",
            message_ids=msg_ids,
            flags_add=flags,
        )

    if op in ("+FLAGS", "-FLAGS"):
        adding = op == "+FLAGS"
        recognised = [
            (op_type, verb)
            for flag_name, add_sem, remove_sem in _STORE_FLAG_SEMANTICS
            if flag_name in normalised_flags
            for op_type, verb in [add_sem if adding else remove_sem]
        ]
        if recognised:
            op_type = recognised[0][0]
            # "Mark as read and star": first verb as-is, the rest lower-cased.
            verbs = [recognised[0][1]] + [v.lower() for _, v in recognised[1:]]
            verb_phrase = " and ".join(verbs)
            return ParsedOperation(
                tag=tag,
                op_type=op_type,
                imap_command=cmd_str,
                description=f"{verb_phrase}: {uid_set}",
                message_ids=msg_ids,
                flags_add=flags if adding else [],
                flags_remove=[] if adding else flags,
                verb=verb_phrase,
            )

    # Generic fallback — unrecognised flags or a FLAGS (replace) operation
    return ParsedOperation(
        tag=tag,
        op_type="store",
        imap_command=cmd_str,
        description=f"Flag update on {uid_set}: {flags_op} ({' '.join(flags)})",
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


def _is_drafts_append(folder: str, flags: List[str]) -> bool:
    """True if an APPEND targets a drafts folder or carries the \\Draft flag.

    Name check covers "/" and "." namespaces ("[Gmail]/Drafts", "INBOX.Drafts").
    gateway.intent classifies more thoroughly (provider profiles); this only
    decides the parse-time wording.
    """
    if any(f.lstrip("\\").lower() == "draft" for f in flags):
        return True
    name = folder.strip('"').lower()
    return name.rsplit("/", 1)[-1].rsplit(".", 1)[-1] in ("drafts", "draft")


def parse_append(tag: str, folder: str, flags: List[str], message_size: int) -> ParsedOperation:
    """Parse APPEND command into a ParsedOperation.

    Only an APPEND into a drafts folder (or with the \\Draft flag) is a draft
    save; an APPEND anywhere else adds a new message to that folder and is
    described as such.
    """
    cmd_str = f"APPEND {folder} ({' '.join(flags)}) {{{message_size}}}"
    if _is_drafts_append(folder, flags):
        description = f"Save draft to {folder} ({message_size} bytes)"
    else:
        description = f"Add message to {folder} ({message_size} bytes)"
    return ParsedOperation(
        tag=tag,
        op_type="append",
        imap_command=cmd_str,
        description=description,
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

# Human-readable verb for each intent label (see gateway.intent). When an
# intent is known it wins over the mechanical op_type verb: a MOVE to
# [Gmail]/All Mail reads "Archive ...", not "Move ... to [Gmail]/All Mail".
INTENT_VERBS: dict[str, str] = {
    "archive": "Archive",
    "delete": "Move to Trash",
    "mark_spam": "Mark as spam",
    "not_spam": "Not spam — move to Inbox",
    "restore_from_trash": "Restore from Trash",
    "unarchive": "Move back to Inbox",
    "mark_answered": "Mark as replied",
    "save_draft": "Save draft",
    "import_message": "Add message",
}

# Intents whose verb already names the destination — the " to X" suffix would
# only repeat provider internals the user shouldn't need to read.
_INTENT_IMPLIES_DEST = {"archive", "delete", "mark_spam", "not_spam", "unarchive"}


def build_rich_description(
    parsed_op: "ParsedOperation",
    metadata: "List[dict]",
    intent_label: "Optional[str]" = None,
) -> str:
    """Build a human-readable description enriched with sender/subject metadata.

    metadata is a list of dicts with keys ``uid``, ``sender``, ``subject``
    (as returned by state_db.get_message_metadata_by_uid_set). Empty list
    means no metadata was available — falls back to the plain UID-based
    description on parsed_op (or an intent-verb description when an intent
    is known).

    intent_label (see gateway.intent) selects the verb when set: a MOVE to
    the provider's archive folder reads 'Archive: "Re: Invoice" ...' instead
    of 'Move: "Re: Invoice" ... to [Gmail]/All Mail'.

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

    verb = (
        INTENT_VERBS.get(intent_label or "")
        or parsed_op.verb
        or _OP_VERB.get(op_type, op_type.replace("_", " ").capitalize())
    )

    # Determine how many messages this op targets by inspecting the uid_set
    uid_set_str = parsed_op.message_ids[0] if parsed_op.message_ids else ""

    # Build folder suffix for move/copy. Suppressed when the intent verb
    # already implies the destination; "Restore from Trash" keeps it unless
    # the destination is the inbox ("Restore from Trash to Receipts").
    folder_suffix = ""
    if op_type in ("move", "copy") and parsed_op.folder_to:
        if intent_label in _INTENT_IMPLIES_DEST:
            folder_suffix = ""
        elif intent_label == "restore_from_trash" and parsed_op.folder_to.strip('"').upper() == "INBOX":
            folder_suffix = ""
        else:
            folder_suffix = f" to {parsed_op.folder_to}"

    # --- No metadata available: fall back to UID-based description ---
    if not metadata:
        if intent_label in INTENT_VERBS:
            return f"{verb}: {uid_set_str}{folder_suffix}"
        return parsed_op.description

    total = len(metadata)

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
        if intent_label in INTENT_VERBS:
            return f"{verb}: {uid_set_str}{folder_suffix}"
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



