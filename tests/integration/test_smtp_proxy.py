"""
Integration tests for Milestone 0.5: SMTP proxy staging stub.

Test topology:

  test client (asyncio.open_connection, plain TCP)
        │
        ▼
  smtp_proxy server (gateway.smtp_proxy — local, plain)
        │  STARTTLS
        ▼
  upstream SMTP (blizzard.mxrouting.net:587)

Each test starts the proxy via an ephemeral port (port 0), connects as a
plain TCP client, and exercises the command/response behaviour.
Credentials are loaded from .env via conftest.py.

Auth notes (post-auth milestone):
  The proxy now verifies SMTP AUTH credentials against the agent_credentials
  table. Integration tests create a temp DB, register the upstream credentials
  as an agent credential, and use agent_username/agent_token for AUTH PLAIN.
"""

import asyncio
import base64
import os
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

import gateway.state_db as _state_db_mod
from gateway.smtp_proxy import handle_smtp_client
from gateway.state_db import get_db, init_db

# Ensure .env is loaded even when running this file directly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

_TIMEOUT = 20  # seconds — generous for slow upstreams and TLS handshake overhead


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def smtp_integration_db(upstream_smtp_config: dict) -> tuple[Path, str, str]:
    """Create a temp DB, register upstream SMTP creds as an agent credential.

    Returns (db_path, agent_username, agent_token_plain) so tests can use
    agent_username/agent_token as AUTH PLAIN credentials.
    """
    from api.auth import generate_token, hash_agent_token

    tmpdir = tempfile.mkdtemp()
    try:
        db_path = Path(tmpdir) / "smtp_integration.db"
        await init_db(db_path)

        agent_username = "nuvrail_smtp_test"
        agent_token_plain = generate_token()
        hashed = hash_agent_token(agent_token_plain)
        now = int(time.time())

        async with get_db(db_path) as db:
            cur = await db.execute(
                "INSERT INTO users (email, hashed_password, api_token, created_at) VALUES (?, ?, ?, ?)",
                ("smtp_test@test.com", "x", "smtp_test_token", now),
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
                    upstream_smtp_config.get("host", ""),
                    993,
                    upstream_smtp_config.get("port", 587),
                    upstream_smtp_config.get("user", ""),
                    upstream_smtp_config.get("password", ""),
                    now,
                ),
            )
            await db.commit()

        original_db_path = _state_db_mod.DB_PATH
        _state_db_mod.DB_PATH = db_path
        yield db_path, agent_username, agent_token_plain
        _state_db_mod.DB_PATH = original_db_path
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest_asyncio.fixture(loop_scope="session")
async def smtp_proxy_server(smtp_integration_db: tuple):
    """Start SMTP proxy on an ephemeral port; yield (host, port, agent_user, agent_token)."""
    db_path, agent_username, agent_token = smtp_integration_db
    server = await asyncio.start_server(handle_smtp_client, "127.0.0.1", 0)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


async def _read_line(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> str:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


async def _read_response(
    reader: asyncio.StreamReader, timeout: float = _TIMEOUT
) -> list[str]:
    """Read a complete (possibly multi-line) SMTP response, decoded."""
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
        # Final line: position 3 is a space (or line is short — i.e., no dash)
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
    """Encode credentials for AUTH PLAIN: base64('\\x00user\\x00password')."""
    return base64.b64encode(f"\x00{user}\x00{password}".encode()).decode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_proxy_forwards_greeting(
    smtp_proxy_server: tuple,
) -> None:
    """Proxy must forward the upstream SMTP greeting (starts with 220)."""
    host, port, _au, _at = smtp_proxy_server
    reader, writer = await _connect(host, port)
    try:
        greeting = await _read_line(reader)
        assert greeting.startswith("220"), (
            f"Expected 220 greeting, got: {greeting!r}"
        )
    finally:
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_ehlo(
    smtp_proxy_server: tuple,
) -> None:
    """EHLO must return 250 with capabilities; STARTTLS must NOT be advertised."""
    host, port, _au, _at = smtp_proxy_server
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        await _send(writer, "EHLO test.client\r\n")
        lines = await _read_response(reader)
        joined = "\n".join(lines).upper()
        assert lines[0].startswith("250"), f"Expected 250 after EHLO, got: {lines!r}"
        assert "STARTTLS" not in joined, (
            f"STARTTLS should be stripped from capabilities, got: {lines!r}"
        )
    finally:
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_auth_valid_credentials(
    smtp_proxy_server: tuple,
    upstream_smtp_config: dict,
) -> None:
    """AUTH PLAIN with valid agent credentials must return 235 Authentication succeeded."""
    host, port, agent_user, agent_token = smtp_proxy_server
    if not upstream_smtp_config.get("host"):
        pytest.skip("NUVRAIL_TEST_SMTP_HOST not set")
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        await _send(writer, "EHLO test.client\r\n")
        await _read_response(reader)

        creds = _auth_plain_b64(agent_user, agent_token)
        await _send(writer, f"AUTH PLAIN {creds}\r\n")
        lines = await _read_response(reader)
        assert lines[-1].startswith("235"), (
            f"Expected 235 after valid AUTH, got: {lines!r}"
        )
    finally:
        try:
            await _send(writer, "QUIT\r\n")
        except OSError:
            pass
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_auth_bad_credentials(
    smtp_proxy_server: tuple,
) -> None:
    """AUTH PLAIN with bad credentials must return 535; proxy must not crash."""
    host, port, _au, _at = smtp_proxy_server
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        await _send(writer, "EHLO test.client\r\n")
        await _read_response(reader)

        creds = _auth_plain_b64("baduser@example.com", "wrongpassword")
        await _send(writer, f"AUTH PLAIN {creds}\r\n")
        lines = await _read_response(reader)
        assert lines[-1].startswith("535"), (
            f"Expected 535 after bad AUTH, got: {lines!r}"
        )
    finally:
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_data_returns_staged(
    smtp_proxy_server: tuple,
    upstream_smtp_config: dict,
) -> None:
    """Full session EHLO→AUTH→MAIL FROM→RCPT TO→DATA→body→'.' must return 250 OK [STAGED]."""
    host, port, agent_user, agent_token = smtp_proxy_server
    if not upstream_smtp_config.get("host"):
        pytest.skip("NUVRAIL_TEST_SMTP_HOST not set")
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting

        await _send(writer, "EHLO test.client\r\n")
        ehlo_resp = await _read_response(reader)
        assert ehlo_resp[0].startswith("250"), f"EHLO failed: {ehlo_resp!r}"

        creds = _auth_plain_b64(agent_user, agent_token)
        await _send(writer, f"AUTH PLAIN {creds}\r\n")
        auth_resp = await _read_response(reader)
        assert auth_resp[-1].startswith("235"), f"AUTH failed: {auth_resp!r}"

        user = upstream_smtp_config["user"]
        await _send(writer, f"MAIL FROM:<{user}>\r\n")
        mail_resp = await _read_response(reader)
        assert mail_resp[-1].startswith("250"), f"MAIL FROM failed: {mail_resp!r}"

        await _send(writer, f"RCPT TO:<{user}>\r\n")
        rcpt_resp = await _read_response(reader)
        assert rcpt_resp[-1].startswith("250"), f"RCPT TO failed: {rcpt_resp!r}"

        await _send(writer, "DATA\r\n")
        data_go_ahead = await _read_response(reader)
        assert data_go_ahead[-1].startswith("354"), (
            f"Expected 354 after DATA, got: {data_go_ahead!r}"
        )

        # Send a minimal message body
        await _send(writer, f"From: <{user}>\r\n")
        await _send(writer, f"To: <{user}>\r\n")
        await _send(writer, "Subject: Milestone 0.5 test\r\n")
        await _send(writer, "\r\n")
        await _send(writer, "This is a test message body.\r\n")
        await _send(writer, ".\r\n")  # end of message

        staged_resp = await _read_response(reader)
        resp_text = " ".join(staged_resp)
        assert "250" in staged_resp[-1], (
            f"Expected 250 response after DATA body, got: {staged_resp!r}"
        )
        assert "STAGED" in resp_text.upper(), (
            f"Expected STAGED in response, got: {staged_resp!r}"
        )
    finally:
        try:
            await _send(writer, "QUIT\r\n")
        except OSError:
            pass
        await _close(writer)


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_quit_clean(
    smtp_proxy_server: tuple,
    upstream_smtp_config: dict,
) -> None:
    """QUIT after AUTH must return 221 and the connection must close cleanly."""
    host, port, agent_user, agent_token = smtp_proxy_server
    if not upstream_smtp_config.get("host"):
        pytest.skip("NUVRAIL_TEST_SMTP_HOST not set")
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting

        await _send(writer, "EHLO test.client\r\n")
        await _read_response(reader)

        creds = _auth_plain_b64(agent_user, agent_token)
        await _send(writer, f"AUTH PLAIN {creds}\r\n")
        auth_resp = await _read_response(reader)
        assert auth_resp[-1].startswith("235"), f"AUTH failed: {auth_resp!r}"

        await _send(writer, "QUIT\r\n")
        quit_resp = await _read_response(reader)
        assert quit_resp[-1].startswith("221"), (
            f"Expected 221 after QUIT, got: {quit_resp!r}"
        )

        # Connection should close — read should return empty bytes
        eof = await asyncio.wait_for(reader.read(1), timeout=5.0)
        assert eof == b"", f"Expected EOF after QUIT, got: {eof!r}"
    finally:
        await _close(writer)
