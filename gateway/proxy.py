"""
Nuvrail IMAP Proxy — Milestone 0.1: Dumb byte pipe with LOGIN interception.

Data flow:

  Client (plain TCP)               proxy.py               Upstream (SSL/TLS)
  ──────────────────               ────────               ──────────────────
       connect          ──────────────────────────►       SSL connect
                        ◄──────────────────────────       greeting
       ◄── greeting
       cmd ──────────────────────────────────────►
                        (intercepts LOGIN, logs it)
                        ◄──────────────────────────       response
       ◄── response
       ...              ◄──────────────────────────       ...
       disconnect ──────────────────────────────►

Sub-milestone 0.1: NO parser, NO classifier, NO staging engine.
The LOGIN line is intercepted only for logging/future translation.
All bytes pass through to upstream unchanged.
"""

import asyncio
import logging
import os
import ssl

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_UPSTREAM_HOST: str = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
_UPSTREAM_PORT: int = int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993"))

_READ_CHUNK = 4096


async def _client_to_upstream(
    client_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
    peer: str,
) -> None:
    """Pump lines from client → upstream, intercepting LOGIN for logging.

    Reads line-by-line so we can detect the LOGIN command without a full parser.
    All bytes pass through unchanged.
    """
    while True:
        try:
            line = await client_reader.readline()
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            break
        if not line:
            break

        # Detect LOGIN command: <tag> LOGIN <user> <password>\r\n
        # No quoted-string parsing here — that lands in milestone 0.2.
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        parts = decoded.split()
        if len(parts) >= 3 and parts[1].upper() == "LOGIN":
            user = parts[2] if len(parts) > 2 else "<unknown>"
            # TODO: in sub-milestone 0.3, translate LOGIN to XOAUTH2 for OAuth2 providers
            logger.debug("[%s] LOGIN intercepted user=%s [password redacted]", peer, user)

        try:
            upstream_writer.write(line)
            await upstream_writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            break


async def _upstream_to_client(
    upstream_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    peer: str,
) -> None:
    """Pump raw bytes from upstream → client."""
    while True:
        try:
            data = await upstream_reader.read(_READ_CHUNK)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            break
        if not data:
            break

        try:
            client_writer.write(data)
            await client_writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            break


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """Handle one client connection: open upstream SSL, pump bytes bidirectionally."""
    peer = client_writer.get_extra_info("peername", ("?", 0))
    peer_str = f"{peer[0]}:{peer[1]}"
    logger.info("[%s] Client connected", peer_str)

    # Open SSL connection to upstream immediately.
    ssl_ctx = ssl.create_default_context()
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            _UPSTREAM_HOST,
            _UPSTREAM_PORT,
            ssl=ssl_ctx,
        )
    except Exception as exc:
        logger.error("[%s] Failed to connect to upstream %s:%d — %s", peer_str, _UPSTREAM_HOST, _UPSTREAM_PORT, exc)
        # Return a proper IMAP error to the client rather than just dropping the connection.
        try:
            client_writer.write(b"* BYE Upstream connection failed\r\n")
            await client_writer.drain()
        except OSError:
            pass
        client_writer.close()
        return

    # Forward the upstream greeting to the client.
    try:
        greeting = await upstream_reader.readline()
        client_writer.write(greeting)
        await client_writer.drain()
    except (OSError, asyncio.IncompleteReadError) as exc:
        logger.error("[%s] Failed to read upstream greeting: %s", peer_str, exc)
        client_writer.close()
        upstream_writer.close()
        return

    # Start bidirectional pump: two tasks, one per direction.
    c2u = asyncio.create_task(_client_to_upstream(client_reader, upstream_writer, peer_str))
    u2c = asyncio.create_task(_upstream_to_client(upstream_reader, client_writer, peer_str))

    # Wait for either direction to finish (disconnect on either side).
    done, pending = await asyncio.wait({c2u, u2c}, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Close both sides cleanly.
    try:
        upstream_writer.close()
        await upstream_writer.wait_closed()
    except OSError:
        pass

    try:
        client_writer.close()
        await client_writer.wait_closed()
    except OSError:
        pass

    logger.info("[%s] Client disconnected", peer_str)


async def start_proxy(host: str, port: int) -> asyncio.AbstractServer:
    """Start the proxy server and return the Server object.

    Exposed separately from main() so tests can bind to port 0 and retrieve the
    actual ephemeral port via server.sockets[0].getsockname()[1].
    """
    server = await asyncio.start_server(handle_client, host, port)
    actual = server.sockets[0].getsockname()
    logger.info("Nuvrail IMAP proxy listening on %s:%d → upstream %s:%d", actual[0], actual[1], _UPSTREAM_HOST, _UPSTREAM_PORT)
    return server


async def main() -> None:
    """Entry point: read config from env and start the proxy."""
    host = os.environ.get("NUVRAIL_PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("NUVRAIL_PROXY_IMAP_PORT", "10143"))

    server = await start_proxy(host, port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
