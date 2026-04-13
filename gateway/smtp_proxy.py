"""
Nuvrail SMTP Proxy — Milestone 0.5: Staging stub.

Data flow:

  Client (plain TCP)          smtp_proxy.py           Upstream (STARTTLS)
  ──────────────────          ─────────────           ───────────────────
       connect       ──────────────────────────►      connect + STARTTLS
                     ◄──────────────────────────      220 greeting
       ◄── greeting

       EHLO          ──────────────────────────►
                     ◄──────────────────────────      250 capabilities
       ◄── 250 (STARTTLS stripped)

       AUTH          ──────────────────────────►      (log user, redact creds)
                     ◄──────────────────────────      235 OK
       ◄── 235

       MAIL FROM     ──────────────────────────►      (track sender)
       RCPT TO       ──────────────────────────►      (track recipients)
       DATA          ──────────────────────────►
                     ◄──────────────────────────      354 go ahead
       message body  →  (consumed, NOT forwarded)
       .             →  (end of body — NOT forwarded)
       ◄── 250 OK [STAGED] Send queued for approval — ID: pending

       QUIT          ──────────────────────────►
       ◄── 221

Sub-milestone 0.5: staging stub only. Full relay lands in milestone 1.0.
TODO 0.3: translate AUTH to XOAUTH2 for OAuth2 providers.

Architectural note — single coroutine vs. two-task design
──────────────────────────────────────────────────────────
The IMAP proxy uses two concurrent asyncio tasks (one for client→upstream,
one for upstream→client) because IMAP is full-duplex: the server may send
unsolicited push responses (EXISTS, EXPUNGE, FETCH) at any time, independent
of client commands. The two directions can be pumped independently with no
synchronisation required.

SMTP is strictly request/response. Every command the client sends elicits
exactly one response from the server before the next command is valid. More
critically, DATA interception requires tight synchronisation across BOTH
directions:

  1. Forward DATA → upstream
  2. Read upstream's 354 (don't forward to client yet)
  3. Drain the client body until the terminating "." line
  4. Return 250 OK [STAGED] to client

Doing this with two independent tasks would require a shared state machine,
condition variables, and careful locking — far more complex than necessary.
A single coroutine that reads one client line at a time and drives upstream
responses inline is both simpler and correct for the SMTP protocol.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import ssl
from typing import Optional

from dotenv import load_dotenv

from gateway.credentials import decrypt_credential
from gateway.staging import create_operation
from gateway.state_db import DB_PATH, get_db

load_dotenv()

logger = logging.getLogger(__name__)

# Regex helpers for envelope extraction
_MAIL_FROM_RE = re.compile(r"MAIL FROM:\s*<([^>]*)>", re.IGNORECASE)
_RCPT_TO_RE = re.compile(r"RCPT TO:\s*<([^>]*)>", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Agent credential verification
# ---------------------------------------------------------------------------


async def _verify_smtp_agent_credential(
    agent_user: str, agent_pass: str, db_path: "Optional[Path]" = None
) -> "Optional[dict]":
    """Verify SMTP agent username + password against agent_credentials table.

    Returns the credential row if valid and not revoked, None otherwise.
    db_path defaults to the current module-level DB_PATH (read at call time
    so integration tests can patch gateway.state_db.DB_PATH).
    """
    import gateway.state_db as _state_db_mod  # noqa: PLC0415

    _db = db_path if db_path is not None else _state_db_mod.DB_PATH
    async with get_db(_db) as db:
        async with db.execute(
            """SELECT * FROM agent_credentials
               WHERE agent_username = ? AND revoked_at IS NULL""",
            (agent_user,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    from api.auth import verify_password  # noqa: PLC0415

    if not verify_password(agent_pass, row["hashed_token"]):
        return None
    return dict(row)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Low-level SMTP response helpers
# ---------------------------------------------------------------------------


async def _read_smtp_response(reader: asyncio.StreamReader) -> list[bytes]:
    """Read a complete (possibly multi-line) SMTP response.

    Multi-line responses use the format:
        CODE-text\\r\\n   ← continuation line
        CODE text\\r\\n   ← final line (space instead of dash)
    Returns all lines including the final one.
    """
    lines: list[bytes] = []
    while True:
        line = await reader.readline()
        if not line:
            break
        lines.append(line)
        # Final line has a space (or no char) at position 3; continuation has '-'
        if len(line) < 4 or line[3:4] != b"-":
            break
    return lines


def _strip_starttls(lines: list[bytes]) -> list[bytes]:
    """Remove STARTTLS capability from EHLO response and fix continuation markers.

    When STARTTLS is the last capability listed, the line before it becomes the
    new last line and must use a space (not a dash) at position 3.
    """
    filtered = [ln for ln in lines if b"STARTTLS" not in ln.upper()]
    if not filtered:
        return lines  # safety fallback

    # Ensure the final line uses a space at position 3 (not a dash)
    last = filtered[-1]
    if len(last) >= 4 and last[3:4] == b"-":
        filtered[-1] = last[:3] + b" " + last[4:]
    return filtered


def _extract_subject(body_lines: list[bytes]) -> str:
    """Extract the Subject header value from a list of message body lines."""
    for line in body_lines:
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if decoded.lower().startswith("subject:"):
            return decoded[8:].strip()
        if not decoded.strip():
            # Blank line marks end of headers
            break
    return "<no subject>"


# ---------------------------------------------------------------------------
# Upstream STARTTLS setup
# ---------------------------------------------------------------------------


async def _connect_upstream_starttls(
    host: str,
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    """Open a plain TCP connection to upstream, negotiate STARTTLS, and return
    the upgraded (TLS) reader/writer pair plus the raw 220 greeting bytes.

    Raises on connection failure or unexpected responses.
    """
    reader, writer = await asyncio.open_connection(host, port)

    # Step 1: Read 220 greeting (save it to forward to client later)
    greeting_lines = await _read_smtp_response(reader)
    greeting_bytes = b"".join(greeting_lines)

    # Step 2: Send EHLO pre-STARTTLS (required to discover STARTTLS capability)
    writer.write(b"EHLO nuvrail-proxy\r\n")
    await writer.drain()
    await _read_smtp_response(reader)  # discard pre-TLS capabilities

    # Step 3: Send STARTTLS command
    writer.write(b"STARTTLS\r\n")
    await writer.drain()
    starttls_resp = await _read_smtp_response(reader)
    if not starttls_resp or not starttls_resp[0].startswith(b"220"):
        raise RuntimeError(f"Unexpected STARTTLS response: {starttls_resp!r}")

    # Step 4: Upgrade the transport to TLS.
    # Python 3.9 does not have StreamWriter.start_tls() (added in 3.11), so
    # we call loop.start_tls() directly, passing the underlying transport and
    # protocol, then patch the writer's _transport reference so subsequent
    # writer.write() calls go over the TLS channel.
    ssl_ctx = ssl.create_default_context()
    loop = asyncio.get_running_loop()
    tls_transport = await loop.start_tls(
        writer.transport,
        writer._protocol,  # type: ignore[attr-defined]
        ssl_ctx,
        server_side=False,
        server_hostname=host,
    )
    writer._transport = tls_transport  # type: ignore[attr-defined]

    # Step 5: EHLO again — required by RFC 3207 after STARTTLS
    writer.write(b"EHLO nuvrail-proxy\r\n")
    await writer.drain()
    # (Caller will read this response in the command loop as needed)

    return reader, writer, greeting_bytes


# ---------------------------------------------------------------------------
# Main connection handler
# ---------------------------------------------------------------------------


async def handle_smtp_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """
    Handle one SMTP client connection.

    Auth-before-upstream flow:

      Client connects
          │
          ▼
      Proxy sends synthetic 220 greeting (no upstream yet)
          │
          ▼
      Relay EHLO locally with synthetic capabilities
          │
          ▼
      Wait for AUTH — verify agent credentials
          ├─ fail → 535 + close
          │
          ▼
      Open upstream using credential.upstream_host/upstream_smtp_port
      Negotiate STARTTLS, authenticate to upstream with real credentials
          │
          ▼
      Continue command loop (MAIL FROM, RCPT TO, DATA, QUIT)

    Each agent's upstream host/port comes from agent_credentials row.
    """
    peer = client_writer.get_extra_info("peername", ("?", 0))
    peer_str = f"{peer[0]}:{peer[1]}"
    logger.info("[%s] SMTP client connected", peer_str)

    # --- Step 1: Send synthetic greeting — no upstream connection yet --------
    try:
        client_writer.write(b"220 Nuvrail SMTP proxy ready\r\n")
        await client_writer.drain()
    except OSError as exc:
        logger.error("[%s] Failed to send greeting: %s", peer_str, exc)
        client_writer.close()
        return

    upstream_reader: Optional[asyncio.StreamReader] = None
    upstream_writer: Optional[asyncio.StreamWriter] = None
    upstream_credential: Optional[dict] = None

    # --- Envelope tracking -------------------------------------------------
    sender: Optional[str] = None
    recipients: list[str] = []

    # --- Command loop ------------------------------------------------------
    try:
        while True:
            try:
                line_bytes = await client_reader.readline()
            except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
                break
            if not line_bytes:
                break

            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            cmd = line.split()[0].upper() if line.split() else ""

            logger.debug("[%s] C→P: %s", peer_str, line)

            # ----------------------------------------------------------------
            # EHLO / HELO — respond locally before upstream is connected
            # ----------------------------------------------------------------
            if cmd in ("EHLO", "HELO"):
                client_writer.write(
                    b"250-Nuvrail proxy\r\n"
                    b"250-AUTH PLAIN LOGIN\r\n"
                    b"250-SIZE 52428800\r\n"
                    b"250 8BITMIME\r\n"
                )
                await client_writer.drain()
                continue

            # ----------------------------------------------------------------
            # AUTH — verify agent credentials, then open upstream connection
            # ----------------------------------------------------------------
            if cmd == "AUTH":
                parts = line.split()
                mech = parts[1].upper() if len(parts) > 1 else ""
                cred: Optional[dict] = None

                if mech == "PLAIN" and len(parts) >= 3:
                    import base64 as _b64  # noqa: PLC0415
                    try:
                        decoded = _b64.b64decode(parts[2])
                        auth_parts = decoded.split(b"\x00")
                        agent_user = auth_parts[1].decode("utf-8", errors="replace") if len(auth_parts) >= 3 else ""
                        agent_pass_plain = auth_parts[2].decode("utf-8", errors="replace") if len(auth_parts) >= 3 else ""
                    except Exception:
                        agent_user, agent_pass_plain = "", ""
                    cred = await _verify_smtp_agent_credential(agent_user, agent_pass_plain)
                    if cred:
                        logger.info("[%s] SMTP agent authenticated: %s", peer_str, agent_user)
                    else:
                        logger.warning("[%s] SMTP AUTH PLAIN failed for user=%s", peer_str, agent_user)

                elif mech == "PLAIN" and len(parts) < 3:
                    # AUTH PLAIN without inline credentials — send challenge
                    client_writer.write(b"334 \r\n")
                    await client_writer.drain()
                    cred_line = await client_reader.readline()
                    import base64 as _b64c  # noqa: PLC0415
                    try:
                        decoded = _b64c.b64decode(cred_line.strip())
                        auth_parts = decoded.split(b"\x00")
                        agent_user = auth_parts[1].decode("utf-8", errors="replace") if len(auth_parts) >= 3 else ""
                        agent_pass_plain = auth_parts[2].decode("utf-8", errors="replace") if len(auth_parts) >= 3 else ""
                    except Exception:
                        agent_user, agent_pass_plain = "", ""
                    cred = await _verify_smtp_agent_credential(agent_user, agent_pass_plain)

                elif mech == "LOGIN":
                    # AUTH LOGIN multi-step
                    import base64 as _b64l  # noqa: PLC0415
                    client_writer.write(b"334 " + _b64l.b64encode(b"Username:") + b"\r\n")
                    await client_writer.drain()
                    user_b64 = await client_reader.readline()
                    client_writer.write(b"334 " + _b64l.b64encode(b"Password:") + b"\r\n")
                    await client_writer.drain()
                    pass_b64 = await client_reader.readline()
                    try:
                        agent_user = _b64l.b64decode(user_b64.strip()).decode("utf-8", errors="replace")
                        agent_pass_plain = _b64l.b64decode(pass_b64.strip()).decode("utf-8", errors="replace")
                    except Exception:
                        agent_user, agent_pass_plain = "", ""
                    cred = await _verify_smtp_agent_credential(agent_user, agent_pass_plain)

                else:
                    client_writer.write(b"504 Authentication mechanism not supported\r\n")
                    await client_writer.drain()
                    continue

                if cred is None:
                    client_writer.write(b"535 5.7.8 Authentication credentials invalid\r\n")
                    await client_writer.drain()
                    logger.warning("[%s] SMTP auth failed — closing", peer_str)
                    break

                # Auth succeeded — open upstream using per-agent host/port
                upstream_host = cred["upstream_host"]
                upstream_port = int(cred["upstream_smtp_port"])
                up_user = cred["upstream_user"]
                up_pass = decrypt_credential(cred["upstream_password"])

                try:
                    upstream_reader, upstream_writer, _ = await _connect_upstream_starttls(
                        upstream_host, upstream_port
                    )
                except Exception as exc:
                    logger.error(
                        "[%s] Failed to connect to upstream %s:%d — %s",
                        peer_str, upstream_host, upstream_port, exc,
                    )
                    client_writer.write(b"421 Service temporarily unavailable\r\n")
                    await client_writer.drain()
                    break

                # Drain the post-STARTTLS EHLO response
                await _read_smtp_response(upstream_reader)

                # Authenticate to upstream with real credentials
                import base64 as _b64u  # noqa: PLC0415
                rewritten_b64 = _b64u.b64encode(
                    f"\x00{up_user}\x00{up_pass}".encode()
                ).decode()
                upstream_writer.write(f"AUTH PLAIN {rewritten_b64}\r\n".encode())
                await upstream_writer.drain()
                upstream_auth_resp = await _read_smtp_response(upstream_reader)
                if not upstream_auth_resp or not upstream_auth_resp[0].startswith(b"235"):
                    logger.error("[%s] Upstream AUTH failed: %r", peer_str, upstream_auth_resp)
                    client_writer.write(b"535 5.7.8 Upstream authentication failed\r\n")
                    await client_writer.drain()
                    break

                upstream_credential = cred
                client_writer.write(b"235 2.7.0 Authentication succeeded\r\n")
                await client_writer.drain()
                logger.info(
                    "[%s] Upstream authenticated: %s:%d user=%s",
                    peer_str, upstream_host, upstream_port, up_user,
                )
                continue

            # ----------------------------------------------------------------
            # Require auth before any envelope commands
            # ----------------------------------------------------------------
            if upstream_writer is None:
                client_writer.write(b"530 5.7.0 Authentication required\r\n")
                await client_writer.drain()
                continue

            # ----------------------------------------------------------------
            # MAIL FROM — track sender, pass through
            # ----------------------------------------------------------------
            if cmd == "MAIL":
                m = _MAIL_FROM_RE.search(line)
                if m:
                    sender = m.group(1)
                    logger.debug("[%s] MAIL FROM: %s", peer_str, sender)
                upstream_writer.write(line_bytes)
                await upstream_writer.drain()
                resp_lines = await _read_smtp_response(upstream_reader)
                client_writer.write(b"".join(resp_lines))
                await client_writer.drain()

            # ----------------------------------------------------------------
            # RCPT TO — accumulate recipients, pass through
            # ----------------------------------------------------------------
            elif cmd == "RCPT":
                m = _RCPT_TO_RE.search(line)
                if m:
                    recipients.append(m.group(1))
                    logger.debug("[%s] RCPT TO: %s", peer_str, m.group(1))
                upstream_writer.write(line_bytes)
                await upstream_writer.drain()
                resp_lines = await _read_smtp_response(upstream_reader)
                client_writer.write(b"".join(resp_lines))
                await client_writer.drain()

            # ----------------------------------------------------------------
            # DATA — intercept: consume body, do NOT forward, return STAGED
            # ----------------------------------------------------------------
            elif cmd == "DATA":
                # 1. Forward DATA command to upstream
                upstream_writer.write(b"DATA\r\n")
                await upstream_writer.drain()

                # 2. Read upstream 354 "go ahead" — don't forward to client
                data_go_ahead = await _read_smtp_response(upstream_reader)
                logger.debug(
                    "[%s] Upstream DATA response: %r", peer_str, b"".join(data_go_ahead)
                )

                if not data_go_ahead or not data_go_ahead[0].startswith(b"354"):
                    # Upstream rejected DATA; relay the error to the client
                    client_writer.write(b"".join(data_go_ahead))
                    await client_writer.drain()
                    continue

                # 3. Send 354 to client so it starts sending the body
                client_writer.write(b"354 Start mail input; end with <CRLF>.<CRLF>\r\n")
                await client_writer.drain()

                # 4. Consume message body from client until terminator line ".\r\n"
                body_lines: list[bytes] = []
                byte_count = 0
                while True:
                    body_line = await client_reader.readline()
                    if not body_line:
                        break
                    if body_line in (b".\r\n", b".\n"):
                        break
                    body_lines.append(body_line)
                    byte_count += len(body_line)

                # 5. Extract subject for logging
                subject = _extract_subject(body_lines)
                logger.info(
                    "[%s] STAGED DATA: from=%s to=%s subject=%r bytes=%d",
                    peer_str,
                    sender or "<unknown>",
                    recipients,
                    subject,
                    byte_count,
                )

                # 6. Do NOT forward body or terminating "." to upstream

                # 7. Create Operation record in staging engine
                body_text = b"".join(body_lines).decode("utf-8", errors="replace")
                body_preview = body_text[:200]
                try:
                    op_id = await create_operation(
                        op_type="smtp_send",
                        protocol="smtp",
                        description=(
                            f"Send email to {', '.join(recipients or ['<unknown>'])} "
                            f"— Subject: \"{subject}\""
                        ),
                        smtp_envelope={
                            "from": sender or "",
                            "to": recipients,
                            "subject": subject,
                            "body_preview": body_preview,
                        },
                    )
                    staged_resp = (
                        f"250 OK [STAGED] Send queued for approval — ID: {op_id}\r\n"
                    )
                except Exception as exc:
                    logger.error("[%s] Failed to stage SMTP operation: %s", peer_str, exc)
                    staged_resp = "250 OK [STAGED] Send queued for approval — ID: pending\r\n"

                # 8. Return STAGED response to client
                client_writer.write(staged_resp.encode())
                await client_writer.drain()

                # 9. Reset envelope tracking for potential next message
                sender = None
                recipients = []

            # ----------------------------------------------------------------
            # QUIT — pass through, then close both sides
            # ----------------------------------------------------------------
            elif cmd == "QUIT":
                upstream_writer.write(line_bytes)
                await upstream_writer.drain()
                resp_lines = await _read_smtp_response(upstream_reader)
                client_writer.write(b"".join(resp_lines))
                await client_writer.drain()
                break

            # ----------------------------------------------------------------
            # Everything else (EHLO, HELO, NOOP, RSET, VRFY, etc.) — pass through
            # ----------------------------------------------------------------
            else:
                upstream_writer.write(line_bytes)
                await upstream_writer.drain()
                resp_lines = await _read_smtp_response(upstream_reader)
                resp_bytes = b"".join(resp_lines)

                # Strip STARTTLS from EHLO capabilities — client is already plain TCP;
                # advertising STARTTLS would confuse clients into attempting an upgrade
                # over a channel that is already plaintext on the client side.
                if cmd in ("EHLO", "HELO"):
                    resp_bytes = b"".join(_strip_starttls(list(resp_lines)))

                client_writer.write(resp_bytes)
                await client_writer.drain()

    except (ConnectionResetError, BrokenPipeError, OSError) as exc:
        logger.warning("[%s] Connection error: %s", peer_str, exc)
    finally:
        for w in (upstream_writer, client_writer):
            try:
                w.close()
                await w.wait_closed()
            except OSError:
                pass
        logger.info("[%s] SMTP client disconnected", peer_str)


# ---------------------------------------------------------------------------
# Server entry points
# ---------------------------------------------------------------------------


async def start_smtp_proxy(host: str, port: int) -> asyncio.AbstractServer:
    """Start the SMTP proxy and return the Server object.

    Separated from main() so tests can bind to port 0 and retrieve the
    ephemeral port via server.sockets[0].getsockname()[1].
    """
    server = await asyncio.start_server(handle_smtp_client, host, port)
    actual = server.sockets[0].getsockname()
    logger.info(
        "Nuvrail SMTP proxy listening on %s:%d (upstream host/port per agent credential)",
        actual[0],
        actual[1],
    )
    return server


async def main() -> None:
    """Entry point: read config from env and start the proxy."""
    host = os.environ.get("NUVRAIL_PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("NUVRAIL_PROXY_SMTP_PORT", "10587"))

    server = await start_smtp_proxy(host, port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
