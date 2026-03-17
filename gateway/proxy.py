"""
Nuvrail IMAP Proxy — Milestone 0.2: Parser-aware intercept.

Data flow (0.2):

  Client (plain TCP)               proxy.py                Upstream (SSL/TLS)
  ──────────────────               ────────                ──────────────────
       connect          ──────────────────────────►        SSL connect
                        ◄──────────────────────────        greeting
       ◄── greeting

       cmd ────────────────► parse_line()
                                   │
                          ┌────────┴──────────────────────┐
                          │  classify() → read             │  classify() → write/blocked
                          │                                │
                          ▼                                ▼
                  forward cmd to upstream          intercept & respond locally
                  ◄──── upstream response          send OK [STAGED] / OK Noted
                  ◄──── forward response

       (special case: sync literal {N})
       ◄── "+ Ready for literal data"
       literal bytes ────────────────                 (discarded — staged stub)
       ◄── "<tag> OK [STAGED] ..."

       ...              ◄──────────────────────────        ...
       disconnect ────────────────────────────────►

Sub-milestone 0.1 LOGIN intercept is preserved; 0.3 will translate to XOAUTH2.
Upstream→client direction is still a raw byte pump.
"""

import asyncio
import logging
import os
import re
import ssl

from dotenv import load_dotenv

from gateway.command_router import classify
from gateway.imap_parser import parse_line

load_dotenv()

logger = logging.getLogger(__name__)

_UPSTREAM_HOST: str = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
_UPSTREAM_PORT: int = int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993"))

_READ_CHUNK = 4096

# Matches the literal size at end of an IMAP line: {N} or {N+}
_LITERAL_RE = re.compile(r"\{(\d+)\+?\}$")


async def _client_to_upstream(
    client_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
    client_writer: asyncio.StreamWriter,
    peer: str,
) -> None:
    """Parse each client line and route: read→upstream, write/blocked→intercept.

    Handles sync literals ({N} at end of line) by consuming the literal body
    from the client and responding with OK [STAGED] without forwarding.
    """
    while True:
        try:
            line_bytes = await client_reader.readline()
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            break
        if not line_bytes:
            break

        raw = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")

        # --- LOGIN intercept (kept from 0.1) ---------------------------------
        parts = raw.split()
        if len(parts) >= 3 and parts[1].upper() == "LOGIN":
            user = parts[2] if len(parts) > 2 else "<unknown>"
            # TODO 0.3: translate LOGIN to XOAUTH2 for OAuth2 providers
            logger.debug("[%s] LOGIN intercepted user=%s [password redacted]", peer, user)

        # --- Parse line -------------------------------------------------------
        parsed = parse_line(raw)

        if parsed is None:
            # Sync literal: {N} at end of line — consume the literal bytes and
            # respond with STAGED instead of forwarding to upstream.
            tag = raw.split()[0] if raw.split() else "?"
            m = _LITERAL_RE.search(raw)
            literal_size = int(m.group(1)) if m else 0

            try:
                client_writer.write(b"+ Ready for literal data\r\n")
                await client_writer.drain()
                if literal_size > 0:
                    await client_reader.readexactly(literal_size)
                await client_reader.readline()  # consume trailing CRLF after literal
                client_writer.write(
                    f"{tag} OK [STAGED] Operation queued for approval\r\n".encode()
                )
                await client_writer.drain()
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            logger.info("[%s] STAGED (literal): %s", peer, raw)
            continue

        action = classify(parsed)

        if action == "read":
            # Pass through to upstream; the u2c pump returns the response.
            try:
                upstream_writer.write(line_bytes)
                await upstream_writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

        elif action == "write":
            # Intercept — don't forward, respond locally.
            resp = f"{parsed.tag} OK [STAGED] Operation queued for approval\r\n"
            try:
                client_writer.write(resp.encode())
                await client_writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
            logger.info("[%s] STAGED: %s", peer, raw)
            # TODO 0.3+: create Operation record in staging engine

        else:  # blocked
            resp = f"{parsed.tag} OK Noted\r\n"
            try:
                client_writer.write(resp.encode())
                await client_writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
            logger.info("[%s] BLOCKED: %s", peer, raw)


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
        logger.error(
            "[%s] Failed to connect to upstream %s:%d — %s",
            peer_str, _UPSTREAM_HOST, _UPSTREAM_PORT, exc,
        )
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

    # Start bidirectional pump.
    c2u = asyncio.create_task(
        _client_to_upstream(client_reader, upstream_writer, client_writer, peer_str)
    )
    u2c = asyncio.create_task(
        _upstream_to_client(upstream_reader, client_writer, peer_str)
    )

    done, pending = await asyncio.wait({c2u, u2c}, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

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
    logger.info(
        "Nuvrail IMAP proxy listening on %s:%d → upstream %s:%d",
        actual[0], actual[1], _UPSTREAM_HOST, _UPSTREAM_PORT,
    )
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
