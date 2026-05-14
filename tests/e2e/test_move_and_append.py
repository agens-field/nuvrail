"""
E2E tests: IMAP proxy staging for MOVE and APPEND operations.

These tests fill the coverage gap left by test_archive_and_verify.py (STORE)
and test_send_and_verify.py (SMTP send).  Together the four test modules
cover every intercept-and-stage path in the IMAP proxy:

  STORE flags  ←→ test_archive_and_verify.py
  MOVE         ←→ this file
  APPEND       ←→ this file
  SMTP send    ←→ test_send_and_verify.py
  Rejection    ←→ test_reject_scenario.py, test_rejection_revert.py

Test topology
─────────────
  test
   ├─► direct SMTP  (plant a message so MOVE has something to act on)
   ├─► IMAP proxy   (intercept MOVE / APPEND → STAGED)
   ├─► direct IMAP  (ground truth: verify move/append executed on upstream)
   └─► FastAPI API  (approve the staged operation)

These tests require live network access.  Credentials come from .env
via the e2e conftest (NUVRAIL_TEST_IMAP_HOST / NUVRAIL_TEST_IMAP_USER /
NUVRAIL_TEST_IMAP_PASS, etc.).  They are skipped cleanly when env vars
are absent, so they never break the offline unit-test run.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import aioimaplib
import pytest

from tests.e2e.helpers import (
    delete_message_by_uid,
    send_direct_smtp,
    wait_for_message,
)

_TIMEOUT = 20  # seconds — generous for network + IMAP relay latency


# ---------------------------------------------------------------------------
# Low-level IMAP proxy helpers (raw asyncio — proxy speaks plain TCP)
# ---------------------------------------------------------------------------

async def _raw_connect(
    host: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


async def _raw_read_line(
    reader: asyncio.StreamReader, timeout: float = _TIMEOUT
) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


async def _raw_read_until_tagged(
    reader: asyncio.StreamReader, tag: str, timeout: float = _TIMEOUT
) -> list[str]:
    """Read lines until the tagged response for *tag* arrives."""
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for tagged response {tag!r}")
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


async def _raw_send(writer: asyncio.StreamWriter, cmd: str) -> None:
    writer.write((cmd + "\r\n").encode())
    await writer.drain()


async def _raw_close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.write(b"X99 LOGOUT\r\n")
        await writer.drain()
    except OSError:
        pass
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass


def _extract_op_id(lines: list[str]) -> Optional[str]:
    """Pull op_XXXXXX from any line in the response."""
    for line in lines:
        m = re.search(r"(op_[A-Za-z0-9]+)", line)
        if m:
            return m.group(1)
    return None


async def _imap_proxy_login_and_select(
    imap_host: str,
    imap_port: int,
    agent_user: str,
    agent_token: str,
    mailbox: str = "INBOX",
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int]:
    """
    Open a raw TCP connection to the IMAP proxy, LOGIN, SELECT mailbox.

    Returns (reader, writer, next_tag_counter).  Caller is responsible for
    closing writer with _raw_close().

    Tag counter starts at 1; caller should increment after each command.
    """
    reader, writer = await _raw_connect(imap_host, imap_port)
    # Consume greeting
    await _raw_read_line(reader)

    # LOGIN
    await _raw_send(writer, f"A01 LOGIN {agent_user} {agent_token}")
    login_lines = await _raw_read_until_tagged(reader, "A01")
    assert any("OK" in l.upper() for l in login_lines), (
        f"LOGIN failed: {login_lines!r}"
    )

    # SELECT mailbox
    await _raw_send(writer, f"A02 SELECT {mailbox}")
    select_lines = await _raw_read_until_tagged(reader, "A02")
    assert any("OK" in l.upper() for l in select_lines), (
        f"SELECT {mailbox} failed: {select_lines!r}"
    )

    return reader, writer, 3  # next tag counter


async def _uid_in_folder(imap_config: dict, folder: str, uid: int) -> bool:
    """Return True if *uid* is present in *folder* on the upstream server."""
    client = aioimaplib.IMAP4_SSL(
        host=imap_config["host"],
        port=imap_config["port"],
    )
    try:
        await client.wait_hello_from_server()
        await client.login(imap_config["user"], imap_config["password"])
        await client.select(folder)
        status, data = await client.uid_search("ALL")
        if status != "OK":
            return False
        uids_str = data[0].decode().strip() if data[0] else ""
        return str(uid) in uids_str.split()
    except Exception:
        return False
    finally:
        try:
            await client.logout()
        except Exception:
            pass


async def _search_all_uids_in_folder(imap_config: dict, folder: str) -> list[int]:
    """Return all UIDs present in *folder* on the upstream server."""
    client = aioimaplib.IMAP4_SSL(
        host=imap_config["host"],
        port=imap_config["port"],
    )
    try:
        await client.wait_hello_from_server()
        await client.login(imap_config["user"], imap_config["password"])
        await client.select(folder)
        status, data = await client.uid_search("ALL")
        if status != "OK":
            return []
        uids_str = data[0].decode().strip() if data[0] else ""
        return [int(u) for u in uids_str.split() if u]
    finally:
        try:
            await client.logout()
        except Exception:
            pass


async def _delete_from_folder(imap_config: dict, folder: str, uid: int) -> None:
    """Delete a message by UID from *folder* via direct IMAP."""
    client = aioimaplib.IMAP4_SSL(
        host=imap_config["host"],
        port=imap_config["port"],
    )
    try:
        await client.wait_hello_from_server()
        await client.login(imap_config["user"], imap_config["password"])
        await client.select(folder)
        await client.uid("store", str(uid), "+FLAGS", r"(\Deleted)")
        await client.expunge()
    finally:
        try:
            await client.logout()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_imap_move_staged_then_approved(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
) -> None:
    """
    Full IMAP MOVE approval flow:

      Direct SMTP ──► plant a message in INBOX
      IMAP proxy  ──► UID MOVE {uid} Trash → STAGED, op_id returned
      API         ──► GET /operations → op is pending
      API         ──► POST /operations/{op_id}/approve → executed
      Direct IMAP ──► message no longer in INBOX, now in Trash

    Cleanup: delete from Trash.

    This proves the proxy intercepts MOVE, holds it in staging, and
    executes against the real upstream only after explicit approval.
    """
    subject = f"E2E MOVE Test {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    imap_host = e2e_setup["imap_host"]
    imap_port = e2e_setup["imap_port"]
    agent_user = e2e_setup["proxy_agent_auth"]["imap"]["username"]
    agent_token = e2e_setup["proxy_agent_auth"]["imap"]["token"]

    trash_uid: Optional[int] = None

    try:
        # ── Step 1: Plant a message in INBOX via direct SMTP ──────────────
        await send_direct_smtp(upstream_smtp_cfg, subject)
        uid = await wait_for_message(upstream_imap_cfg, subject, timeout=15.0)
        assert uid is not None, f"Setup message {subject!r} did not arrive in INBOX within 15s"

        # ── Step 2: Issue UID MOVE via IMAP proxy ─────────────────────────
        reader, writer, tag_n = await _imap_proxy_login_and_select(
            imap_host, imap_port, agent_user, agent_token, "INBOX"
        )
        try:
            tag = f"A{tag_n:02d}"
            await _raw_send(writer, f"{tag} UID MOVE {uid} Trash")
            move_lines = await _raw_read_until_tagged(reader, tag)
        finally:
            await _raw_close(writer)

        # Response must be a tagged OK with [STAGED] and an op_id
        tagged_line = next(
            (l for l in move_lines if l.upper().startswith(tag.upper() + " OK")), None
        )
        assert tagged_line is not None, (
            f"No tagged OK in MOVE response: {move_lines!r}"
        )
        assert "STAGED" in tagged_line.upper(), (
            f"Expected [STAGED] in MOVE response, got: {move_lines!r}"
        )
        op_id = _extract_op_id(move_lines)
        assert op_id is not None, f"Could not extract op_id from: {move_lines!r}"

        # ── Step 3: Verify op is pending in API ───────────────────────────
        resp = await api_client.get(
            "/api/v1/operations?status=pending",
            headers=e2e_setup["auth_headers"],
        )
        assert resp.status_code == 200
        pending_ids = [op["id"] for op in resp.json()["operations"]]
        assert op_id in pending_ids, (
            f"op_id {op_id!r} not in pending ops: {pending_ids}"
        )

        # ── Step 4: Approve via API ────────────────────────────────────────
        approve_resp = await api_client.post(
            f"/api/v1/operations/{op_id}/approve",
            headers=e2e_setup["auth_headers"],
        )
        assert approve_resp.status_code == 200, (
            f"Approve failed: {approve_resp.status_code} {approve_resp.text}"
        )
        assert approve_resp.json()["status"] == "executed"

        # ── Step 5: Verify message is in Trash (not INBOX) ────────────────
        # Brief poll — MOVE propagation on upstream may take a moment
        deadline = time.monotonic() + 10.0
        in_trash = False
        while time.monotonic() < deadline:
            in_trash = await _uid_in_folder(upstream_imap_cfg, "Trash", uid)
            if in_trash:
                break
            await asyncio.sleep(1.0)

        assert in_trash, (
            f"Message uid={uid} did not appear in Trash within 10s after approval"
        )
        trash_uid = uid

    finally:
        # ── Cleanup ───────────────────────────────────────────────────────
        if trash_uid is not None:
            await _delete_from_folder(upstream_imap_cfg, "Trash", trash_uid)


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_append_to_drafts_staged_then_approved(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
) -> None:
    """
    Full IMAP APPEND approval flow:

      IMAP proxy  ──► APPEND "Drafts" (\\Draft) {size}\\r\\n{body} → STAGED
      API         ──► GET /operations → op is pending
      API         ──► POST /operations/{op_id}/approve → executed
      Direct IMAP ──► message now present in Drafts

    Cleanup: delete the draft from Drafts.

    This proves the proxy intercepts APPEND literals, stages them, and
    writes to the upstream Drafts folder only on explicit approval.
    """
    subject = f"E2E Draft {time.time():.0f}"
    api_client = e2e_setup["api_client"]
    imap_host = e2e_setup["imap_host"]
    imap_port = e2e_setup["imap_port"]
    agent_user = e2e_setup["proxy_agent_auth"]["imap"]["username"]
    agent_token = e2e_setup["proxy_agent_auth"]["imap"]["token"]

    draft_uid: Optional[int] = None

    try:
        # ── Step 1: Snapshot Drafts UIDs before the test ──────────────────
        uids_before = set(
            await _search_all_uids_in_folder(upstream_imap_cfg, "Drafts")
        )

        # ── Step 2: APPEND via IMAP proxy ─────────────────────────────────
        # Build a minimal RFC 2822 message
        draft_body = (
            f"From: test@nuvrail.com\r\n"
            f"To: test@nuvrail.com\r\n"
            f"Subject: {subject}\r\n"
            f"Content-Type: text/plain\r\n"
            f"\r\n"
            f"Draft body for Nuvrail e2e test.\r\n"
        ).encode("utf-8")
        literal_size = len(draft_body)

        reader, writer = await _raw_connect(imap_host, imap_port)
        try:
            # Consume greeting
            await _raw_read_line(reader)

            # LOGIN
            await _raw_send(writer, f"A01 LOGIN {agent_user} {agent_token}")
            login_lines = await _raw_read_until_tagged(reader, "A01")
            assert any("OK" in l.upper() for l in login_lines), (
                f"LOGIN failed: {login_lines!r}"
            )

            # APPEND command — proxy will respond with continuation (+ ...)
            tag = "A02"
            writer.write(
                f'{tag} APPEND "Drafts" (\\Draft) {{{literal_size}}}\r\n'.encode()
            )
            await writer.drain()

            # Read continuation response ("+ Ready for literal data")
            continuation = await _raw_read_line(reader)
            assert continuation.startswith("+"), (
                f"Expected continuation, got: {continuation!r}"
            )

            # Send literal body followed by CRLF
            writer.write(draft_body + b"\r\n")
            await writer.drain()

            # Read tagged response
            append_lines = await _raw_read_until_tagged(reader, tag)
        finally:
            await _raw_close(writer)

        # Response must be OK [STAGED] with an op_id
        tagged_line = next(
            (l for l in append_lines if l.upper().startswith(tag.upper() + " OK")), None
        )
        assert tagged_line is not None, (
            f"No tagged OK in APPEND response: {append_lines!r}"
        )
        assert "STAGED" in tagged_line.upper(), (
            f"Expected [STAGED] in APPEND response, got: {append_lines!r}"
        )
        op_id = _extract_op_id(append_lines)
        assert op_id is not None, f"Could not extract op_id from: {append_lines!r}"

        # ── Step 3: Verify op is pending in API ───────────────────────────
        resp = await api_client.get(
            "/api/v1/operations?status=pending",
            headers=e2e_setup["auth_headers"],
        )
        assert resp.status_code == 200
        pending_ids = [op["id"] for op in resp.json()["operations"]]
        assert op_id in pending_ids, (
            f"op_id {op_id!r} not in pending ops: {pending_ids}"
        )

        # ── Step 4: Approve via API ────────────────────────────────────────
        approve_resp = await api_client.post(
            f"/api/v1/operations/{op_id}/approve",
            headers=e2e_setup["auth_headers"],
        )
        assert approve_resp.status_code == 200, (
            f"Approve failed: {approve_resp.status_code} {approve_resp.text}"
        )
        assert approve_resp.json()["status"] == "executed"

        # ── Step 5: Verify draft appeared in Drafts ───────────────────────
        # Poll for up to 10s for the new draft UID to appear
        deadline = time.monotonic() + 10.0
        new_uid: Optional[int] = None
        while time.monotonic() < deadline:
            uids_after = set(
                await _search_all_uids_in_folder(upstream_imap_cfg, "Drafts")
            )
            new_uids = uids_after - uids_before
            if new_uids:
                new_uid = max(new_uids)  # most recent
                break
            await asyncio.sleep(1.0)

        assert new_uid is not None, (
            "No new message appeared in Drafts within 10s after APPEND approval"
        )
        draft_uid = new_uid

    finally:
        # ── Cleanup ───────────────────────────────────────────────────────
        if draft_uid is not None:
            await _delete_from_folder(upstream_imap_cfg, "Drafts", draft_uid)
