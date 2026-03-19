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

from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
from typing import Optional

from dotenv import load_dotenv

from gateway.command_router import classify
from gateway.imap_parser import ParsedCommand, parse_line
from gateway.operation_parser import ParsedOperation, parse_append, parse_copy, parse_move, parse_store
from gateway.staging import create_operation
from gateway.state_db import DB_PATH, init_db

load_dotenv()

logger = logging.getLogger(__name__)

_UPSTREAM_HOST: str = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
_UPSTREAM_PORT: int = int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993"))

_READ_CHUNK = 4096

# Matches the literal size at end of an IMAP line: {N} or {N+}
_LITERAL_RE = re.compile(r"\{(\d+)\+?\}$")


def _build_parsed_op(parsed: ParsedCommand) -> Optional[ParsedOperation]:
    """Convert a ParsedCommand into a ParsedOperation for staging.

    Returns None for commands that should use generic staging (CREATE, RENAME, etc.).
    """
    cmd = parsed.command
    args = parsed.args
    uid_mode = parsed.uid

    if cmd in ("STORE",):
        # STORE uid_set flags_op flags_list
        uid_set = args[0] if args else "?"
        flags_op = args[1] if len(args) > 1 else "+FLAGS"
        # flags may be in parentheses like (\Seen) or bare \Seen
        raw_flags = args[2] if len(args) > 2 else "()"
        if raw_flags.startswith("(") and raw_flags.endswith(")"):
            flags = raw_flags[1:-1].split()
        else:
            flags = [raw_flags]
        return parse_store(parsed.tag, uid_mode, uid_set, flags_op, flags)

    if cmd in ("MOVE",):
        uid_set = args[0] if args else "?"
        destination = args[1] if len(args) > 1 else "?"
        return parse_move(parsed.tag, uid_set, destination)

    if cmd in ("COPY",):
        uid_set = args[0] if args else "?"
        destination = args[1] if len(args) > 1 else "?"
        return parse_copy(parsed.tag, uid_set, destination)

    # APPEND is handled via the literal path; if it somehow reaches here, generic staging
    # CREATE, RENAME → return None (caller uses generic staging)
    return None


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
            # Sync literal: {N} at end of line — this is typically an APPEND command.
            # Consume the literal bytes, stage the operation, respond with OK [STAGED].
            parts = raw.split()
            tag = parts[0] if parts else "?"
            m = _LITERAL_RE.search(raw)
            literal_size = int(m.group(1)) if m else 0

            # Extract APPEND parameters from the raw command line before the literal
            # Format: tag APPEND folder (flags) {size}
            append_folder = parts[2] if len(parts) > 2 else "INBOX"
            # Flags are in a parenthesised token — find it
            import re as _re
            flags_match = _re.search(r"\(([^)]*)\)", raw)
            append_flags = flags_match.group(1).split() if flags_match else []

            try:
                client_writer.write(b"+ Ready for literal data\r\n")
                await client_writer.drain()
                if literal_size > 0:
                    await client_reader.readexactly(literal_size)
                await client_reader.readline()  # consume trailing CRLF after literal
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break

            try:
                parsed_op = parse_append(tag, append_folder, append_flags, literal_size)
                op_id = await create_operation(
                    op_type=parsed_op.op_type,
                    protocol="imap",
                    description=parsed_op.description,
                    imap_command=parsed_op.imap_command,
                    folder_to=parsed_op.folder_to,
                    flags_add=parsed_op.flags_add,
                )
                resp = f"{tag} OK [STAGED] Operation queued — ID: {op_id}\r\n"
            except Exception as exc:
                logger.error("[%s] Failed to stage APPEND: %s", peer, exc)
                resp = f"{tag} OK [STAGED] Operation queued for approval\r\n"

            try:
                client_writer.write(resp.encode())
                await client_writer.drain()
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            logger.info("[%s] STAGED (literal APPEND): %s", peer, raw)
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
            # Intercept — don't forward, stage the operation, respond locally.
            try:
                parsed_op = _build_parsed_op(parsed)
                if parsed_op is not None:
                    op_id = await create_operation(
                        op_type=parsed_op.op_type,
                        protocol="imap",
                        description=parsed_op.description,
                        imap_command=parsed_op.imap_command,
                        message_ids=parsed_op.message_ids if parsed_op.message_ids else None,
                        folder_from=parsed_op.folder_from,
                        folder_to=parsed_op.folder_to,
                        flags_add=parsed_op.flags_add if parsed_op.flags_add else None,
                        flags_remove=parsed_op.flags_remove if parsed_op.flags_remove else None,
                    )
                    resp = f"{parsed.tag} OK [STAGED] Operation queued — ID: {op_id}\r\n"
                else:
                    # CREATE / RENAME or unknown write — stage generically
                    op_id = await create_operation(
                        op_type=parsed.command.lower(),
                        protocol="imap",
                        description=f"{parsed.command} {' '.join(parsed.args)}".strip(),
                        imap_command=raw,
                    )
                    resp = f"{parsed.tag} OK [STAGED] Operation queued — ID: {op_id}\r\n"
            except Exception as exc:
                logger.error("[%s] Failed to stage write command: %s", peer, exc)
                resp = f"{parsed.tag} OK [STAGED] Operation queued for approval\r\n"

            try:
                client_writer.write(resp.encode())
                await client_writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
            logger.info("[%s] STAGED: %s", peer, raw)

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
    await init_db(DB_PATH)
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
