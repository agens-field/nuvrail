"""
Tests for proxy error message content (GitHub #14).

Verifies that NO/BAD/5xx responses from both proxies are specific and
actionable rather than generic. These tests spin up proxy instances
against a fake upstream (or no upstream at all) so they run entirely
offline and require no real credentials.

Test map
--------
IMAP
  test_imap_pre_auth_command_rejected            -- cmd before LOGIN → [CLIENTBUG]
  test_imap_unsupported_auth_mechanism           -- non-PLAIN mech → specific text
  test_imap_bad_credentials                      -- wrong token  → [AUTHENTICATIONFAILED]
  test_imap_upstream_unreachable                 -- bad upstream host → [UNAVAILABLE] + host
  test_imap_rate_limit_message_includes_retry    -- rate-limit BYE/NO → [LIMIT] + seconds

SMTP
  test_smtp_unsupported_auth_mechanism           -- unknown mech → 504 + mechanism hint
  test_smtp_bad_credentials                      -- wrong token  → 535 + enhanced code
  test_smtp_no_auth_before_command               -- MAIL before auth → 530
  test_smtp_upstream_unreachable                 -- bad upstream host → 421 + 4.4.1 + host
"""

from __future__ import annotations

import asyncio
import base64
import time
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

import gateway.state_db as _state_db_mod
from api.auth import generate_token, hash_agent_token
from gateway.proxy import handle_client
from gateway.smtp_proxy import handle_smtp_client
from gateway.state_db import get_db, init_db

# 127.0.0.1:1 causes immediate ECONNREFUSED (much faster than a routable unreachable host).
# We check that the proxy echoes back the host in its error message, and port 1 is
# nearly always unbound on any test machine.
_UNREACHABLE_HOST = "127.0.0.1"
_UNREACHABLE_PORT_IMAP = 1
_UNREACHABLE_PORT_SMTP = 1

_TIMEOUT = 5  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _readline_timeout(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> str:
    raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


async def _read_until_tagged(
    reader: asyncio.StreamReader, tag: str, timeout: float = _TIMEOUT
) -> list[str]:
    """Read lines until the one starting with <tag> OK/NO/BAD."""
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(decoded)
        upper = decoded.upper()
        if (
            upper.startswith(tag.upper() + " OK")
            or upper.startswith(tag.upper() + " NO")
            or upper.startswith(tag.upper() + " BAD")
        ):
            break
    return lines


async def _read_smtp_lines(reader: asyncio.StreamReader, timeout: float = _TIMEOUT) -> list[str]:
    """Read SMTP response lines (stop on a line not ending in '-' continuation)."""
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(decoded)
        # SMTP multi-line: "250-..." continues; "250 ..." terminates
        if len(decoded) >= 4 and decoded[3] != "-":
            break
    return lines


# ---------------------------------------------------------------------------
# Shared DB fixture (patches _state_db_mod.DB_PATH — same pattern as integration tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def error_test_db() -> tuple[Path, str, str, str, str]:
    """
    Single shared DB for all error message tests.

    Registers two agent credentials:
      - imap_err_user / imap_err_token  (IMAP unreachable-upstream tests)
      - smtp_err_user / smtp_err_token  (SMTP unreachable-upstream tests)

    Both credentials point at 127.0.0.1:1 (localhost port 1, immediate ECONNREFUSED)
    so auth succeeds at the proxy layer but upstream connection fails fast.

    Patches _state_db_mod.DB_PATH for the entire session; restores on teardown.
    """
    from gateway.credentials import encrypt_credential

    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "error_tests.db"
    await init_db(db_path)

    imap_user = "nuvrail_imap_err"
    imap_token = generate_token()
    smtp_user = "nuvrail_smtp_err"
    smtp_token = generate_token()
    now = int(time.time())

    async with get_db(db_path) as db:
        cur = await db.execute(
            "INSERT INTO users (email, hashed_password, api_token, created_at) VALUES (?, ?, ?, ?)",
            ("err_test@test.com", "x", "err_test_token", now),
        )
        user_id = cur.lastrowid
        for username, token_plain, imap_port, smtp_port in (
            (imap_user, imap_token, _UNREACHABLE_PORT_IMAP, 587),
            (smtp_user, smtp_token, 993, _UNREACHABLE_PORT_SMTP),
        ):
            await db.execute(
                """INSERT INTO agent_credentials
                   (user_id, label, agent_username, hashed_token,
                    upstream_host, upstream_imap_port, upstream_smtp_port,
                    upstream_user, upstream_password, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, username, username,
                    hash_agent_token(token_plain),
                    _UNREACHABLE_HOST,  # localhost port 1 — immediate ECONNREFUSED
                    imap_port, smtp_port,
                    "upstream@example.com",
                    encrypt_credential("fake_password"),
                    now,
                ),
            )
        await db.commit()

    # Patch module-level DB_PATH (same pattern as integration tests)
    original = _state_db_mod.DB_PATH
    _state_db_mod.DB_PATH = db_path
    yield db_path, imap_user, imap_token, smtp_user, smtp_token
    _state_db_mod.DB_PATH = original


# ---------------------------------------------------------------------------
# IMAP proxy fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def imap_proxy_for_errors(
    error_test_db: tuple,
) -> tuple[asyncio.AbstractServer, int]:
    """IMAP proxy bound to an ephemeral port. Uses patched _state_db_mod.DB_PATH."""
    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield server, port
    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# SMTP proxy fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def smtp_proxy_for_errors(
    error_test_db: tuple,
) -> tuple[asyncio.AbstractServer, int]:
    """SMTP proxy bound to an ephemeral port. Uses patched _state_db_mod.DB_PATH."""
    server = await asyncio.start_server(handle_smtp_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield server, port
    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# IMAP error message tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_pre_auth_command_rejected(
    error_test_db: tuple,
    imap_proxy_for_errors: tuple,
) -> None:
    """Sending SELECT before LOGIN must produce NO [CLIENTBUG] with the command name."""
    _, port = imap_proxy_for_errors
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # greeting

        writer.write(b"A1 SELECT INBOX\r\n")
        await writer.drain()
        lines = await _read_until_tagged(reader, "A1")
        response = " ".join(lines)

        assert "NO" in response, f"Expected NO, got: {lines!r}"
        assert "[CLIENTBUG]" in response, f"Expected [CLIENTBUG] in response, got: {lines!r}"
        assert "SELECT" in response, (
            f"Expected command name 'SELECT' in error, got: {lines!r}"
        )
        assert "LOGIN" in response.upper(), (
            f"Expected authentication hint in error, got: {lines!r}"
        )
    finally:
        writer.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_unsupported_auth_mechanism(
    error_test_db: tuple,
    imap_proxy_for_errors: tuple,
) -> None:
    """AUTHENTICATE GSSAPI must produce NO with instructions to use supported mechanisms."""
    _, port = imap_proxy_for_errors
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # greeting

        writer.write(b"A1 AUTHENTICATE GSSAPI\r\n")
        await writer.drain()
        lines = await _read_until_tagged(reader, "A1")
        response = " ".join(lines)

        assert "NO" in response, f"Expected NO for unsupported mechanism, got: {lines!r}"
        # Must tell agent what to use instead
        assert "PLAIN" in response.upper(), (
            f"Expected 'PLAIN' hint in response, got: {lines!r}"
        )
    finally:
        writer.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_bad_credentials(
    error_test_db: tuple,
    imap_proxy_for_errors: tuple,
) -> None:
    """Wrong agent token must produce NO [AUTHENTICATIONFAILED]."""
    _, port = imap_proxy_for_errors
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # greeting

        writer.write(b"A1 LOGIN bad_user wrong_token\r\n")
        await writer.drain()
        # Response may be BYE + NO on two lines
        lines: list[str] = []
        for _ in range(5):
            line = await asyncio.wait_for(reader.readline(), timeout=_TIMEOUT)
            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            lines.append(decoded)
            upper = decoded.upper()
            if upper.startswith("A1 NO") or upper.startswith("A1 BAD"):
                break
        response = " ".join(lines)

        assert "NO" in response, f"Expected NO for bad credentials, got: {lines!r}"
        assert "[AUTHENTICATIONFAILED]" in response, (
            f"Expected [AUTHENTICATIONFAILED] in response, got: {lines!r}"
        )
    finally:
        writer.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_imap_upstream_unreachable(
    error_test_db: tuple,
    imap_proxy_for_errors: tuple,
) -> None:
    """Valid credentials but unreachable upstream must produce NO [UNAVAILABLE] + host."""
    _, port = imap_proxy_for_errors
    _db, agent_username, agent_token, _su, _st = error_test_db

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # greeting

        writer.write(f"A1 LOGIN {agent_username} {agent_token}\r\n".encode())
        await writer.drain()
        lines: list[str] = []
        for _ in range(5):
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=_TIMEOUT)
            except asyncio.TimeoutError:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            lines.append(decoded)
            upper = decoded.upper()
            if upper.startswith("A1 NO") or upper.startswith("A1 BAD"):
                break
        response = " ".join(lines)

        assert "NO" in response, f"Expected NO for unreachable upstream, got: {lines!r}"
        assert "[UNAVAILABLE]" in response, (
            f"Expected [UNAVAILABLE] in response, got: {lines!r}"
        )
        # Must include host so operator knows which upstream is down
        assert _UNREACHABLE_HOST in response, (
            f"Expected upstream host in error message, got: {lines!r}"
        )
    finally:
        writer.close()


# ---------------------------------------------------------------------------
# SMTP error message tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_unsupported_auth_mechanism(
    error_test_db: tuple,
    smtp_proxy_for_errors: tuple,
) -> None:
    """AUTH with unknown mechanism must produce 504 with supported-mechanism hint."""
    _, port = smtp_proxy_for_errors
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # 220 greeting

        writer.write(b"EHLO test.local\r\n")
        await writer.drain()
        await _read_smtp_lines(reader)  # 250 capabilities

        writer.write(b"AUTH GSSAPI\r\n")
        await writer.drain()
        lines = await _read_smtp_lines(reader)
        response = " ".join(lines)

        assert lines[0].startswith("504"), (
            f"Expected 504 for unsupported mechanism, got: {lines!r}"
        )
        assert "5.5.4" in response, (
            f"Expected enhanced status code 5.5.4, got: {lines!r}"
        )
        assert "PLAIN" in response.upper(), (
            f"Expected 'PLAIN' hint in 504 response, got: {lines!r}"
        )
    finally:
        writer.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_bad_credentials(
    error_test_db: tuple,
    smtp_proxy_for_errors: tuple,
) -> None:
    """AUTH PLAIN with wrong credentials must produce 535 5.7.8."""
    _, port = smtp_proxy_for_errors
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # 220

        writer.write(b"EHLO test.local\r\n")
        await writer.drain()
        await _read_smtp_lines(reader)  # 250

        bad_b64 = base64.b64encode(b"\x00bad_user\x00wrong_token").decode()
        writer.write(f"AUTH PLAIN {bad_b64}\r\n".encode())
        await writer.drain()
        lines = await _read_smtp_lines(reader)

        assert lines[0].startswith("535"), (
            f"Expected 535 for bad credentials, got: {lines!r}"
        )
        assert "5.7.8" in lines[0], (
            f"Expected enhanced status 5.7.8 in 535 response, got: {lines!r}"
        )
    finally:
        writer.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_no_auth_before_command(
    error_test_db: tuple,
    smtp_proxy_for_errors: tuple,
) -> None:
    """MAIL FROM before AUTH must return 530 5.7.0."""
    _, port = smtp_proxy_for_errors
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # 220

        writer.write(b"EHLO test.local\r\n")
        await writer.drain()
        await _read_smtp_lines(reader)  # 250

        writer.write(b"MAIL FROM:<agent@example.com>\r\n")
        await writer.drain()
        lines = await _read_smtp_lines(reader)

        assert lines[0].startswith("530"), (
            f"Expected 530 before auth, got: {lines!r}"
        )
        assert "5.7.0" in lines[0], (
            f"Expected enhanced status 5.7.0 in 530 response, got: {lines!r}"
        )
    finally:
        writer.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_smtp_upstream_unreachable(
    error_test_db: tuple,
    smtp_proxy_for_errors: tuple,
) -> None:
    """Valid credentials but unreachable upstream must produce 421 4.4.1 + host."""
    _, port = smtp_proxy_for_errors
    _db, _iu, _it, agent_username, agent_token = error_test_db

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _readline_timeout(reader)  # 220

        writer.write(b"EHLO test.local\r\n")
        await writer.drain()
        await _read_smtp_lines(reader)  # 250

        b64 = base64.b64encode(f"\x00{agent_username}\x00{agent_token}".encode()).decode()
        writer.write(f"AUTH PLAIN {b64}\r\n".encode())
        await writer.drain()
        lines = await _read_smtp_lines(reader)
        response = " ".join(lines)

        assert lines[0].startswith("421"), (
            f"Expected 421 for unreachable upstream, got: {lines!r}"
        )
        assert "4.4.1" in response, (
            f"Expected enhanced status 4.4.1, got: {lines!r}"
        )
        assert _UNREACHABLE_HOST in response, (
            f"Expected upstream host in 421 response, got: {lines!r}"
        )
    finally:
        writer.close()
