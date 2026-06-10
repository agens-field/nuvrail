"""
Operation intent derivation — semantic labels for staged operations.

An operation's ``op_type`` describes the IMAP mechanism (move, store, append);
the *intent* describes what the agent is trying to accomplish in the user's
terms (archive, delete, mark as spam). The intent is derived at staging time
from the operation's source/destination folders and flags, using the active
provider profile when one is configured and common folder-name heuristics
otherwise.

Intent labels (stored in staged_operations.intent_label):
  archive            — move into the provider's archive folder
  delete             — STORE \\Deleted (trash op) or move into the trash folder
  mark_spam          — move into the junk/spam folder
  not_spam           — move out of the junk folder back to INBOX
  restore_from_trash — move out of the trash folder
  unarchive          — move from the archive folder back to INBOX
  mark_answered      — STORE +FLAGS \\Answered (typically paired with a reply)
  save_draft         — APPEND into a drafts folder (or with the \\Draft flag)
  import_message     — APPEND into any other folder

intent_confidence:
  1.0 — exact match against the provider profile, or mechanically certain
        (e.g. STORE \\Deleted is always delete intent)
  0.8 — matched by common folder-name heuristics only (no profile match)

A NULL intent means "no semantics beyond op_type" — consumers fall back to
the op_type itself (mark_read, flag, copy, smtp_send, ...).
"""
from __future__ import annotations

from typing import Optional, Tuple

from gateway.operation_parser import ParsedOperation
from gateway.provider_profiles import ProviderProfile

PROFILE_CONFIDENCE = 1.0
HEURISTIC_CONFIDENCE = 0.8

# Common folder names per role, lower-cased. Matched against the full folder
# name and against its last path segment (so "INBOX/Archive" and
# "[Gmail]/Trash" both classify without provider knowledge).
_ARCHIVE_NAMES = {"archive", "archives", "all mail"}
_TRASH_NAMES = {"trash", "deleted items", "deleted messages", "deleted", "bin", "wastebasket"}
_JUNK_NAMES = {"junk", "junk email", "junk e-mail", "spam", "bulk mail"}
_DRAFTS_NAMES = {"drafts", "draft"}

_NAME_ROLES = [
    ("archive", _ARCHIVE_NAMES),
    ("trash", _TRASH_NAMES),
    ("junk", _JUNK_NAMES),
    ("drafts", _DRAFTS_NAMES),
]


def classify_folder(
    folder: Optional[str],
    profile: Optional[ProviderProfile] = None,
) -> Tuple[Optional[str], float]:
    """Classify a folder into a role: inbox|archive|trash|junk|drafts|None.

    Returns (role, confidence). Provider-profile matches are 1.0; common-name
    heuristics are 0.8; unknown folders are (None, 0.0).
    """
    if not folder:
        return None, 0.0
    name = folder.strip().strip('"').lower()
    if not name:
        return None, 0.0
    if name == "inbox":
        return "inbox", PROFILE_CONFIDENCE
    if profile is not None:
        if profile.archive_folder and name == profile.archive_folder.lower():
            return "archive", PROFILE_CONFIDENCE
        if profile.trash_folder and name == profile.trash_folder.lower():
            return "trash", PROFILE_CONFIDENCE
        if profile.junk_folder and name == profile.junk_folder.lower():
            return "junk", PROFILE_CONFIDENCE
    # Heuristic: match the full name, then the last path segment for both
    # "/"-delimited and "."-delimited namespaces.
    candidates = [name, name.rsplit("/", 1)[-1], name.rsplit(".", 1)[-1]]
    for candidate in candidates:
        for role, names in _NAME_ROLES:
            if candidate in names:
                return role, HEURISTIC_CONFIDENCE
    return None, 0.0


def _normalised_flags(flags: list[str]) -> set[str]:
    return {f.lstrip("\\").lower() for f in flags}


def derive_intent(
    parsed_op: ParsedOperation,
    profile: Optional[ProviderProfile] = None,
    folder_from: Optional[str] = None,
) -> Tuple[Optional[str], Optional[float]]:
    """Derive (intent_label, intent_confidence) for a parsed operation.

    ``folder_from`` is the currently-selected mailbox (the parser doesn't know
    it for STORE/MOVE); needed for direction-sensitive intents like not_spam.
    Returns (None, None) when the op_type already says everything (mark_read,
    flag, copy, ...) or nothing semantic can be inferred.
    """
    op_type = parsed_op.op_type

    if op_type == "trash":
        # STORE \Deleted — delete intent regardless of provider.
        return "delete", 1.0

    if op_type == "append":
        if "draft" in _normalised_flags(parsed_op.flags_add):
            return "save_draft", 1.0
        role, conf = classify_folder(parsed_op.folder_to, profile)
        if role == "drafts":
            return "save_draft", conf
        return "import_message", 1.0

    if op_type == "store":
        # Recognise the one generic-store case with clear semantics: \Answered.
        added = _normalised_flags(parsed_op.flags_add)
        if added == {"answered"} and not parsed_op.flags_remove:
            return "mark_answered", 1.0
        return None, None

    if op_type == "move":
        src = folder_from or parsed_op.folder_from
        dest_role, dest_conf = classify_folder(parsed_op.folder_to, profile)
        src_role, src_conf = classify_folder(src, profile)
        if dest_role == "archive":
            return "archive", dest_conf
        if dest_role == "trash":
            return "delete", dest_conf
        if dest_role == "junk":
            return "mark_spam", dest_conf
        if src_role == "junk" and dest_role == "inbox":
            return "not_spam", min(src_conf, dest_conf)
        if src_role == "trash":
            return "restore_from_trash", src_conf
        if src_role == "archive" and dest_role == "inbox":
            return "unarchive", min(src_conf, dest_conf)

    return None, None
