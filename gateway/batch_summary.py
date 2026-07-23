"""
Batch summaries — one human-readable line for a group of staged operations.

A batch is the approval UI's "commit": operations staged by one agent against
one folder within the batching window (gateway.batching). This module turns
the batch's operations into the "commit message" the user actually reads,
aggregating by semantic intent (falling back to op_type):

  Uniform batch:     'Archive 14 messages'
                     'Move 12 messages to Receipts'
  Mixed batch:       'Inbox triage — 12 archived, 3 marked read, 2 moved to Receipts'
  Mixed folders:     'Mailbox triage — ...'

Counts are messages, not operations (one MOVE of "1:5" counts as 5), so the
summary reflects the real blast radius of approving the batch.
"""
from __future__ import annotations

from gateway.operation_parser import _OP_VERB, INTENT_VERBS
from gateway.state_db import decode_json_list

# Fragment phrasing per effective label (intent_label, falling back to
# op_type), used in the mixed-batch "— 12 archived, 3 marked read" form.
# {n} is the message count for message ops, the operation count otherwise.
_FRAGMENTS: dict[str, str] = {
    "archive": "{n} archived",
    "delete": "{n} deleted",
    "trash": "{n} deleted",
    "mark_spam": "{n} marked as spam",
    "not_spam": "{n} rescued from spam",
    "restore_from_trash": "{n} restored from Trash",
    "unarchive": "{n} moved back to Inbox",
    "mark_read": "{n} marked read",
    "mark_unread": "{n} marked unread",
    "flag": "{n} starred",
    "unflag": "{n} unstarred",
    "mark_answered": "{n} marked replied",
    "move": "{n} moved",
    "copy": "{n} copied",
    "store": "{n} flag update{s}",
    "save_draft": "{n} draft{s} saved",
    "append": "{n} message{s} added",
    "import_message": "{n} message{s} added",
    "create": "{n} folder{s} created",
    "rename": "{n} folder{s} renamed",
}

# Labels that operate on messages — {n} sums expanded UID sets. Everything
# else counts operations.
_MESSAGE_LABELS = {
    "archive", "delete", "trash", "mark_spam", "not_spam", "restore_from_trash",
    "unarchive", "mark_read", "mark_unread", "flag", "unflag", "mark_answered",
    "move", "copy", "store",
}


def count_uid_set(uid_set: str | None) -> int:
    """Count the messages a UID-set string addresses ("5"→1, "1:5"→5, "1,3"→2).

    Open-ended ranges ("12:*") and malformed tokens count as 1 — the summary
    must never overstate what it cannot know.
    """
    if not uid_set:
        return 0
    total = 0
    for token in str(uid_set).split(","):
        token = token.strip()  # noqa: PLW2901 — intentional normalize-in-place of the loop token
        if not token:
            continue
        if ":" in token:
            lo, _, hi = token.partition(":")
            if lo.strip().isdigit() and hi.strip().isdigit():
                total += abs(int(hi) - int(lo)) + 1
            else:
                total += 1  # "12:*" or malformed — count conservatively
        else:
            total += 1
    return total


def _op_message_count(op: dict) -> int:
    """Messages addressed by one operation row (1 for ops without UID sets)."""
    uid_sets = decode_json_list(op.get("message_ids"))
    counted = sum(count_uid_set(s) for s in uid_sets)
    return counted or 1


def _effective_label(op: dict) -> str:
    return op.get("intent_label") or op.get("op_type") or "operation"


def _display_folder(name: str) -> str:
    """Folder name for headlines: 'INBOX' → 'Inbox', otherwise as-is."""
    return "Inbox" if name.strip('"').upper() == "INBOX" else name.strip('"')


def _dest_suffix(label: str, ops: list[dict]) -> str:
    """' to X' when every plain move/copy in the bucket shares a destination."""
    if label not in ("move", "copy"):
        return ""
    dests = {op.get("folder_to") for op in ops if op.get("folder_to")}
    if len(dests) == 1:
        return f" to {next(iter(dests))}"
    return ""


def _fragment(label: str, count: int, ops: list[dict]) -> str:
    template = _FRAGMENTS.get(label, "{n} " + label.replace("_", " "))
    return (
        template.format(n=count, s="s" if count != 1 else "")
        + _dest_suffix(label, ops)
    )


def build_batch_summary(ops: list[dict]) -> str:
    """Build the one-line human summary for a batch of staged operations.

    ``ops`` are staged_operations rows (dicts) sharing a batch_id. Buckets by
    intent_label (falling back to op_type); a single bucket collapses to a
    verb phrase, several buckets become a triage headline with per-intent
    fragments ordered by size.
    """
    if not ops:
        return ""

    buckets: dict[str, list[dict]] = {}
    for op in ops:
        buckets.setdefault(_effective_label(op), []).append(op)

    counts = {
        label: sum(
            _op_message_count(op) if label in _MESSAGE_LABELS else 1
            for op in bucket
        )
        for label, bucket in buckets.items()
    }

    if len(buckets) == 1:
        label = next(iter(buckets))
        count = counts[label]
        verb = INTENT_VERBS.get(label) or _OP_VERB.get(label)
        if verb and label in _MESSAGE_LABELS:
            noun = "message" if count == 1 else "messages"
            return f"{verb} {count} {noun}{_dest_suffix(label, buckets[label])}"
        return _fragment(label, count, buckets[label]).capitalize()

    src_folders = {op.get("folder_from") for op in ops if op.get("folder_from")}
    if len(src_folders) == 1:
        headline = f"{_display_folder(next(iter(src_folders)))} triage"
    else:
        headline = "Mailbox triage"

    fragments = [
        _fragment(label, counts[label], buckets[label])
        for label in sorted(buckets, key=lambda lbl: counts[lbl], reverse=True)
    ]
    return f"{headline} — {', '.join(fragments)}"
