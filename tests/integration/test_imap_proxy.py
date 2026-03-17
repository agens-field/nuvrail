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
"""

import asyncio
import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from gateway.proxy import handle_client

# Ensure .env is loaded even when running this file directly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

_TIMEOUT = 15  # seconds — generous for CI and slow upstreams


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def proxy_server():
    """Start the proxy on an ephemeral port; yield (host, port); tear down."""
    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()
    task = asyncio.create_task(server.serve_forever())
    yield host, port
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


@pytest.mark.asyncio
async def test_proxy_forwards_greeting(proxy_server: tuple[str, int]) -> None:
    """Proxy must forward the upstream IMAP greeting (contains OK or PREAUTH)."""
    host, port = proxy_server
    reader, writer = await _connect(host, port)
    try:
        greeting = await _read_line(reader)
        assert "OK" in greeting.upper() or "PREAUTH" in greeting.upper(), (
            f"Expected greeting to contain OK or PREAUTH, got: {greeting!r}"
        )
    finally:
        await _close(writer)


@pytest.mark.asyncio
async def test_login_valid_credentials(
    proxy_server: tuple[str, int],
    upstream_imap_config: dict,
) -> None:
    """LOGIN with valid credentials must return a tagged OK."""
    host, port = proxy_server
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        user = upstream_imap_config["user"]
        password = upstream_imap_config["password"]
        writer.write(f"a001 LOGIN {user} {password}\r\n".encode())
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


@pytest.mark.asyncio
async def test_login_bad_credentials(proxy_server: tuple[str, int]) -> None:
    """LOGIN with bad credentials must return NO/BAD — proxy must not crash."""
    host, port = proxy_server
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        writer.write(b"a002 LOGIN baduser@example.com wrongpassword\r\n")
        await writer.drain()
        lines = await _read_until_ok_or_no(reader, "a002")
        last = lines[-1].upper()
        assert "A002 NO" in last or "A002 BAD" in last, (
            f"Expected NO or BAD after bad LOGIN, got: {lines!r}"
        )
    finally:
        await _close(writer)


@pytest.mark.asyncio
async def test_select_inbox_after_login(
    proxy_server: tuple[str, int],
    upstream_imap_config: dict,
) -> None:
    """After a successful LOGIN, SELECT INBOX must return a tagged OK."""
    host, port = proxy_server
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        user = upstream_imap_config["user"]
        password = upstream_imap_config["password"]

        writer.write(f"a003 LOGIN {user} {password}\r\n".encode())
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


@pytest.mark.asyncio
async def test_logout_clean(
    proxy_server: tuple[str, int],
    upstream_imap_config: dict,
) -> None:
    """LOGOUT after a successful login must complete cleanly (tagged OK)."""
    host, port = proxy_server
    reader, writer = await _connect(host, port)
    try:
        await _read_line(reader)  # consume greeting
        user = upstream_imap_config["user"]
        password = upstream_imap_config["password"]

        writer.write(f"a005 LOGIN {user} {password}\r\n".encode())
        await writer.drain()
        await _read_until_ok_or_no(reader, "a005")

        writer.write(b"a006 LOGOUT\r\n")
        await writer.drain()
        lines = await _read_until_ok_or_no(reader, "a006")
        last = lines[-1].upper()
        assert "A006 OK" in last, f"Expected OK after LOGOUT, got: {lines!r}"
    finally:
        await _close(writer)
