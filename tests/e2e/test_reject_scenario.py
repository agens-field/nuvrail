"""
E2E tests: rejection scenarios — verify that rejected operations produce no
observable side effects on the upstream mail server.

Tests:
  1. SMTP send rejected → message never arrives in INBOX
  2. IMAP write rejected → flag NOT applied to message

These tests require live network access to blizzard.mxrouting.net.
Credentials come from .env via the e2e conftest.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Optional

import pytest

from tests.e2e.helpers import (
    check_message_exists,
    delete_message_by_uid,
    get_message_flags,
    send_direct_smtp,
    wait_for_message,
)

_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Low-level SMTP helpers (plain TCP proxy connection)
# ---------------------------------------------------------------------------


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


async def _read_line(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


async def _read_response(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> list[str]:
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("Timed out reading SMTP response")
        raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
        if not raw:
            break
        decoded = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(decoded)
        if len(decoded) < 4 or decoded[3] != "-":
            break
    return lines


async def _send(writer: asyncio.StreamWriter, text: str) -> None:
    writer.write(text.encode())
    await writer.drain()


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass


def _auth_plain_b64(user: str, password: str) -> str:
    return base64.b64encode(f"\x00{user}\x00{password}".encode()).decode()


def _extract_op_id(text: str) -> Optional[str]:
    m = re.search(r"(op_[A-Za-z0-9]+)", text)
    return m.group(1) if m else None


async def _smtp_send_via_proxy(
    host: str,
    port: int,
    user: str,
    password: str,
    subject: str,
) -> str:
    """Send a message via the SMTP proxy. Returns op_id from STAGED response."""
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # greeting

        await _send(writer, "EHLO test.client\r\n")
        await _read_response(reader)

        creds = _auth_plain_b64(user, password)
        await _send(writer, f"AUTH PLAIN {creds}\r\n")
        auth_resp = await _read_response(reader)
        assert auth_resp[-1].startswith("235"), f"AUTH failed: {auth_resp!r}"

        await _send(writer, f"MAIL FROM:<{user}>\r\n")
        await _read_response(reader)

        await _send(writer, f"RCPT TO:<{user}>\r\n")
        await _read_response(reader)

        await _send(writer, "DATA\r\n")
        data_resp = await _read_response(reader)
        assert data_resp[-1].startswith("354"), f"Expected 354: {data_resp!r}"

        await _send(writer, f"From: <{user}>\r\nTo: <{user}>\r\nSubject: {subject}\r\n\r\n")
        await _send(writer, "Reject scenario test message.\r\n")
        await _send(writer, ".\r\n")

        staged_resp = await _read_response(reader)
        resp_text = " ".join(staged_resp)
        assert "250" in staged_resp[-1], f"Expected 250: {staged_resp!r}"
        assert "STAGED" in resp_text.upper(), f"Expected STAGED: {staged_resp!r}"

        op_id = _extract_op_id(resp_text)
        assert op_id is not None, f"No op_id in: {staged_resp!r}"
        return op_id
    finally:
        try:
            await _send(writer, "QUIT\r\n")
        except OSError:
            pass
        await _close(writer)


# ---------------------------------------------------------------------------
# Low-level IMAP proxy helpers
# ---------------------------------------------------------------------------


async def _imap_raw_connect(
    host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


async def _imap_raw_read_line(
    reader: asyncio.StreamReader, timeout: float = _TIMEOUT
) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


async def _imap_raw_read_until_tagged(
    reader: asyncio.StreamReader, tag: str, timeout: float = _TIMEOUT
) -> list[str]:
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {tag!r}")
        raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
        decoded = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(decoded)
        upper = decoded.upper()
        if (
            upper.startswith(tag.upper() + " OK")
            or upper.startswith(tag.upper() + " NO")
            or upper.startswith(tag.upper() + " BAD")
        ):
            break
    return lines


async def _imap_raw_send(writer: asyncio.StreamWriter, cmd: str) -> None:
    writer.write((cmd + "\r\n").encode())
    await writer.drain()


async def _imap_raw_close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass


async def _proxy_imap_store_flags(
    proxy_host: str,
    proxy_port: int,
    user: str,
    password: str,
    uid: int,
    flags: str,
    flags_op: str = "+FLAGS",
) -> tuple[str, str]:
    """Connect to the IMAP proxy, LOGIN, SELECT INBOX, UID STORE flags.

    Returns (full_response_text, op_id).
    """
    reader, writer = await _imap_raw_connect(proxy_host, proxy_port)
    try:
        await _imap_raw_read_line(reader)  # greeting

        await _imap_raw_send(writer, f"t01 LOGIN {user} {password}")
        await _imap_raw_read_until_tagged(reader, "t01")

        await _imap_raw_send(writer, "t02 SELECT INBOX")
        await _imap_raw_read_until_tagged(reader, "t02")

        await _imap_raw_send(writer, f"t03 UID STORE {uid} {flags_op} ({flags})")
        store_lines = await _imap_raw_read_until_tagged(reader, "t03")
        full_response = " ".join(store_lines)
        op_id = _extract_op_id(full_response) or ""
        return full_response, op_id
    finally:
        try:
            await _imap_raw_send(writer, "t99 LOGOUT")
        except OSError:
            pass
        await _imap_raw_close(writer)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_send_rejected_never_arrives(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    SMTP send staged then rejected: message must NOT appear in INBOX.

    Flow:
      1. Send via SMTP proxy → STAGED, get op_id
      2. Reject via API → status = rejected
      3. Wait 5s for any spurious delivery
      4. Direct IMAP SEARCH: assert NOT found
    """
    subject = f"Reject Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    smtp_host = e2e_setup["smtp_host"]
    smtp_port = e2e_setup["smtp_port"]
    user = upstream_smtp_cfg["user"]
    password = upstream_smtp_cfg["password"]

    # Step 1: Send via proxy → STAGED
    op_id = await _smtp_send_via_proxy(smtp_host, smtp_port, user, password, subject)

    # Step 2: Reject via API
    reject_resp = await api_client.post(f"/api/v1/operations/{op_id}/reject")
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    # Step 3: Wait a short time — if anything was going to arrive it would
    # have arrived or be in-flight. 5s is enough to catch any accidental relay.
    await asyncio.sleep(5.0)

    # Step 4: Verify message did NOT arrive in INBOX
    uid = await wait_for_message(upstream_imap_cfg, subject, timeout=1.0, poll_interval=0.5)
    assert uid is None, (
        f"Message {subject!r} arrived in INBOX after rejection — "
        f"proxy should not have relayed it. UID={uid}"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_write_rejected_flag_unchanged(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    IMAP write (STORE \\Seen) rejected: flag must NOT be applied to message.

    Flow:
      1. Send message via direct SMTP → wait for arrival
      2. STORE \\Seen via IMAP proxy → STAGED, get op_id
      3. Reject via API
      4. Direct IMAP FETCH FLAGS: \\Seen must NOT be in flags
      5. Cleanup
    """
    subject = f"Flag Reject Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    imap_proxy_host = e2e_setup["imap_host"]
    imap_proxy_port = e2e_setup["imap_port"]
    user = upstream_smtp_cfg["user"]
    password = upstream_smtp_cfg["password"]

    uid: Optional[int] = None

    try:
        # Step 1: Send message via direct SMTP, wait for arrival
        await send_direct_smtp(upstream_smtp_cfg, subject)
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=_TIMEOUT)
        assert uid is not None, f"Setup message {subject!r} did not arrive within {_TIMEOUT}s"

        # Step 2: STORE \\Seen via IMAP proxy
        store_response, op_id = await _proxy_imap_store_flags(
            imap_proxy_host, imap_proxy_port, user, password, uid, r"\Seen"
        )
        assert "STAGED" in store_response.upper(), (
            f"Expected STAGED in proxy response, got: {store_response!r}"
        )
        assert op_id, f"No op_id in STAGED response: {store_response!r}"

        # Step 3: Reject via API
        reject_resp = await api_client.post(f"/api/v1/operations/{op_id}/reject")
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "rejected"

        # Step 4: Verify \\Seen was NOT applied
        flags = await get_message_flags(upstream_imap_cfg, uid)
        seen_flags = [f for f in flags if "Seen" in f]
        assert not seen_flags, (
            f"\\Seen flag was applied despite rejection! flags={flags!r}"
        )

    finally:
        # Step 5: Cleanup
        if uid is not None:
            await delete_message_by_uid(upstream_imap_cfg, uid)
