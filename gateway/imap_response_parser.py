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

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SelectInfo:
    exists: Optional[int] = None
    recent: Optional[int] = None
    uidvalidity: Optional[int] = None
    uidnext: Optional[int] = None
    unseen: Optional[int] = None


@dataclass
class FetchInfo:
    seq_num: int
    uid: Optional[int] = None
    flags: list[str] = field(default_factory=list)
    subject: Optional[str] = None
    sender: Optional[str] = None  # "Display Name <email>" or just "email"
    date_str: Optional[str] = None
    size: Optional[int] = None
    message_id: Optional[str] = None


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
_RE_LIST = re.compile(
    r'^\* LIST\s+\([^)]*\)\s+"[^"]*"\s+(.+)$',
    re.IGNORECASE,
)

# FETCH response — must start with "* <seq> FETCH ("
_RE_FETCH_START = re.compile(r"^\* (\d+) FETCH \((.+)\)$", re.IGNORECASE | re.DOTALL)

# Within FETCH payload
_RE_UID = re.compile(r"\bUID (\d+)\b", re.IGNORECASE)
_RE_FLAGS = re.compile(r"\bFLAGS \(([^)]*)\)", re.IGNORECASE)
_RE_SIZE = re.compile(r"\bRFC822\.SIZE (\d+)\b", re.IGNORECASE)

# ENVELOPE: ENVELOPE ("date" "subject" (from) (sender) ...)
# We only try to extract date, subject, and from (sender).
_RE_ENVELOPE = re.compile(r'\bENVELOPE\s+\(\s*"([^"]*)"', re.IGNORECASE)  # date
_RE_ENV_SUBJECT = re.compile(r'\bENVELOPE\s+\("[^"]*"\s+"([^"]*)"', re.IGNORECASE)

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


def parse_list_response(lines: list[str]) -> list[str]:
    """Parse untagged LIST response lines, return list of folder names.

    Input:  ["* LIST (\\HasNoChildren) \\"/\\" \\"INBOX\\"", ...]
    Output: ["INBOX", "Sent", ...]

    Handles both quoted and unquoted folder names, and names with spaces.
    """
    folders: list[str] = []
    for line in lines:
        m = _RE_LIST.match(line)
        if not m:
            logger.debug("parse_list_response: no match for line: %r", line)
            continue
        raw_name = m.group(1).strip()
        # Strip surrounding quotes if present
        if raw_name.startswith('"') and raw_name.endswith('"'):
            raw_name = raw_name[1:-1]
        folders.append(raw_name)
    return folders


def parse_fetch_line(line: str) -> Optional[FetchInfo]:
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
    except Exception as exc:  # noqa: BLE001
        logger.debug("parse_fetch_line: ENVELOPE parsing failed: %s", exc)

    return info
