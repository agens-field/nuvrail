"""
E2E test helpers for direct IMAP/SMTP access (bypassing the Nuvrail proxy).

These utilities connect straight to the upstream MXrouting server and are used
to verify ground truth — did a message actually arrive? was a flag actually set?
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from email.mime.text import MIMEText

import aioimaplib
import aiosmtplib

logger = logging.getLogger(__name__)


async def wait_for_message(
    imap_config: dict,
    subject_contains: str,
    timeout: float = 15.0,
    poll_interval: float = 2.0,
) -> int | None:
    """Poll direct IMAP until a message with the given subject appears.

    Connects directly to the upstream server (bypassing the proxy).
    Returns the UID (int) or None on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = aioimaplib.IMAP4_SSL(
            host=imap_config["host"],
            port=imap_config["port"],
        )
        try:
            await client.wait_hello_from_server()
            await client.login(imap_config["user"], imap_config["password"])
            await client.select("INBOX")
            status, data = await client.uid_search("ALL")
            if status != "OK":
                logger.warning("IMAP search returned status=%s", status)
            else:
                all_uids = data[0].decode().split() if data[0] else []
                # Fetch subjects for all messages and find matching one
                for uid_str in reversed(all_uids):
                    fetch_status, fetch_data = await client.uid(
                        "fetch", uid_str, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
                    )
                    if fetch_status != "OK":
                        continue
                    # fetch_data is a list; items can be bytes or bytearray
                    full_response = b"".join(
                        bytes(item) if isinstance(item, (bytes, bytearray)) else b""
                        for item in fetch_data
                    )
                    if subject_contains.encode("utf-8") in full_response:
                        return int(uid_str)
        except Exception as exc:
            logger.debug("wait_for_message poll error: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                await client.logout()

        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(poll_interval, remaining))

    return None


async def delete_message_by_uid(imap_config: dict, uid: int) -> None:
    """Delete a message by UID via direct IMAP (flag \\Deleted + EXPUNGE).

    Used for test cleanup to prevent orphan messages accumulating in the mailbox.
    """
    client = aioimaplib.IMAP4_SSL(
        host=imap_config["host"],
        port=imap_config["port"],
    )
    try:
        await client.wait_hello_from_server()
        await client.login(imap_config["user"], imap_config["password"])
        await client.select("INBOX")
        await client.uid("store", str(uid), "+FLAGS", r"(\Deleted)")
        await client.expunge()
    finally:
        with contextlib.suppress(Exception):
            await client.logout()


async def send_direct_smtp(
    smtp_config: dict,
    subject: str,
    body: str = "Test message",
    to_addr: str | None = None,
) -> None:
    """Send a message directly via aiosmtplib (bypassing the Nuvrail proxy).

    Used to set up test state (e.g. putting a message in INBOX so it can
    be archived via the proxy).
    """
    sender = smtp_config["user"]
    recipient = to_addr or sender

    msg = MIMEText(body, "plain")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject

    await aiosmtplib.send(
        msg,
        hostname=smtp_config["host"],
        port=smtp_config["port"],
        username=smtp_config["user"],
        password=smtp_config["password"],
        start_tls=True,
    )


async def get_message_flags(imap_config: dict, uid: int) -> list[str]:
    """Fetch the flags for a specific UID via direct IMAP.

    Returns a list of flag strings (e.g. ['\\Seen', '\\Flagged']).
    """
    client = aioimaplib.IMAP4_SSL(
        host=imap_config["host"],
        port=imap_config["port"],
    )
    try:
        await client.wait_hello_from_server()
        await client.login(imap_config["user"], imap_config["password"])
        await client.select("INBOX")
        status, data = await client.uid("fetch", str(uid), "(FLAGS)")
        if status != "OK":
            return []
        # Parse flags from response bytes/bytearrays like: b'1 FETCH (UID 42 FLAGS (\\Seen))'
        full_response = b"".join(
            bytes(item) if isinstance(item, (bytes, bytearray)) else b"" for item in data
        ).decode("utf-8", errors="replace")
        import re
        m = re.search(r"FLAGS\s*\(([^)]*)\)", full_response, re.IGNORECASE)
        if m:
            flags_str = m.group(1).strip()
            return flags_str.split() if flags_str else []
        return []
    finally:
        with contextlib.suppress(Exception):
            await client.logout()


async def check_message_exists(imap_config: dict, uid: int) -> bool:
    """Check whether a specific UID still exists in INBOX via direct IMAP.

    Returns True if the message is present, False if it has been deleted/expunged.
    """
    client = aioimaplib.IMAP4_SSL(
        host=imap_config["host"],
        port=imap_config["port"],
    )
    try:
        await client.wait_hello_from_server()
        await client.login(imap_config["user"], imap_config["password"])
        await client.select("INBOX")
        # UID SEARCH UID <uid> returns the UID if it exists, empty if not
        status, data = await client.uid_search(f"UID {uid}")
        if status != "OK":
            return False
        result = data[0].decode().strip() if data[0] else ""
        return str(uid) in result.split()
    finally:
        with contextlib.suppress(Exception):
            await client.logout()
