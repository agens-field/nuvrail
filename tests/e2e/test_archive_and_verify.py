"""
E2E tests: IMAP proxy staging for write operations (STORE / flag changes).

Key invariant being tested:
  The proxy must NOT apply the flag change to the upstream server
  until the operation is explicitly approved. This proves staging is
  actually gating execution, not just decorating it.

Test topology:

  test
   ├─► direct SMTP  (send a real message so we have something to flag)
   ├─► IMAP proxy   (intercept the STORE command → STAGED)
   ├─► direct IMAP  (ground truth: verify flag state before and after)
   └─► FastAPI API  (approve / reject the staged operation)

These tests require live network access to mail.example.com.
Credentials come from .env via the e2e conftest.

All API calls pass auth_headers (Bearer token) from e2e_setup, exercising
the full Lane 3 authentication stack on every approve/reject call.
"""
from __future__ import annotations

import asyncio
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

_TIMEOUT = 20  # generous timeout for network I/O

# ---------------------------------------------------------------------------
# Low-level IMAP helpers (raw asyncio — used when we need to see the full
# response text including the [STAGED] token)
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
    """Read lines until a tagged OK/NO/BAD response for `tag`."""
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for tagged response to {tag!r}")
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


def _extract_op_id(text: str) -> Optional[str]:
    """Extract op_XXXXXX from a STAGED response."""
    m = re.search(r"(op_[A-Za-z0-9]+)", text)
    return m.group(1) if m else None


async def _proxy_imap_store_flags(
    proxy_host: str,
    proxy_port: int,
    imap_user: str,
    imap_password: str,
    uid: int,
    flags: str,
    flags_op: str = "+FLAGS",
) -> tuple[str, str]:
    """
    Connect to the IMAP proxy, LOGIN, SELECT INBOX, then STORE `uid` with flags.

    Returns (tagged_response_line, op_id) — the tagged response from the proxy
    (which should contain [STAGED]) and the extracted op_id.
    """
    reader, writer = await _imap_raw_connect(proxy_host, proxy_port)
    try:
        await _imap_raw_read_line(reader)  # consume greeting

        await _imap_raw_send(writer, f"t01 LOGIN {imap_user} {imap_password}")
        await _imap_raw_read_until_tagged(reader, "t01")

        await _imap_raw_send(writer, "t02 SELECT INBOX")
        await _imap_raw_read_until_tagged(reader, "t02")

        await _imap_raw_send(writer, f"t03 UID STORE {uid} {flags_op} ({flags})")
        store_lines = await _imap_raw_read_until_tagged(reader, "t03")
        full_response = " ".join(store_lines)

        op_id = _extract_op_id(full_response)
        return full_response, op_id or ""
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
async def test_imap_archive_staged_not_yet_executed(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    Full IMAP approve cycle: staging must gate execution, then approval must
    apply the flag change to the upstream server.

    Flow:
      1. Send a message directly to INBOX (bypass proxy)
      2. Wait for it to arrive
      3. STORE \\Deleted via IMAP proxy → assert STAGED + op_id
      4. Direct IMAP: assert message is STILL PRESENT and flag NOT applied
      5. Approve via API (with auth)
      6. Direct IMAP: assert \\Deleted flag IS now applied upstream
      7. Cleanup
    """
    subject = f"Archive Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    auth_headers = e2e_setup["auth_headers"]
    imap_proxy_host = e2e_setup["imap_host"]
    imap_proxy_port = e2e_setup["imap_port"]
    proxy_agent = e2e_setup["proxy_agent_auth"]["imap"]
    user = proxy_agent["username"]
    password = proxy_agent["token"]

    uid: Optional[int] = None

    try:
        # Step 1: Send a real message via direct SMTP (bypass proxy)
        await send_direct_smtp(upstream_smtp_cfg, subject)

        # Step 2: Wait for it to arrive in INBOX
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=_TIMEOUT)
        assert uid is not None, (
            f"Setup message with subject {subject!r} did not arrive within {_TIMEOUT}s"
        )

        # Step 3: STORE \\Deleted via IMAP proxy
        store_response, op_id = await _proxy_imap_store_flags(
            imap_proxy_host, imap_proxy_port, user, password, uid, r"\Deleted"
        )
        assert "STAGED" in store_response.upper(), (
            f"Expected STAGED in proxy response, got: {store_response!r}"
        )
        assert op_id, f"Could not extract op_id from: {store_response!r}"

        # Step 4: KEY ASSERTION — message must still be present, flag not applied
        still_present = await check_message_exists(upstream_imap_cfg, uid)
        assert still_present, (
            "Message was NOT found via direct IMAP immediately after proxy STORE "
            "— staging failed to gate execution. UID may have been expunged or "
            "the flag applied prematurely."
        )
        flags_before = await get_message_flags(upstream_imap_cfg, uid)
        assert r"\Deleted" not in flags_before and "\\Deleted" not in flags_before, (
            f"\\Deleted flag was applied before approval! flags={flags_before!r}"
        )

        # Step 5: Approve via API (Bearer auth required)
        approve_resp = await api_client.post(
            f"/api/v1/operations/{op_id}/approve",
            headers=auth_headers,
        )
        assert approve_resp.status_code == 200, (
            f"Approve failed: {approve_resp.status_code} {approve_resp.text}"
        )
        assert approve_resp.json()["status"] == "executed"

        # Step 6: Verify upstream execution — flag must now be applied
        flags_after = await get_message_flags(upstream_imap_cfg, uid)
        assert r"\Deleted" in flags_after or "\\Deleted" in flags_after, (
            f"\\Deleted flag was NOT applied upstream after approval. flags={flags_after!r}"
        )

    finally:
        if uid is not None:
            await delete_message_by_uid(upstream_imap_cfg, uid)


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_upstream_flag_applied_after_approve(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    After approving an IMAP STORE op, the flag must be applied to the
    upstream server. Separated from the staging gate test so each concern
    has a single focused assertion.
    """
    subject = f"Archive Upstream Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    auth_headers = e2e_setup["auth_headers"]
    imap_proxy_host = e2e_setup["imap_host"]
    imap_proxy_port = e2e_setup["imap_port"]
    proxy_agent = e2e_setup["proxy_agent_auth"]["imap"]
    user = proxy_agent["username"]
    password = proxy_agent["token"]

    uid: Optional[int] = None

    try:
        await send_direct_smtp(upstream_smtp_cfg, subject)
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=_TIMEOUT)
        assert uid is not None, f"Setup message {subject!r} did not arrive"

        _, op_id = await _proxy_imap_store_flags(
            imap_proxy_host, imap_proxy_port, user, password, uid, r"\Seen"
        )
        assert op_id, "No op_id in STAGED response"

        approve_resp = await api_client.post(
            f"/api/v1/operations/{op_id}/approve",
            headers=auth_headers,
        )
        assert approve_resp.status_code == 200

        flags = await get_message_flags(upstream_imap_cfg, uid)
        assert r"\Seen" in flags or "\\Seen" in flags, (
            f"\\Seen not applied upstream after approval. flags={flags!r}"
        )
    finally:
        if uid is not None:
            await delete_message_by_uid(upstream_imap_cfg, uid)


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_archive_staging_gate(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    Focused staging gate test: proves the proxy does NOT forward STORE to upstream.

    This test must PASS in Milestone 1.0. It does not attempt to verify
    post-approval upstream execution.
    """
    subject = f"Archive Gate Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    imap_proxy_host = e2e_setup["imap_host"]
    imap_proxy_port = e2e_setup["imap_port"]
    proxy_agent = e2e_setup["proxy_agent_auth"]["imap"]
    user = proxy_agent["username"]
    password = proxy_agent["token"]

    uid: Optional[int] = None

    try:
        # Send via direct SMTP so we have a message to work with
        await send_direct_smtp(upstream_smtp_cfg, subject)
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=_TIMEOUT)
        assert uid is not None, f"Setup message {subject!r} did not arrive"

        # STORE \\Deleted via proxy
        store_response, op_id = await _proxy_imap_store_flags(
            imap_proxy_host, imap_proxy_port, user, password, uid, r"\Deleted"
        )
        assert "STAGED" in store_response.upper(), (
            f"Proxy did not return STAGED: {store_response!r}"
        )
        assert op_id, "No op_id in STAGED response"

        # KEY: message must still exist, flag must NOT be applied yet
        still_present = await check_message_exists(upstream_imap_cfg, uid)
        assert still_present, (
            "Message disappeared from upstream immediately after proxy STORE — "
            "proxy forwarded the command instead of staging it"
        )

        flags_before = await get_message_flags(upstream_imap_cfg, uid)
        deleted_flags = [f for f in flags_before if "Deleted" in f]
        assert not deleted_flags, (
            f"\\Deleted flag was applied before approval! flags={flags_before!r}"
        )

        # Verify op is visible and pending via API (auth required)
        op_resp = await api_client.get(
            f"/api/v1/operations/{op_id}",
            headers=e2e_setup["auth_headers"],
        )
        assert op_resp.status_code == 200
        assert op_resp.json()["status"] == "pending"
        assert op_resp.json()["protocol"] == "imap"

    finally:
        if uid is not None:
            await delete_message_by_uid(upstream_imap_cfg, uid)


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_archive_rejected_state_unchanged(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    After rejecting a STORE operation, the message must remain in INBOX
    with no flag changes applied.
    """
    subject = f"Archive Reject Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    imap_proxy_host = e2e_setup["imap_host"]
    imap_proxy_port = e2e_setup["imap_port"]
    proxy_agent = e2e_setup["proxy_agent_auth"]["imap"]
    user = proxy_agent["username"]
    password = proxy_agent["token"]

    uid: Optional[int] = None

    try:
        # Step 1: Send message via direct SMTP
        await send_direct_smtp(upstream_smtp_cfg, subject)
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=_TIMEOUT)
        assert uid is not None, f"Setup message {subject!r} did not arrive"

        # Step 2: STORE \\Deleted via IMAP proxy
        store_response, op_id = await _proxy_imap_store_flags(
            imap_proxy_host, imap_proxy_port, user, password, uid, r"\Deleted"
        )
        assert "STAGED" in store_response.upper()
        assert op_id

        # Step 3: Reject via API (Bearer auth required)
        reject_resp = await api_client.post(
            f"/api/v1/operations/{op_id}/reject",
            headers=e2e_setup["auth_headers"],
        )
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "rejected"

        # Step 4: Verify message still in INBOX, no \\Deleted flag
        still_present = await check_message_exists(upstream_imap_cfg, uid)
        assert still_present, "Message disappeared after rejection"

        flags = await get_message_flags(upstream_imap_cfg, uid)
        deleted_flags = [f for f in flags if "Deleted" in f]
        assert not deleted_flags, (
            f"\\Deleted flag present after rejection! flags={flags!r}"
        )

    finally:
        if uid is not None:
            await delete_message_by_uid(upstream_imap_cfg, uid)
