"""
Lightweight parser for upstream IMAP server responses.

Not a full IMAP response parser — extracts only the fields needed for state
sync (SELECT stats, LIST folder names, FETCH UID/flags/envelope).

SELECT response flow
====================

  Upstream sends multiple untagged lines before the tagged OK:

  ┌─────────────────────────────────────────────────────────┐
  │  Upstream                   proxy (_sync_upstream_line) │
  │                                                         │
  │  * 47 EXISTS      ──────►  accumulate → select_lines    │
  │  * 2 RECENT       ──────►  accumulate → select_lines    │
  │  * 0 RECENT       ──────►  accumulate → select_lines    │
  │  * OK [UIDVAL…]   ──────►  accumulate → select_lines    │
  │  * OK [UIDNEXT…]  ──────►  accumulate → select_lines    │
  │  * OK [UNSEEN 5]  ──────►  accumulate → select_lines    │
  │  A001 OK [READ-WRITE] ──►  flush → parse_select_response│
  │                                     │                   │
  │                                     ▼                   │
  │                             update_folder_stats()       │
  └─────────────────────────────────────────────────────────┘

  LIST lines are parsed individually as they arrive.
  FETCH lines are parsed individually; only single-line FETCH responses
  (FLAGS / UID / ENVELOPE) are processed. Large literal blocks {N} are skipped.
"""

from __future__ import annotations

import email
import email.header
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SelectInfo:
    exists: int | None = None
    recent: int | None = None
    uidvalidity: int | None = None
    uidnext: int | None = None
    unseen: int | None = None


@dataclass
class ListedFolder:
    """One folder from a LIST response: name + mailbox attributes.

    ``attributes`` are the raw backslash-prefixed tokens from the parenthesised
    list (e.g. ["\\HasNoChildren", "\\Trash"]). RFC 6154 SPECIAL-USE attributes
    (\\Archive \\All \\Trash \\Junk \\Sent \\Drafts) appear here when the server
    includes them — Gmail and Dovecot do so in plain LIST responses.
    """

    name: str
    attributes: list[str] = field(default_factory=list)


@dataclass
class FetchInfo:
    seq_num: int
    uid: int | None = None
    flags: list[str] = field(default_factory=list)
    subject: str | None = None
    sender: str | None = None  # "Display Name <email>" or just "email"
    date_str: str | None = None
    size: int | None = None
    message_id: str | None = None


# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# SELECT untagged lines
_RE_EXISTS = re.compile(r"^\* (\d+) EXISTS", re.IGNORECASE)
_RE_RECENT = re.compile(r"^\* (\d+) RECENT", re.IGNORECASE)
_RE_UIDVALIDITY = re.compile(r"^\* OK \[UIDVALIDITY (\d+)\]", re.IGNORECASE)
_RE_UIDNEXT = re.compile(r"^\* OK \[UIDNEXT (\d+)\]", re.IGNORECASE)
_RE_UNSEEN = re.compile(r"^\* OK \[UNSEEN (\d+)\]", re.IGNORECASE)

# LIST response
# * LIST (\flags) "delim" "folder" or * LIST (\flags) "delim" folder
# Group 1: the attribute list (e.g. "\HasNoChildren \Trash"), group 2: the name.
_RE_LIST = re.compile(
    r'^\* LIST\s+\(([^)]*)\)\s+"[^"]*"\s+(.+)$',
    re.IGNORECASE,
)

# FETCH response — must start with "* <seq> FETCH ("
_RE_FETCH_START = re.compile(r"^\* (\d+) FETCH \((.+)\)$", re.IGNORECASE | re.DOTALL)

# FETCH response with a trailing literal: "* N FETCH (UID M RFC822 {size}" or
# "* N FETCH (UID M BODY[] {size}" etc.  Captures seq_num, UID, and literal size.
# FETCH response with a trailing literal: "* N FETCH (UID M RFC822 {size}" or
# "* N FETCH (UID M BODY[] {size}" etc.  Captures seq_num, UID, and literal size.
# The closing ')' may or may not appear on the same line before the literal data.
_RE_FETCH_LITERAL = re.compile(
    r"^\* (\d+) FETCH \(.*?\bUID (\d+)\b.*\{(\d+)\}\)?\s*$",
    re.IGNORECASE,
)

# Within FETCH payload
_RE_UID = re.compile(r"\bUID (\d+)\b", re.IGNORECASE)
_RE_FLAGS = re.compile(r"\bFLAGS \(([^)]*)\)", re.IGNORECASE)
_RE_SIZE = re.compile(r"\bRFC822\.SIZE (\d+)\b", re.IGNORECASE)

# ENVELOPE: ENVELOPE ("date" "subject" (from) (sender) ...)
# We only try to extract date, subject, from (sender), and message-id.
_RE_ENVELOPE = re.compile(r'\bENVELOPE\s+\(\s*"([^"]*)"', re.IGNORECASE)  # date
_RE_ENV_SUBJECT = re.compile(r'\bENVELOPE\s+\("[^"]*"\s+"([^"]*)"', re.IGNORECASE)

# Quoted RFC 5322 msg-id tokens inside ENVELOPE — the envelope's last two
# string fields are in-reply-to then message-id, so the LAST match is the
# message-id. Best-effort: a subject containing a quoted <x@y> could match
# too, but a real message-id always follows it, so "last wins" stays correct.
_RE_ENV_MSGID = re.compile(r'"(<[^">]+@[^">]+>)"')

# Address struct: (("Name" NIL "user" "domain")) — capture first address
_RE_ADDR_STRUCT = re.compile(
    r'\((?:"([^"]*)"|\bNIL\b)\s+(?:"[^"]*"|\bNIL\b)\s+"([^"]*)"\s+"([^"]*)"\)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_select_response(lines: list[str]) -> SelectInfo:
    """Parse a list of untagged lines from a SELECT response.

    Returns a SelectInfo with whatever fields were found.
    Unrecognised lines are silently ignored.
    """
    info = SelectInfo()
    for line in lines:
        m = _RE_EXISTS.match(line)
        if m:
            info.exists = int(m.group(1))
            continue
        m = _RE_RECENT.match(line)
        if m:
            info.recent = int(m.group(1))
            continue
        m = _RE_UIDVALIDITY.match(line)
        if m:
            info.uidvalidity = int(m.group(1))
            continue
        m = _RE_UIDNEXT.match(line)
        if m:
            info.uidnext = int(m.group(1))
            continue
        m = _RE_UNSEEN.match(line)
        if m:
            info.unseen = int(m.group(1))
            continue
        logger.debug("parse_select_response: unrecognised line: %r", line)
    return info


def parse_list_folders(lines: list[str]) -> list[ListedFolder]:
    """Parse untagged LIST response lines into ListedFolder records.

    Input:  ["* LIST (\\HasNoChildren \\Trash) \\"/\\" \\"[Gmail]/Trash\\"", ...]
    Output: [ListedFolder(name="[Gmail]/Trash", attributes=["\\HasNoChildren", "\\Trash"])]

    Handles both quoted and unquoted folder names, and names with spaces.
    """
    folders: list[ListedFolder] = []
    for line in lines:
        m = _RE_LIST.match(line)
        if not m:
            logger.debug("parse_list_folders: no match for line: %r", line)
            continue
        attributes = m.group(1).split()
        raw_name = m.group(2).strip()
        # Strip surrounding quotes if present
        if raw_name.startswith('"') and raw_name.endswith('"'):
            raw_name = raw_name[1:-1]
        folders.append(ListedFolder(name=raw_name, attributes=attributes))
    return folders


def parse_list_response(lines: list[str]) -> list[str]:
    """Parse untagged LIST response lines, return list of folder names.

    Input:  ["* LIST (\\HasNoChildren) \\"/\\" \\"INBOX\\"", ...]
    Output: ["INBOX", "Sent", ...]

    Handles both quoted and unquoted folder names, and names with spaces.
    """
    return [f.name for f in parse_list_folders(lines)]


def parse_fetch_line(line: str) -> FetchInfo | None:
    """Parse a single untagged FETCH response line.

    Returns None if the line isn't a FETCH response or cannot be parsed.
    Only processes lines that contain FLAGS, UID, or ENVELOPE — large
    literal continuation lines are ignored.
    """
    m = _RE_FETCH_START.match(line)
    if not m:
        return None

    seq_num = int(m.group(1))
    payload = m.group(2)

    info = FetchInfo(seq_num=seq_num)

    # UID
    uid_m = _RE_UID.search(payload)
    if uid_m:
        info.uid = int(uid_m.group(1))

    # FLAGS
    flags_m = _RE_FLAGS.search(payload)
    if flags_m:
        raw_flags = flags_m.group(1).strip()
        info.flags = raw_flags.split() if raw_flags else []

    # RFC822.SIZE
    size_m = _RE_SIZE.search(payload)
    if size_m:
        info.size = int(size_m.group(1))

    # ENVELOPE — best-effort
    try:
        env_m = _RE_ENVELOPE.search(payload)
        if env_m:
            info.date_str = env_m.group(1) or None

        subj_m = _RE_ENV_SUBJECT.search(payload)
        if subj_m:
            raw_subj = subj_m.group(1)
            info.subject = raw_subj if raw_subj.upper() != "NIL" else None

        # From address struct — search for first address tuple after ENVELOPE header
        # Pattern: ENVELOPE ("date" "subject" (("name" NIL "user" "domain")) ...)
        # The from-list is the third parenthesised group inside ENVELOPE.
        addr_m = _RE_ADDR_STRUCT.search(payload)
        if addr_m:
            display = addr_m.group(1)
            user = addr_m.group(2)
            domain = addr_m.group(3)
            email = f"{user}@{domain}"
            if display and display.upper() != "NIL":
                info.sender = f"{display} <{email}>"
            else:
                info.sender = email

        # Message-ID — needed to match outgoing replies to mirrored messages.
        if env_m:
            msgid_matches = _RE_ENV_MSGID.findall(payload)
            if msgid_matches:
                info.message_id = msgid_matches[-1]
    except Exception as exc:
        logger.debug("parse_fetch_line: ENVELOPE parsing failed: %s", exc)

    return info


# Maximum bytes to read from an RFC822 literal to extract headers.
# Real-world message headers rarely exceed 8 KB; 16 KB is a safe cap.
_HEADER_READ_LIMIT = 16 * 1024


def _decode_header(raw: str) -> str:
    """Decode an RFC2047 encoded-word header value to a plain string.

    Examples:
      '=?UTF-8?Q?Re=3A_Invoice?='  →  'Re: Invoice'
      '=?iso-8859-1?B?dGVzdA==?='  →  'test'
      'Plain subject'              →  'Plain subject'
    """
    try:
        parts = email.header.decode_header(raw)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return "".join(decoded).strip()
    except Exception:
        return raw.strip()


def extract_headers_from_rfc822(
    data: bytes,
) -> tuple[str | None, str | None, str | None]:
    """Extract (sender, subject, message_id) from the start of a raw RFC822 message.

    Parses only the header block (everything before the first blank line).
    Returns (None, None, None) if headers cannot be extracted.

    Handles:
    - RFC2047 encoded-word subjects (=?UTF-8?Q?...?=)
    - Multi-line (folded) headers
    - From:/Subject:/Message-ID: in any order

    Does NOT load the full message body — data may be a truncated prefix.
    """
    try:
        # Find end of headers (blank line). Work on the prefix only.
        header_end = data.find(b"\r\n\r\n")
        if header_end == -1:
            header_end = data.find(b"\n\n")
        header_bytes = data[:header_end] if header_end != -1 else data

        # Use stdlib email parser in headersonly mode — safe on truncated input.
        msg = email.message_from_bytes(header_bytes + b"\r\n\r\n")

        raw_from = msg.get("From", "")
        raw_subject = msg.get("Subject", "")
        raw_message_id = msg.get("Message-ID", "")

        sender = _decode_header(raw_from) if raw_from else None
        subject = _decode_header(raw_subject) if raw_subject else None
        message_id = raw_message_id.strip() if raw_message_id else None

        return sender or None, subject or None, message_id or None
    except Exception as exc:
        logger.debug("extract_headers_from_rfc822: failed: %s", exc)
        return None, None, None
