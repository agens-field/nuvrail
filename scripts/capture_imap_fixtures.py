"""
Capture real IMAP fixture lines from MXrouting.

Connects to blizzard.mxrouting.net:993 (SSL), performs a standard set of
read-only operations, and prints each command sent.  The printed lines become
test fixtures for test_imap_parser.py.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/capture_imap_fixtures.py

Credentials loaded from .env:
    IMAP_USER=<email>
    IMAP_PASSWORD=<password>
"""

import imaplib
import os
import sys

from dotenv import load_dotenv

load_dotenv()

HOST = "blizzard.mxrouting.net"
PORT = 993

USER = os.environ.get("IMAP_USER", "")
PASSWORD = os.environ.get("IMAP_PASSWORD", "")

if not USER or not PASSWORD:
    print("ERROR: IMAP_USER and IMAP_PASSWORD must be set in .env", file=sys.stderr)
    sys.exit(1)

_TAG_COUNTER = 0


def _tag() -> str:
    global _TAG_COUNTER
    _TAG_COUNTER += 1
    return f"F{_TAG_COUNTER:03d}"


def _send_and_print(imap: imaplib.IMAP4_SSL, command: str) -> tuple[str, list]:
    """Send an IMAP command, print it, return the raw response."""
    tag = _tag()
    full_cmd = f"{tag} {command}"
    print(f">>> {full_cmd}")
    typ, data = imap._simple_command(full_cmd)  # type: ignore[attr-defined]
    return typ, data


def main() -> None:
    print(f"# Connecting to {HOST}:{PORT}")
    with imaplib.IMAP4_SSL(HOST, PORT) as imap:
        # LOGIN
        tag = _tag()
        full_cmd = f"{tag} LOGIN {USER} {PASSWORD}"
        print(f">>> {tag} LOGIN {USER} [PASSWORD REDACTED]")
        imap._simple_command(full_cmd)  # type: ignore[attr-defined]

        # LIST
        _send_and_print(imap, 'LIST "" "*"')

        # SELECT INBOX
        _send_and_print(imap, "SELECT INBOX")

        # FETCH 1:3 FLAGS
        _send_and_print(imap, "FETCH 1:3 (FLAGS)")

        # FETCH 1 BODY[HEADER]
        _send_and_print(imap, "FETCH 1 BODY[HEADER]")

        # SEARCH ALL
        _send_and_print(imap, "SEARCH ALL")

        # STATUS INBOX (MESSAGES UNSEEN)
        _send_and_print(imap, "STATUS INBOX (MESSAGES UNSEEN)")

        # NOOP
        _send_and_print(imap, "NOOP")

        # EXAMINE INBOX
        _send_and_print(imap, "EXAMINE INBOX")

        # LOGOUT
        _send_and_print(imap, "LOGOUT")

    print("# Done.")


if __name__ == "__main__":
    main()
