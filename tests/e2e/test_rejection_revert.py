"""
E2E test: full rejection revert cycle (Milestone 1.2 — Issue #27).

Verifies the complete rejection loop:

  1. Send a message directly to INBOX (setup)
  2. STORE +FLAGS (\\Seen) via IMAP proxy → OK [STAGED]
     - Local state DB: \\Seen applied optimistically
  3. Reject via API
     - Local state DB: flags restored to pre-op state
     - pending_reverts queued
  4. AI sends NOOP via proxy
     - Proxy detects pending_reverts, injects:
         * N FETCH (UID M FLAGS ())
       into the NOOP response stream
  5. Assert the injected FETCH line is present after NOOP OK

From the AI's perspective: another client removed the flag between commands.
Standard IMAP sync — no IMAP extensions required.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import pytest
import pytest_asyncio

from tests.e2e.helpers import (
    delete_message_by_uid,
    send_direct_smtp,
    wait_for_message,
)


_TIMEOUT = 20.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _imap_raw_connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


async def _read_line(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


async def _read_until_tagged(
    reader: asyncio.StreamReader,
    tag: str,
    timeout: float = _TIMEOUT,
) -> list[str]:
    """Read lines until we see a tagged OK/NO/BAD for the given tag."""
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


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_rejection_injects_unsolicited_fetch_on_noop(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    Full rejection revert cycle:
      1. Send a message → wait for arrival → get UID
      2. STORE +FLAGS (\\Seen) via proxy → STAGED → op_id
      3. Reject via API
      4. NOOP via proxy → response must include unsolicited FETCH with empty FLAGS
      5. Cleanup
    """
    subject = f"Revert Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    imap_host = e2e_setup["imap_host"]
    imap_port = e2e_setup["imap_port"]
    user = upstream_imap_cfg["user"]
    password = upstream_imap_cfg["password"]

    uid: Optional[int] = None
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None

    try:
        # Step 1: Send a message directly (bypass proxy) and wait for it to arrive
        await send_direct_smtp(upstream_smtp_cfg, subject)
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=_TIMEOUT)
        assert uid is not None, f"Setup message {subject!r} did not arrive within {_TIMEOUT}s"

        # Step 2: Connect to IMAP proxy and SELECT INBOX
        reader, writer = await _imap_raw_connect(imap_host, imap_port)
        await _read_line(reader)  # greeting

        writer.write(b"T01 LOGIN " + user.encode() + b" " + password.encode() + b"\r\n")
        await writer.drain()
        login_resp = await _read_until_tagged(reader, "T01")
        assert any("OK" in l.upper() for l in login_resp), f"LOGIN failed: {login_resp}"

        writer.write(b"T02 SELECT INBOX\r\n")
        await writer.drain()
        select_resp = await _read_until_tagged(reader, "T02")
        assert any("OK" in l.upper() for l in select_resp), f"SELECT failed: {select_resp}"

        # Brief settle to let the u2c pump sync the SELECT response to the state DB
        # so folder_id is set in session before the STORE snapshot runs.
        await asyncio.sleep(0.05)

        # Step 3: STORE +FLAGS (\\Seen) via proxy → should return STAGED
        store_cmd = f"T03 UID STORE {uid} +FLAGS (\\Seen)\r\n"
        writer.write(store_cmd.encode())
        await writer.drain()
        store_resp = await _read_until_tagged(reader, "T03")
        store_text = " ".join(store_resp)
        assert "STAGED" in store_text.upper(), (
            f"Expected STAGED in STORE response, got: {store_resp!r}"
        )

        # Extract op_id from response
        import re as _re
        op_id_match = _re.search(r"(op_[A-Za-z0-9]+)", store_text)
        assert op_id_match, f"Could not extract op_id from: {store_text!r}"
        op_id = op_id_match.group(1)

        # Step 4: Reject via API (Bearer auth required)
        reject_resp = await api_client.post(
            f"/api/v1/operations/{op_id}/reject",
            headers=e2e_setup["auth_headers"],
        )
        assert reject_resp.status_code == 200, f"Reject failed: {reject_resp.text}"
        assert reject_resp.json()["status"] == "rejected"

        # Step 5: Send NOOP via proxy → response must include unsolicited FETCH
        # with the true (empty) flags — proving the proxy injected the revert.
        writer.write(b"T04 NOOP\r\n")
        await writer.drain()
        noop_lines = await _read_until_tagged(reader, "T04")

        # Look for "* N FETCH (UID <uid> FLAGS (...))" in response lines
        fetch_lines = [l for l in noop_lines if "FETCH" in l.upper() and str(uid) in l]
        assert fetch_lines, (
            f"Expected unsolicited FETCH for UID {uid} in NOOP response, "
            f"got lines: {noop_lines!r}"
        )

        # The injected FETCH must show empty flags (\\Seen was reverted)
        fetch_line = fetch_lines[0].upper()
        assert "FLAGS ()" in fetch_line or "FLAGS ( )" in fetch_line, (
            f"Expected empty FLAGS in injected FETCH, got: {fetch_lines[0]!r}"
        )

        # Step 6: LOGOUT cleanly
        writer.write(b"T05 LOGOUT\r\n")
        await writer.drain()
        await _read_until_tagged(reader, "T05")

    finally:
        if writer:
            await _close(writer)
        if uid is not None:
            try:
                await delete_message_by_uid(upstream_imap_cfg, uid)
            except Exception:
                pass


@pytest.mark.asyncio(loop_scope="session")
async def test_rejection_revert_does_not_fire_for_write_commands(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    Revert injection must NOT fire on write/blocked command responses.
    This test stages a rejection, then sends a second STORE (which returns
    STAGED locally without going upstream). Verify the injected FETCH does
    NOT appear in the STORE response — only in the subsequent NOOP.
    """
    subject = f"Revert Guard Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    imap_host = e2e_setup["imap_host"]
    imap_port = e2e_setup["imap_port"]
    user = upstream_imap_cfg["user"]
    password = upstream_imap_cfg["password"]

    uid: Optional[int] = None
    reader: Optional[asyncio.StreamReader] = None
    writer: Optional[asyncio.StreamWriter] = None

    try:
        await send_direct_smtp(upstream_smtp_cfg, subject)
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=_TIMEOUT)
        assert uid is not None, f"Setup message {subject!r} did not arrive"

        reader, writer = await _imap_raw_connect(imap_host, imap_port)
        await _read_line(reader)

        writer.write(b"U01 LOGIN " + user.encode() + b" " + password.encode() + b"\r\n")
        await writer.drain()
        await _read_until_tagged(reader, "U01")

        writer.write(b"U02 SELECT INBOX\r\n")
        await writer.drain()
        await _read_until_tagged(reader, "U02")
        await asyncio.sleep(0.05)  # let SELECT sync settle

        # Stage op 1 and reject it (Bearer auth required)
        writer.write(f"U03 UID STORE {uid} +FLAGS (\\Seen)\r\n".encode())
        await writer.drain()
        store1 = await _read_until_tagged(reader, "U03")
        import re as _re
        m = _re.search(r"(op_[A-Za-z0-9]+)", " ".join(store1))
        assert m, "No op_id in first STORE response"
        await api_client.post(
            f"/api/v1/operations/{m.group(1)}/reject",
            headers=e2e_setup["auth_headers"],
        )

        # Stage op 2 (pending revert is now queued from op 1)
        writer.write(f"U04 UID STORE {uid} +FLAGS (\\Flagged)\r\n".encode())
        await writer.drain()
        store2 = await _read_until_tagged(reader, "U04")
        # STORE response must NOT contain an injected FETCH
        assert not any("FETCH" in l.upper() for l in store2), (
            f"Unexpected FETCH injection in STORE response: {store2!r}"
        )

        # Now NOOP — SHOULD contain the injected FETCH
        writer.write(b"U05 NOOP\r\n")
        await writer.drain()
        noop_lines = await _read_until_tagged(reader, "U05")
        fetch_lines = [l for l in noop_lines if "FETCH" in l.upper() and str(uid) in l]
        assert fetch_lines, (
            f"Expected injected FETCH in NOOP after rejection, got: {noop_lines!r}"
        )

        writer.write(b"U06 LOGOUT\r\n")
        await writer.drain()
        await _read_until_tagged(reader, "U06")

    finally:
        if writer:
            await _close(writer)
        if uid is not None:
            try:
                await delete_message_by_uid(upstream_imap_cfg, uid)
            except Exception:
                pass
