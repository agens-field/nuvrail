"""
Provider-specific IMAP normalization profiles.

Different IMAP providers handle the same logical operations (archive, delete,
sent-dedup) in non-standard ways.  AI agents send standard IMAP and must not
need to know which provider is behind the proxy; this module translates.

Data flow (Gmail example):

  Agent sends standard IMAP          proxy normalizes           Real Gmail IMAP
  ─────────────────────────          ─────────────────          ────────────────
  UID COPY X INBOX     ─────────────────────────────────────►  (held, not staged)
  UID STORE X +FLAGS (\\Deleted) ──►  detect C+S pattern        UID MOVE X [Gmail]/All Mail
  EXPUNGE (blocked)                  rewrite → MOVE
                                     log: "archive: rewrote COPY+DELETE to MOVE for Gmail"

  APPEND [Gmail]/Sent Mail ────────► suppress (Gmail auto-adds after SMTP send)
                                     log: "sent-dedup: suppressed APPEND to [Gmail]/Sent Mail"

Profiles are keyed by upstream hostname fragment.  The match is a suffix check
so subdomains work naturally (imap.gmail.com → gmail profile).

Detection order matters: more specific patterns are checked first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Profile dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProviderProfile:
    """Normalization rules for one IMAP provider.

    Fields
    ------
    name : str
        Human-readable name used in log messages and audit trail notes.
    archive_folder : str | None
        If set, a COPY+STORE \\Deleted to *any* folder immediately followed by
        STORE \\Deleted on the same UIDs is treated as "archive intent" and
        rewritten to UID MOVE → archive_folder.
    trash_folder : str | None
        Folder name for explicit delete-to-trash.  If set, STORE \\Deleted
        without a preceding COPY is rewritten to UID MOVE → trash_folder.
        None means use the standard \\Trash flag approach (current behaviour).
    sent_suppress_folders : list[str]
        APPEND commands targeting any of these folders are silently suppressed.
        Gmail auto-adds to [Gmail]/Sent Mail after every SMTP send; APPENDing
        there would create duplicates.
    sent_folder : str | None
        Folder to IMAP APPEND the sent message to after a successful SMTP relay.
        None means the provider auto-saves on relay (Gmail, Outlook) and no
        APPEND is needed. When set, Nuvrail performs the APPEND on approval so
        the Sent folder is populated regardless of whether the agent does it.
    move_capable : bool
        True if this provider reliably supports RFC 6851 UID MOVE.
        Used to prefer MOVE over COPY+EXPUNGE on execution.
    """

    name: str
    archive_folder: Optional[str] = None
    trash_folder: Optional[str] = None
    sent_suppress_folders: list[str] = field(default_factory=list)
    sent_folder: Optional[str] = None
    move_capable: bool = True


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

GMAIL_PROFILE = ProviderProfile(
    name="Gmail",
    archive_folder="[Gmail]/All Mail",
    trash_folder="[Gmail]/Trash",
    sent_suppress_folders=["[Gmail]/Sent Mail"],
    sent_folder=None,   # Gmail auto-saves to [Gmail]/Sent Mail on every SMTP relay
    move_capable=True,
)

ICLOUD_PROFILE = ProviderProfile(
    name="iCloud Mail",
    archive_folder="Archive",
    trash_folder="Deleted Messages",
    # Suppress agent APPENDs to Sent Messages — Nuvrail handles this save after relay.
    sent_suppress_folders=["Sent Messages"],
    sent_folder="Sent Messages",  # iCloud does NOT auto-save via SMTP relay; we APPEND
    move_capable=True,
)

OUTLOOK_PROFILE = ProviderProfile(
    name="Outlook/Microsoft 365",
    archive_folder=None,          # Outlook uses standard Junk/Archive folders
    trash_folder="Deleted Items",
    sent_suppress_folders=["Sent Items", "Sent"],
    sent_folder=None,   # Outlook auto-saves to Sent Items on every SMTP relay
    move_capable=True,
)

GENERIC_PROFILE = ProviderProfile(
    name="Generic IMAP",
    archive_folder=None,
    trash_folder=None,            # leave to standard \\Trash flag
    sent_suppress_folders=[],
    sent_folder="Sent",           # most generic servers use "Sent"; APPEND is non-fatal if missing
    move_capable=False,           # conservative default: don't assume MOVE
)

# ---------------------------------------------------------------------------
# Detection table — (hostname_suffix, profile) pairs, checked in order
# ---------------------------------------------------------------------------

_DETECTION_TABLE: list[tuple[str, ProviderProfile]] = [
    ("gmail.com",              GMAIL_PROFILE),
    ("googlemail.com",         GMAIL_PROFILE),
    ("mail.me.com",            ICLOUD_PROFILE),   # imap.mail.me.com / smtp.mail.me.com
    ("mac.com",                ICLOUD_PROFILE),   # legacy iCloud domain
    ("outlook.com",            OUTLOOK_PROFILE),
    ("outlook.office365.com",  OUTLOOK_PROFILE),
    ("hotmail.com",            OUTLOOK_PROFILE),
    ("live.com",               OUTLOOK_PROFILE),
]


def detect_provider(upstream_host: str) -> ProviderProfile:
    """Return the ProviderProfile for the given upstream hostname.

    Matches on suffix (case-insensitive) so imap.gmail.com → GMAIL_PROFILE.
    Falls back to GENERIC_PROFILE if nothing matches.

    >>> detect_provider("imap.gmail.com").name
    'Gmail'
    >>> detect_provider("smtp.googlemail.com").name
    'Gmail'
    >>> detect_provider("outlook.office365.com").name
    'Outlook/Microsoft 365'
    >>> detect_provider("mail.example.com").name
    'Generic IMAP'
    """
    host_lower = upstream_host.lower()
    for suffix, profile in _DETECTION_TABLE:
        if host_lower == suffix or host_lower.endswith("." + suffix):
            return profile
    return GENERIC_PROFILE


# ---------------------------------------------------------------------------
# Session-level pending COPY intent
# ---------------------------------------------------------------------------


@dataclass
class PendingCopyIntent:
    """Tracks an intercepted COPY command waiting for a STORE \\Deleted follow-up.

    The COPY is NOT staged immediately.  If a STORE \\Deleted for the same UIDs
    arrives before the intent expires (next non-matching write command), the pair
    is rewritten as a single UID MOVE.  If no STORE \\Deleted follows, the COPY
    is flushed as a normal staged operation.

    Fields
    ------
    tag : str
        IMAP tag of the original COPY command (used to send the synthetic response).
    uid_set : str
        UID set string from the original COPY.
    destination : str
        Destination folder from the original COPY.
    """

    tag: str
    uid_set: str
    destination: str


# ---------------------------------------------------------------------------
# Rewrite helpers
# ---------------------------------------------------------------------------


def should_suppress_append(folder: str, profile: ProviderProfile) -> bool:
    """Return True if an APPEND to *folder* should be silently suppressed.

    Case-insensitive match against the profile's sent_suppress_folders list.

    >>> should_suppress_append("[Gmail]/Sent Mail", GMAIL_PROFILE)
    True
    >>> should_suppress_append("INBOX", GMAIL_PROFILE)
    False
    """
    folder_lower = folder.lower()
    return any(f.lower() == folder_lower for f in profile.sent_suppress_folders)


def copy_archive_intent(
    copy_destination: str,
    profile: ProviderProfile,
) -> Optional[str]:
    """Return the normalized MOVE destination if this COPY represents archive/delete intent.

    For Gmail, both COPY-to-All-Mail (archive) and COPY-to-Trash (delete) are
    recognized.  Returns the destination folder as-is so the caller can rewrite
    the staged operation description correctly.

    Returns None if this COPY should be staged normally (e.g. COPY to a user folder).

    >>> copy_archive_intent("[Gmail]/All Mail", GMAIL_PROFILE)
    '[Gmail]/All Mail'
    >>> copy_archive_intent("[Gmail]/Trash", GMAIL_PROFILE)
    '[Gmail]/Trash'
    >>> copy_archive_intent("Work", GMAIL_PROFILE)
    """
    if not profile.archive_folder and not profile.trash_folder:
        return None
    dest_lower = copy_destination.lower()
    if profile.archive_folder and dest_lower == profile.archive_folder.lower():
        return profile.archive_folder
    if profile.trash_folder and dest_lower == profile.trash_folder.lower():
        return profile.trash_folder
    return None
