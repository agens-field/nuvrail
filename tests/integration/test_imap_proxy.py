"""
Integration tests for Milestone 0.1: IMAP proxy dumb byte pipe.

Test topology:

  test client (asyncio.open_connection, plain TCP)
        │
        ▼
  proxy server (gateway.proxy — local, plain)
        │  SSL/TLS
        ▼
  upstream IMAP (blizzard.mxrouting.net:993)

Each test starts the proxy as a background asyncio task, binds to an
ephemeral port, then connects to it as a plain TCP client.
Credentials are loaded from .env via conftest.py.

Auth notes (post-auth milestone):
  The proxy now verifies IMAP LOGIN credentials against the agent_credentials
  table. Integration tests create a temp DB, register the upstream credentials
  as an agent credential, and use the resulting agent_username/agent_token for
  IMAP LOGIN instead of upstream credentials directly.
"""

import asyncio
import os
import re
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

import gateway.state_db as _state_db_mod
from gateway.proxy import handle_client
from gateway.state_db import get_db, init_db

# Ensure .env is loaded even when running this file directly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

_TIMEOUT = 15  # seconds — generous for CI and slow upstreams


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def imap_integration_db(upstream_imap_config: dict) -> tuple[Path, str, str]:
    """Create a temp DB, register upstream creds as an agent credential.

    Returns (db_path, agent_username, agent_token_plain) so tests can use
    agent_username/agent_token as IMAP LOGIN credentials.
    """
    from api.auth import generate_token, hash_agent_token

    tmpdir = tempfile.mkdtemp()
    try:
        db_path = Path(tmpdir) / "imap_integration.db"
        await init_db(db_path)

        agent_username = "nuvrail_imap_test"
        agent_token_plain = generate_token()
        hashed = hash_agent_token(agent_token_plain)
        now = int(time.time())

        # Create a stub user and agent credential
        async with get_db(db_path) as db:
            cur = await db.execute(
                "INSERT INTO users (email, hashed_password, api_token, created_at) VALUES (?, ?, ?, ?)",
                ("imap_test@test.com", "x", "imap_test_token", now),
            )
            user_id = cur.lastrowid
            await db.execute(
                """INSERT INTO agent_credentials
                   (user_id, label, agent_username, hashed_token,
                    upstream_host, upstream_imap_port, upstream_smtp_port,
                    upstream_user, upstream_password, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, "test", agent_username, hashed,
                    upstream_imap_config["host"],
                    upstream_imap_config["port"],
                    587,
                    upstream_imap_config["user"],
                    upstream_imap_config["password"],
                    now,
                ),
            )
            await db.commit()

        # Patch the module-level DB_PATH so handle_client uses this test DB
        original_db_path = _state_db_mod.DB_PATH
        _state_db_mod.DB_PATH = db_path
        yield db_path, agent_username, agent_token_plain
        _state_db_mod.DB_PATH = original_db_path
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest_asyncio.fixture(loop_scope="session")
async def proxy_server(imap_integration_db: tuple):
    """Start the proxy on an ephemeral port; yield (host, port, agent_username, agent_token); tear down."""
    db_path, agent_username, agent_token = imap_integration_db
    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()
    task = asyncio.create_task(server.serve_forever())
    yield host, port, agent_username, agent_token
    server.close()
    await server.wait_closed()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a plain TCP connection to the proxy."""
    return await asyncio.open_connection(host, port)


async def _read_line(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> str:
    """Read one CRLF-terminated line, decoded as UTF-8."""
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


async def _read_until_ok_or_no(
    reader: asyncio.StreamReader,
    tag: str,
    timeout: float = _TIMEOUT,
) -> list[str]:
    """Read lines until we see a tagged OK/NO/BAD response."""
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for tagged response to tag={tag!r}")
        line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(decoded)
        upper = decoded.upper()
        if upper.startswith(tag.upper() + " OK") or upper.startswith(tag.upper() + " NO") or upper.startswith(tag.upper() + " BAD"):
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
async def test_proxy_forwards_greeting(proxy_server: tuple) -> None:
    """Proxy must forward the upstream IMAP greeting (contains OK or PREAUTH)."""
    host, port, _agent_user, _agent_pass = proxy_server
    reader, writer = await _connect(host, port)
    try:
        greeting = await _read_line(reader)
        assert "OK" in greeting.upper() or "PREAUTH" in greeting.upper(), (
            f"Expected greeting to contain OK or PREAUTH, got: {greeting!r}"
        )
    finally:
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_login_valid_credentials(
    proxy_server: tuple,
    upstream_imap_config: dict,
) -> None:
    """LOGIN with agent credentials must return a tagged OK.

    The proxy verifies agent_username + agent_token against agent_credentials,
    then forwards LOGIN to upstream using stored upstream credentials.
    """
    host, port, agent_user, agent_token = proxy_server
    if not upstream_imap_config.get("host"):
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set")
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        writer.write(f"a001 LOGIN {agent_user} {agent_token}\r\n".encode())
        await writer.drain()
        lines = await _read_until_ok_or_no(reader, "a001")
        last = lines[-1].upper()
        assert "A001 OK" in last, f"Expected OK after LOGIN, got: {lines!r}"
    finally:
        writer.write(b"a099 LOGOUT\r\n")
        try:
            await writer.drain()
        except OSError:
            pass
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_login_bad_credentials(proxy_server: tuple) -> None:
    """LOGIN with unknown agent credentials must be rejected by the proxy."""
    host, port, _agent_user, _agent_pass = proxy_server
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        writer.write(b"a002 LOGIN unknown_agent wrongtoken\r\n")
        await writer.drain()
        # Proxy sends BYE + NO and closes; read both lines
        line1 = await _read_line(reader)
        line2 = await _read_line(reader)
        combined = (line1 + " " + line2).upper()
        assert "BYE" in combined or "NO" in combined, (
            f"Expected BYE or NO after bad LOGIN, got: {line1!r} {line2!r}"
        )
    finally:
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_select_inbox_after_login(
    proxy_server: tuple,
    upstream_imap_config: dict,
) -> None:
    """After a successful LOGIN, SELECT INBOX must return a tagged OK."""
    host, port, agent_user, agent_token = proxy_server
    if not upstream_imap_config.get("host"):
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set")
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        writer.write(f"a003 LOGIN {agent_user} {agent_token}\r\n".encode())
        await writer.drain()
        await _read_until_ok_or_no(reader, "a003")

        writer.write(b"a004 SELECT INBOX\r\n")
        await writer.drain()
        lines = await _read_until_ok_or_no(reader, "a004")
        last = lines[-1].upper()
        assert "A004 OK" in last, f"Expected OK after SELECT INBOX, got: {lines!r}"
    finally:
        writer.write(b"a099 LOGOUT\r\n")
        try:
            await writer.drain()
        except OSError:
            pass
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_logout_clean(
    proxy_server: tuple,
    upstream_imap_config: dict,
) -> None:
    """LOGOUT after a successful login must complete cleanly (tagged OK)."""
    host, port, agent_user, agent_token = proxy_server
    if not upstream_imap_config.get("host"):
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set")
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        writer.write(f"a005 LOGIN {agent_user} {agent_token}\r\n".encode())
        await writer.drain()
        await _read_until_ok_or_no(reader, "a005")

        writer.write(b"a006 LOGOUT\r\n")
        await writer.drain()
        lines = await _read_until_ok_or_no(reader, "a006")
        last = lines[-1].upper()
        assert "A006 OK" in last, f"Expected OK after LOGOUT, got: {lines!r}"
    finally:
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_non_uid_store_rejected(
    proxy_server: tuple,
    upstream_imap_config: dict,
) -> None:
    """Non-UID STORE must be rejected with NO [CLIENTBUG] — sequence numbers
    are unsafe for deferred approval because they shift when messages move."""
    host, port, agent_user, agent_token = proxy_server
    if not upstream_imap_config.get("host"):
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set")
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        writer.write(f"b001 LOGIN {agent_user} {agent_token}\r\n".encode())
        await writer.drain()
        await _read_until_ok_or_no(reader, "b001")

        writer.write(b"b002 SELECT INBOX\r\n")
        await writer.drain()
        await _read_until_ok_or_no(reader, "b002")

        # Non-UID STORE (sequence number) — must be rejected
        writer.write(b"b003 STORE 1 +FLAGS (\\Seen)\r\n")
        await writer.drain()
        lines = await _read_until_ok_or_no(reader, "b003")
        last = lines[-1].upper()
        assert "B003 NO" in last, (
            f"Expected NO for non-UID STORE, got: {lines!r}"
        )
        assert "CLIENTBUG" in last, (
            f"Expected [CLIENTBUG] in response, got: {lines!r}"
        )

        # UID STORE must still be accepted (returns STAGED)
        writer.write(b"b004 UID STORE 1 +FLAGS (\\Seen)\r\n")
        await writer.drain()
        lines = await _read_until_ok_or_no(reader, "b004")
        last = lines[-1].upper()
        assert "B004 OK" in last, (
            f"Expected OK [STAGED] for UID STORE, got: {lines!r}"
        )
        assert "STAGED" in " ".join(lines).upper(), (
            f"Expected STAGED in UID STORE response, got: {lines!r}"
        )
    finally:
        writer.write(b"b099 LOGOUT\r\n")
        try:
            await writer.drain()
        except OSError:
            pass
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_select_exists_adjusted_for_pending_move(
    proxy_server: tuple,
    upstream_imap_config: dict,
) -> None:
    """After staging a MOVE, SELECT EXISTS count must reflect the virtual removal.

    The agent should see one fewer message than upstream reports — the moved
    message is pending approval but already 'gone' from the agent's view.
    """
    host, port, agent_user, agent_token = proxy_server
    if not upstream_imap_config.get("host"):
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set")

    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # greeting

        writer.write(f"c001 LOGIN {agent_user} {agent_token}\r\n".encode())
        await writer.drain()
        await _read_until_ok_or_no(reader, "c001")

        # SELECT to get baseline EXISTS count
        writer.write(b"c002 SELECT INBOX\r\n")
        await writer.drain()
        select_lines = await _read_until_ok_or_no(reader, "c002")

        baseline_exists: int | None = None
        for line in select_lines:
            m = re.search(r"^\* (\d+) EXISTS", line, re.IGNORECASE)
            if m:
                baseline_exists = int(m.group(1))
                break

        if baseline_exists is None or baseline_exists == 0:
            pytest.skip("INBOX has no messages — cannot test EXISTS adjustment")

        import asyncio as _asyncio
        await _asyncio.sleep(0.05)  # let SELECT sync settle

        # Stage a MOVE for the first known UID
        # First, fetch a UID from INBOX so we have something to move
        writer.write(b"c003 UID SEARCH ALL\r\n")
        await writer.drain()
        search_lines = await _read_until_ok_or_no(reader, "c003")
        uids: list[int] = []
        for line in search_lines:
            m = re.match(r"^\* SEARCH (.*)", line, re.IGNORECASE)
            if m:
                uids = [int(u) for u in m.group(1).split() if u.strip().isdigit()]
                break

        if not uids:
            pytest.skip("No UIDs found in INBOX")

        move_uid = uids[0]

        # Stage the MOVE
        writer.write(f"c004 UID MOVE {move_uid} Archive\r\n".encode())
        await writer.drain()
        move_lines = await _read_until_ok_or_no(reader, "c004")
        assert any("STAGED" in l.upper() for l in move_lines), (
            f"Expected STAGED in MOVE response, got: {move_lines!r}"
        )

        # Re-SELECT INBOX — EXISTS count must be reduced by 1
        writer.write(b"c005 SELECT INBOX\r\n")
        await writer.drain()
        reselect_lines = await _read_until_ok_or_no(reader, "c005")

        adjusted_exists: int | None = None
        for line in reselect_lines:
            m = re.search(r"^\* (\d+) EXISTS", line, re.IGNORECASE)
            if m:
                adjusted_exists = int(m.group(1))
                break

        assert adjusted_exists is not None, "No EXISTS line in re-SELECT response"
        assert adjusted_exists == baseline_exists - 1, (
            f"Expected EXISTS={baseline_exists - 1} after staging MOVE, "
            f"got {adjusted_exists} (baseline was {baseline_exists})"
        )

    finally:
        try:
            writer.write(b"c099 LOGOUT\r\n")
            await writer.drain()
        except OSError:
            pass
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_uid_search_filters_pending_move(
    proxy_server: tuple,
    upstream_imap_config: dict,
) -> None:
    """UID SEARCH ALL must not return UIDs that are staged for MOVE.

    The agent should get a search result that excludes moved messages,
    consistent with its view that the message is already gone.
    """
    host, port, agent_user, agent_token = proxy_server
    if not upstream_imap_config.get("host"):
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set")

    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # greeting

        writer.write(f"d001 LOGIN {agent_user} {agent_token}\r\n".encode())
        await writer.drain()
        await _read_until_ok_or_no(reader, "d001")

        writer.write(b"d002 SELECT INBOX\r\n")
        await writer.drain()
        await _read_until_ok_or_no(reader, "d002")
        await __import__("asyncio").sleep(0.05)

        # Baseline UID SEARCH ALL
        writer.write(b"d003 UID SEARCH ALL\r\n")
        await writer.drain()
        baseline_search = await _read_until_ok_or_no(reader, "d003")
        baseline_uids: list[int] = []
        for line in baseline_search:
            m = re.match(r"^\* SEARCH (.*)", line, re.IGNORECASE)
            if m:
                baseline_uids = [int(u) for u in m.group(1).split() if u.strip().isdigit()]

        if not baseline_uids:
            pytest.skip("No UIDs found — cannot test SEARCH filtering")

        move_uid = baseline_uids[0]

        # Stage a MOVE
        writer.write(f"d004 UID MOVE {move_uid} Archive\r\n".encode())
        await writer.drain()
        move_resp = await _read_until_ok_or_no(reader, "d004")
        assert any("STAGED" in l.upper() for l in move_resp)

        # UID SEARCH ALL after staging — moved UID must be absent
        writer.write(b"d005 UID SEARCH ALL\r\n")
        await writer.drain()
        filtered_search = await _read_until_ok_or_no(reader, "d005")
        filtered_uids: list[int] = []
        for line in filtered_search:
            m = re.match(r"^\* SEARCH (.*)", line, re.IGNORECASE)
            if m:
                filtered_uids = [int(u) for u in m.group(1).split() if u.strip().isdigit()]

        assert move_uid not in filtered_uids, (
            f"Pending-move UID {move_uid} should not appear in UID SEARCH results "
            f"after staging. Got: {filtered_uids!r}"
        )
        assert len(filtered_uids) == len(baseline_uids) - 1, (
            f"Expected {len(baseline_uids) - 1} UIDs after filtering, "
            f"got {len(filtered_uids)}"
        )

    finally:
        try:
            writer.write(b"d099 LOGOUT\r\n")
            await writer.drain()
        except OSError:
            pass
        await _close(writer)
