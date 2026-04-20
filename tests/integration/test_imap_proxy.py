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
