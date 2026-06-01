"""
E2E tests: SMTP proxy staging → API approve → upstream delivery verification.

Flow for each test:
  1. Connect to the in-process SMTP proxy (plain TCP)
  2. Authenticate with real upstream credentials
  3. Send a message — proxy returns 250 OK [STAGED] with an op_id
  4. Verify the op appears in the API as pending
  5. Approve via the API → proxy relays to upstream SMTP
  6. Poll direct IMAP (bypassing proxy) for arrival
  7. Cleanup: delete the test message from INBOX

These tests require live network access to mail.example.com.
Credentials come from .env via the e2e conftest.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Optional

import pytest

from tests.e2e.helpers import delete_message_by_uid, wait_for_message

_TIMEOUT = 20  # generous timeout for network I/O and SMTP relay

# ---------------------------------------------------------------------------
# Low-level SMTP client helpers (plain TCP — proxy speaks plain, not SSL)
# ---------------------------------------------------------------------------


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


async def _read_line(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


async def _read_response(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> list[str]:
    """Read a complete (possibly multi-line) SMTP response."""
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


def _extract_op_id(response_line: str) -> Optional[str]:
    """Extract op_XXXXXX from a STAGED response line."""
    m = re.search(r"(op_[A-Za-z0-9]+)", response_line)
    return m.group(1) if m else None


async def _smtp_send_via_proxy(
    host: str,
    port: int,
    user: str,
    password: str,
    subject: str,
    to_addr: Optional[str] = None,
) -> str:
    """
    Perform a full SMTP session via the proxy:
      EHLO → AUTH → MAIL FROM → RCPT TO → DATA → body → '.'
    Returns the op_id extracted from the 250 [STAGED] response.
    """
    recipient = to_addr or user
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting

        await _send(writer, "EHLO test.client\r\n")
        await _read_response(reader)

        creds = _auth_plain_b64(user, password)
        await _send(writer, f"AUTH PLAIN {creds}\r\n")
        auth_resp = await _read_response(reader)
        assert auth_resp[-1].startswith("235"), f"AUTH failed: {auth_resp!r}"

        await _send(writer, f"MAIL FROM:<{user}>\r\n")
        await _read_response(reader)

        await _send(writer, f"RCPT TO:<{recipient}>\r\n")
        await _read_response(reader)

        await _send(writer, "DATA\r\n")
        data_go_ahead = await _read_response(reader)
        assert data_go_ahead[-1].startswith("354"), (
            f"Expected 354, got: {data_go_ahead!r}"
        )

        await _send(writer, f"From: <{user}>\r\n")
        await _send(writer, f"To: <{recipient}>\r\n")
        await _send(writer, f"Subject: {subject}\r\n")
        await _send(writer, "\r\n")
        await _send(writer, "This is an automated E2E test message from Nuvrail.\r\n")
        await _send(writer, ".\r\n")

        staged_resp = await _read_response(reader)
        resp_text = " ".join(staged_resp)
        assert "250" in staged_resp[-1], f"Expected 250, got: {staged_resp!r}"
        assert "STAGED" in resp_text.upper(), f"Expected STAGED, got: {staged_resp!r}"

        op_id = _extract_op_id(resp_text)
        assert op_id is not None, f"Could not extract op_id from: {staged_resp!r}"
        return op_id
    finally:
        try:
            await _send(writer, "QUIT\r\n")
        except OSError:
            pass
        await _close(writer)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_send_staged_then_approved_arrives(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    Full SMTP approval flow:
      1. Send via proxy → STAGED
      2. Assert op appears in API as pending
      3. Approve via API → executed
      4. Verify message arrives in INBOX via direct IMAP (within 15s)
      5. Cleanup: delete test message
    """
    subject = f"E2E Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    smtp_host = e2e_setup["smtp_host"]
    smtp_port = e2e_setup["smtp_port"]
    agent_user = e2e_setup["proxy_agent_auth"]["smtp"]["username"]
    agent_token = e2e_setup["proxy_agent_auth"]["smtp"]["token"]
    recipient = upstream_smtp_cfg["user"]

    uid_to_delete: Optional[int] = None

    try:
        # Step 1–3: Send via proxy, get staged op_id
        op_id = await _smtp_send_via_proxy(
            smtp_host, smtp_port, agent_user, agent_token, subject, to_addr=recipient
        )

        # Step 4: Verify op appears in API as pending (Bearer auth required)
        resp = await api_client.get(
            "/api/v1/operations?status=pending",
            headers=e2e_setup["auth_headers"],
        )
        assert resp.status_code == 200
        ops = resp.json()["operations"]
        op_ids = [op["id"] for op in ops]
        assert op_id in op_ids, f"op_id {op_id!r} not in pending ops: {op_ids}"
        op = next(op for op in ops if op["id"] == op_id)
        assert op["protocol"] == "smtp", f"Expected smtp, got: {op['protocol']!r}"

        # Step 5: Approve via API (Bearer auth required)
        approve_resp = await api_client.post(
            f"/api/v1/operations/{op_id}/approve",
            headers=e2e_setup["auth_headers"],
        )
        assert approve_resp.status_code == 200, (
            f"Approve failed: {approve_resp.status_code} {approve_resp.text}"
        )
        assert approve_resp.json()["status"] == "executed"

        # Step 6: Wait for message to arrive via direct IMAP
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=15.0)
        assert uid is not None, (
            f"Message with subject {subject!r} did not arrive in INBOX within 15s"
        )
        uid_to_delete = uid

    finally:
        # Step 7: Cleanup
        if uid_to_delete is not None:
            await delete_message_by_uid(upstream_imap_cfg, uid_to_delete)


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_send_staged_multiple_then_approve_all(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    Send 3 messages via proxy, approve all, verify all 3 arrive within 20s.
    """
    api_client = e2e_setup["api_client"]
    smtp_host = e2e_setup["smtp_host"]
    smtp_port = e2e_setup["smtp_port"]
    agent_user = e2e_setup["proxy_agent_auth"]["smtp"]["username"]
    agent_token = e2e_setup["proxy_agent_auth"]["smtp"]["token"]
    recipient = upstream_smtp_cfg["user"]

    timestamp = time.time()
    subjects = [f"E2E Multi Test {timestamp:.0f}-{i}" for i in range(3)]
    op_ids: list[str] = []
    uids_to_delete: list[int] = []

    try:
        # Step 1: Send 3 messages via proxy
        for subject in subjects:
            op_id = await _smtp_send_via_proxy(
                smtp_host, smtp_port, agent_user, agent_token, subject, to_addr=recipient
            )
            op_ids.append(op_id)

        # Step 2: Verify all 3 appear as pending (Bearer auth required)
        resp = await api_client.get(
            "/api/v1/operations?status=pending",
            headers=e2e_setup["auth_headers"],
        )
        assert resp.status_code == 200
        pending_ids = [op["id"] for op in resp.json()["operations"]]
        for op_id in op_ids:
            assert op_id in pending_ids, f"op_id {op_id!r} not in pending ops"

        # Step 3: Approve all 3 (Bearer auth required)
        for op_id in op_ids:
            approve_resp = await api_client.post(
                f"/api/v1/operations/{op_id}/approve",
                headers=e2e_setup["auth_headers"],
            )
            assert approve_resp.status_code == 200
            assert approve_resp.json()["status"] == "executed"

        # Step 4–5: Poll for all 3 to arrive (up to 20s)
        for subject in subjects:
            uid = await wait_for_message(upstream_imap_cfg, subject, timeout=20.0)
            assert uid is not None, (
                f"Message {subject!r} did not arrive in INBOX within 20s"
            )
            uids_to_delete.append(uid)

    finally:
        # Step 6: Cleanup all messages
        for uid in uids_to_delete:
            await delete_message_by_uid(upstream_imap_cfg, uid)
