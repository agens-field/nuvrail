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
import json
import logging
import os
import re
import ssl
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from gateway.command_router import classify
from gateway.imap_parser import ParsedCommand, parse_line
from gateway.imap_response_parser import parse_fetch_line, parse_list_response, parse_select_response
from gateway.operation_parser import ParsedOperation, parse_append, parse_copy, parse_move, parse_store
from gateway.credentials import decrypt_credential
from gateway.staging import create_operation
from gateway.state_db import (
    DB_PATH,
    apply_optimistic_flag_update,
    get_db,
    get_message,
    get_pending_move_uids_for_folder,
    get_pending_reverts,
    init_db,
    mark_reverts_delivered,
    remove_messages_from_folder,
    snapshot_messages,
    update_folder_stats,
    upsert_folders_from_list,
    upsert_message,
)

load_dotenv()

logger = logging.getLogger(__name__)

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


async def _inject_pending_reverts(
    client_writer: asyncio.StreamWriter,
    session: dict,
    db_path: Path,
    peer: str,
) -> None:
    """Inject unsolicited FETCH responses for any pending rejected operations.

    Called after the tagged OK for SELECT/NOOP/FETCH is forwarded to the client.
    The injected lines arrive after the tagged OK — valid per RFC 3501 §7 (unsolicited
    responses may be sent at any time). From the AI's perspective, another client
    modified the mailbox between commands — standard IMAP sync behavior.

    Injection format:
      * {seq_num} FETCH (UID {uid} FLAGS ({flags}))

    Errors are always swallowed — never propagated to the byte pump.
    """
    folder_id = session.get("folder_id")
    if folder_id is None:
        return
    try:
        reverts = await get_pending_reverts(folder_id, db_path=db_path)
        if not reverts:
            return
        lines: list[bytes] = []
        ids_to_mark: list[int] = []
        for r in reverts:
            uid = r["uid"]
            msg = await get_message(folder_id, uid, db_path=db_path)
            seq_num = msg["sequence_num"] if msg and msg.get("sequence_num") else 1
            true_flags = json.loads(r["true_flags"]) if r["true_flags"] else []
            flags_str = " ".join(true_flags)
            line = f"* {seq_num} FETCH (UID {uid} FLAGS ({flags_str}))\r\n"
            lines.append(line.encode())
            ids_to_mark.append(r["id"])
        # Write all injected lines in one go
        for line_bytes in lines:
            client_writer.write(line_bytes)
        await client_writer.drain()
        await mark_reverts_delivered(ids_to_mark, db_path=db_path)
        logger.info(
            "[%s] Injected %d unsolicited FETCH revert(s) for folder_id=%s",
            peer, len(lines), folder_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] _inject_pending_reverts failed (non-fatal): %s", peer, exc)


async def _sync_upstream_line(
    raw_line: str,
    session: dict,
    db_path: Path,
    peer: str,
) -> None:
    """Inspect one upstream response line and update the local state DB.

    Session dict shape:
      {
        'folder':             str | None,   # currently selected folder name
        'folder_id':          int | None,   # cached DB id for current folder
        'select_lines':       list[str],    # untagged lines accumulated for SELECT
        'in_select':          bool,         # True while accumulating SELECT lines
        'revert_trigger_tag': str | None,   # tag of last SELECT/NOOP/FETCH sent by AI
      }

    Response handling:
      SELECT — accumulate untagged lines, flush when tagged OK [READ-WRITE/READ-ONLY]
      LIST   — parse folder name, upsert into folders table
      FETCH  — parse UID/flags/envelope, upsert into messages table

    Revert injection is handled by _upstream_to_client BEFORE forwarding the tagged
    OK line — so the AI receives [* N FETCH ...][tag OK] in the right order.

    Errors are logged at WARNING but never re-raised.
    """
    stripped = raw_line.rstrip("\r\n")

    # ------------------------------------------------------------------
    # SELECT accumulation
    # ------------------------------------------------------------------
    if stripped.startswith("* ") and session.get("in_select"):
        session["select_lines"].append(stripped)
        return

    # Untagged lines that signal we're in a SELECT response
    _select_untagged = re.compile(
        r"^\* (?:\d+ (?:EXISTS|RECENT)|OK \[(?:UIDVALIDITY|UIDNEXT|UNSEEN|PERMANENTFLAGS|FLAGS))",
        re.IGNORECASE,
    )
    if _select_untagged.match(stripped):
        session["select_lines"].append(stripped)
        session["in_select"] = True
        return

    # Tagged OK ending a SELECT (e.g. "A001 OK [READ-WRITE] SELECT completed")
    _select_ok = re.compile(r"^[A-Za-z0-9]+ OK \[READ-(?:WRITE|ONLY)\]", re.IGNORECASE)
    if _select_ok.match(stripped) and session.get("in_select") and session.get("folder"):
        lines = session.pop("select_lines", [])
        session["select_lines"] = []
        session["in_select"] = False
        try:
            info = parse_select_response(lines)
            folder_id = await update_folder_stats(
                session["folder"],
                exists_count=info.exists,
                recent_count=info.recent,
                uidvalidity=info.uidvalidity,
                uidnext=info.uidnext,
                unseen_count=info.unseen,
                db_path=db_path,
            )
            session["folder_id"] = folder_id
            logger.debug(
                "[%s] Synced folder %r: exists=%s uidvalidity=%s",
                peer, session["folder"], info.exists, info.uidvalidity,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Failed to sync SELECT stats: %s", peer, exc)
        return

    # ------------------------------------------------------------------
    # LIST handling
    # ------------------------------------------------------------------
    if re.match(r"^\* LIST\b", stripped, re.IGNORECASE):
        try:
            names = parse_list_response([stripped])
            if names:
                await upsert_folders_from_list(names, db_path=db_path)
                logger.debug("[%s] LIST: upserted folders %s", peer, names)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Failed to sync LIST line: %s", peer, exc)
        return

    # ------------------------------------------------------------------
    # FETCH handling — only single-line FETCH with FLAGS/UID/ENVELOPE
    # ------------------------------------------------------------------
    if re.match(r"^\* \d+ FETCH\b", stripped, re.IGNORECASE):
        # Skip if it looks like a literal continuation {N}
        if re.search(r"\{\d+\}$", stripped):
            return
        folder_id = session.get("folder_id")
        if folder_id is None:
            return
        try:
            info = parse_fetch_line(stripped)
            if info is None or info.uid is None:
                return
            await upsert_message(
                folder_id,
                info.uid,
                seq_num=info.seq_num,
                flags=info.flags,
                subject=info.subject,
                sender=info.sender,
                size=info.size,
                db_path=db_path,
            )
            logger.debug(
                "[%s] FETCH synced uid=%s flags=%s", peer, info.uid, info.flags
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Failed to sync FETCH line: %s", peer, exc)


async def _verify_agent_credential(
    agent_user: str, agent_pass: str, db_path: Path
) -> Optional[dict]:
    """Verify agent username + password against agent_credentials table.

    Returns the credential row if valid and not revoked, None otherwise.
    bcrypt verification uses rounds=10 (faster than human passwords — verified
    per-connection on every IMAP LOGIN).
    """
    async with get_db(db_path) as db:
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
    return dict(row)


async def _client_to_upstream(
    client_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
    client_writer: asyncio.StreamWriter,
    session: dict,
    peer: str,
    db_path: Path = DB_PATH,
) -> None:
    """Parse each client line and route: read→upstream, write/blocked→intercept.

    AUTH/LOGIN is handled upstream in handle_client before this coroutine starts.
    All commands arriving here are post-authentication.

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
                    agent_id=session.get("agent_id"),
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
            # Track SELECT so the u2c pump knows which folder we're in.
            if parsed.command.upper() in ("SELECT", "EXAMINE"):
                folder_name = parsed.args[0].strip('"') if parsed.args else None
                if folder_name:
                    session["folder"] = folder_name
                    session["folder_id"] = None
                    session["select_lines"] = []
                    session["in_select"] = False

            # Track which commands should trigger pending_reverts injection.
            # The u2c pump will call _inject_pending_reverts when it sees
            # the tagged OK for one of these commands.
            if parsed.command.upper() in ("SELECT", "EXAMINE", "NOOP", "FETCH"):
                session["revert_trigger_tag"] = parsed.tag.upper()

            # Pass through to upstream; the u2c pump returns the response.
            try:
                upstream_writer.write(line_bytes)
                await upstream_writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

        elif action == "write":
            # Reject non-UID write commands.
            # Sequence numbers are positional and shift whenever messages are
            # added/removed from a folder. Staged operations may be held
            # pending approval for up to 48 hours — a sequence number recorded
            # at staging time can point to a completely different message by
            # execution time. UID addressing is stable across sessions and
            # mailbox changes. All write commands must use UID prefix.
            if not parsed.uid and parsed.command.upper() in ("STORE", "COPY", "MOVE"):
                try:
                    client_writer.write(
                        f"{parsed.tag} NO [CLIENTBUG] Use UID {parsed.command} not "
                        f"{parsed.command} — sequence numbers shift when messages move "
                        f"and are unsafe for staged approval.\r\n".encode()
                    )
                    await client_writer.drain()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    break
                logger.warning("[%s] Rejected non-UID write: %s", peer, raw)
                continue

            # Intercept — don't forward, stage the operation, respond locally.
            try:
                parsed_op = _build_parsed_op(parsed)
                if parsed_op is not None:
                    # For flag operations (STORE), take a pre-op snapshot of
                    # affected messages and apply the optimistic update so the
                    # AI sees the proposed state immediately. The snapshot is
                    # stored in staged_operations for later revert on rejection.
                    op_snapshot: Optional[dict] = None
                    folder_id = session.get("folder_id")
                    is_flag_op = parsed.command.upper() in ("STORE",) and (
                        parsed_op.flags_add or parsed_op.flags_remove
                    )
                    # If folder_id isn't cached yet (SELECT response still in flight),
                    # look it up synchronously so we can still take a snapshot.
                    if is_flag_op and folder_id is None and session.get("folder"):
                        try:
                            from gateway.state_db import get_or_create_folder
                            folder_id = await get_or_create_folder(
                                session["folder"], db_path=db_path
                            )
                            session["folder_id"] = folder_id
                        except Exception:
                            pass
                    if is_flag_op and folder_id is not None and parsed_op.message_ids:
                        uid_set_str = parsed_op.message_ids[0] if len(parsed_op.message_ids) == 1 else ",".join(parsed_op.message_ids)
                        try:
                            op_snapshot = await snapshot_messages(
                                folder_id, uid_set_str, db_path=db_path
                            )
                            # If the message isn't in the state DB yet (not yet FETCH'd
                            # through the proxy), synthesize a minimal snapshot using the
                            # known message_ids. For +FLAGS ops, pre-op state is "no flags"
                            # for unknown messages — this ensures revert can still fire.
                            if not op_snapshot:
                                for uid_str in parsed_op.message_ids:
                                    op_snapshot[uid_str] = {
                                        "flags": [],
                                        "seq_num": None,
                                        "folder_id": folder_id,
                                    }
                                logger.debug(
                                    "[%s] Messages not in state DB — synthesised snapshot for %s UIDs",
                                    peer, len(op_snapshot),
                                )
                            await apply_optimistic_flag_update(
                                folder_id,
                                uid_set_str,
                                flags_add=parsed_op.flags_add or [],
                                flags_remove=parsed_op.flags_remove or [],
                                db_path=db_path,
                            )
                            logger.debug(
                                "[%s] Snapshot captured + optimistic update applied for %s UIDs",
                                peer, len(op_snapshot),
                            )
                        except Exception as snap_exc:
                            logger.warning(
                                "[%s] Snapshot/optimistic update failed (non-fatal): %s",
                                peer, snap_exc,
                            )
                            op_snapshot = None

                    # MOVE optimistic update: remove message from source folder
                    # in local state DB so the agent sees a consistent view
                    # (message appears moved immediately, before human approves).
                    # The snapshot captured above enables full rollback on rejection.
                    is_move_op = parsed.command.upper() == "MOVE" and parsed_op.message_ids
                    if is_move_op and folder_id is not None and parsed_op.message_ids:
                        uid_set_str = parsed_op.message_ids[0]
                        try:
                            # Snapshot first (if not already taken by flag-op path)
                            if op_snapshot is None:
                                op_snapshot = await snapshot_messages(
                                    folder_id, uid_set_str, db_path=db_path
                                )
                            await remove_messages_from_folder(
                                folder_id, uid_set_str, db_path=db_path
                            )
                            logger.debug(
                                "[%s] MOVE staged: removed UIDs %s from local state (folder_id=%s)",
                                peer, uid_set_str, folder_id,
                            )
                        except Exception as move_exc:  # noqa: BLE001
                            logger.warning(
                                "[%s] MOVE optimistic update failed (non-fatal): %s",
                                peer, move_exc,
                            )

                    op_id = await create_operation(
                        op_type=parsed_op.op_type,
                        protocol="imap",
                        agent_id=session.get("agent_id"),
                        description=parsed_op.description,
                        imap_command=parsed_op.imap_command,
                        message_ids=parsed_op.message_ids if parsed_op.message_ids else None,
                        # folder_from: use parsed value if set (COPY/MOVE
                        # parse_move doesn't know the current mailbox), fall
                        # back to the session's selected folder.
                        folder_from=parsed_op.folder_from or session.get("folder"),
                        folder_to=parsed_op.folder_to,
                        flags_add=parsed_op.flags_add if parsed_op.flags_add else None,
                        flags_remove=parsed_op.flags_remove if parsed_op.flags_remove else None,
                        snapshot=op_snapshot,
                    )
                    resp = f"{parsed.tag} OK [STAGED] Operation queued — ID: {op_id}\r\n"
                else:
                    # CREATE / RENAME or unknown write — stage generically (no snapshot)
                    op_id = await create_operation(
                        op_type=parsed.command.lower(),
                        protocol="imap",
                        agent_id=session.get("agent_id"),
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


_REVERT_TRIGGER_OK_RE = re.compile(r"^([A-Za-z0-9]+)\s+OK\b", re.IGNORECASE)


async def _upstream_to_client(
    upstream_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    session: dict,
    db_path: Path,
    peer: str,
) -> None:
    """Forward upstream bytes to client, tapping each line for state sync.

    Data flow for most lines:
      line arrives → forwarded to client immediately → _sync_upstream_line (best-effort)

    Data flow for tagged OK on revert-trigger commands (SELECT/NOOP/FETCH):
      line arrives → _inject_pending_reverts (inject unsolicited FETCH lines first)
                   → tagged OK forwarded to client
                   → _sync_upstream_line for state sync

    This ensures injected unsolicited FETCH lines arrive BEFORE the tagged OK that
    the AI is waiting on. The AI sees: [* N FETCH ...][tag OK] — standard IMAP
    unsolicited response ordering, no extensions required.

    The line-buffering delay is minimal (~microseconds per line) because upstream
    IMAP responses are line-terminated and we read in 4KB chunks.
    """
    buffer = b""
    while True:
        try:
            chunk = await upstream_reader.read(_READ_CHUNK)
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            break
        if not chunk:
            break

        buffer += chunk

        while b"\r\n" in buffer:
            line_bytes, buffer = buffer.split(b"\r\n", 1)
            line = line_bytes.decode("utf-8", errors="replace")
            line_with_crlf = line_bytes + b"\r\n"

            # Check if this is a tagged OK for a revert-trigger command.
            # If so, inject pending reverts BEFORE forwarding the tagged OK,
            # so the AI receives: [* N FETCH (UID M FLAGS (...))] then [tag OK].
            revert_tag = session.get("revert_trigger_tag")
            if revert_tag:
                m = _REVERT_TRIGGER_OK_RE.match(line)
                if m and m.group(1).upper() == revert_tag:
                    session["revert_trigger_tag"] = None
                    await _inject_pending_reverts(client_writer, session, db_path, peer)

            # Suppress FETCH lines for UIDs that are pending MOVE from the
            # current folder. This keeps the agent's view consistent with its
            # staged operations — the message appears already moved without
            # waiting for human approval.
            if re.match(r"^\* \d+ FETCH\b", line, re.IGNORECASE):
                folder_name = session.get("folder")
                if folder_name:
                    try:
                        pending_move_uids = await get_pending_move_uids_for_folder(
                            folder_name, db_path=db_path
                        )
                        if pending_move_uids:
                            fetch_info = parse_fetch_line(line)
                            if fetch_info and fetch_info.uid in pending_move_uids:
                                logger.debug(
                                    "[%s] Suppressing FETCH for pending-move UID %s",
                                    peer, fetch_info.uid,
                                )
                                # Still sync state but don't forward to client
                                try:
                                    await _sync_upstream_line(line, session, db_path, peer)
                                except Exception:  # noqa: BLE001
                                    pass
                                continue
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[%s] Pending-move filter error (non-fatal): %s", peer, exc)

            # Forward this line to the client.
            try:
                client_writer.write(line_with_crlf)
                await client_writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                return

            # State sync (best-effort, errors never propagate).
            try:
                await _sync_upstream_line(line, session, db_path, peer)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] State sync error: %s", peer, exc)

        # If there are buffered bytes that don't form a complete line yet
        # (partial line from upstream), forward them immediately so the client
        # doesn't stall. These partial bytes will be re-buffered on the next chunk.
        if buffer:
            try:
                client_writer.write(buffer)
                await client_writer.drain()
                buffer = b""
            except (ConnectionResetError, BrokenPipeError, OSError):
                break


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """
    Handle one client connection.

    Auth-before-upstream flow:

      Client connects
          │
          ▼
      Proxy sends synthetic greeting (no upstream yet)
          │
          ▼
      Wait for LOGIN command
          │  verify agent_username + token against agent_credentials
          ├─ fail → BYE + close
          │
          ▼
      Open SSL connection to upstream using credential.upstream_host/port
          │
          ▼
      Rewrite LOGIN with upstream_user + upstream_password → forward
          │
          ▼
      Bidirectional pump (c2u + u2c tasks)

    This means the upstream host/port comes from the per-agent credential row,
    not from a static env var — each agent can point to a different IMAP server.
    """
    peer = client_writer.get_extra_info("peername", ("?", 0))
    peer_str = f"{peer[0]}:{peer[1]}"
    logger.info("[%s] Client connected", peer_str)

    import gateway.state_db as _state_db_mod
    _db_path = _state_db_mod.DB_PATH

    # Safety net: init_db is idempotent.
    try:
        await init_db(_db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] init_db safety call failed: %s", peer_str, exc)

    # --- Step 1: Send synthetic greeting to client (no upstream yet) ----------
    try:
        client_writer.write(b"* OK Nuvrail IMAP proxy ready\r\n")
        await client_writer.drain()
    except OSError as exc:
        logger.error("[%s] Failed to send greeting: %s", peer_str, exc)
        client_writer.close()
        return

    # --- Step 2: Wait for LOGIN and verify agent credentials ------------------
    credential: Optional[dict] = None
    login_tag: str = "*"
    login_line_bytes: bytes = b""

    while credential is None:
        try:
            line_bytes = await client_reader.readline()
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            client_writer.close()
            return
        if not line_bytes:
            client_writer.close()
            return

        raw = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
        parts = raw.split()
        if not parts:
            continue

        cmd = parts[1].upper() if len(parts) > 1 else ""

        if cmd == "CAPABILITY":
            # Some clients issue CAPABILITY before LOGIN; respond locally.
            tag = parts[0]
            try:
                client_writer.write(
                    f"* CAPABILITY IMAP4rev1 AUTH=PLAIN AUTH=LOGIN\r\n"
                    f"{tag} OK CAPABILITY completed\r\n".encode()
                )
                await client_writer.drain()
            except OSError:
                client_writer.close()
                return
            continue

        if cmd == "LOGOUT":
            tag = parts[0]
            try:
                client_writer.write(
                    f"* BYE Logging out\r\n"
                    f"{tag} OK LOGOUT completed\r\n".encode()
                )
                await client_writer.drain()
            except OSError:
                pass
            client_writer.close()
            return

        if cmd not in ("LOGIN", "AUTHENTICATE"):
            # Reject any other command before authentication
            tag = parts[0]
            try:
                client_writer.write(
                    f"{tag} NO Please authenticate first\r\n".encode()
                )
                await client_writer.drain()
            except OSError:
                client_writer.close()
                return
            continue

        # LOGIN command: tag LOGIN userid password
        # AUTHENTICATE PLAIN command: tag AUTHENTICATE PLAIN [base64]
        # Clients may quote LOGIN arguments. Strip surrounding double-quotes.
        login_tag = parts[0]

        if cmd == "AUTHENTICATE":
            mech = parts[2].upper() if len(parts) > 2 else ""
            if mech != "PLAIN":
                try:
                    client_writer.write(
                        f"{login_tag} NO Unsupported auth mechanism — use LOGIN or AUTHENTICATE PLAIN\r\n".encode()
                    )
                    await client_writer.drain()
                except OSError:
                    client_writer.close()
                    return
                continue

            # AUTHENTICATE PLAIN may have inline base64 or use a challenge
            if len(parts) > 3:
                b64_payload = parts[3]
            else:
                # Send challenge, wait for client response
                try:
                    client_writer.write(b"+ \r\n")
                    await client_writer.drain()
                    resp_line = await client_reader.readline()
                    b64_payload = resp_line.decode("utf-8", errors="replace").strip()
                except (OSError, asyncio.IncompleteReadError):
                    client_writer.close()
                    return

            import base64 as _b64  # noqa: PLC0415
            try:
                decoded = _b64.b64decode(b64_payload)
                auth_parts = decoded.split(b"\x00")
                agent_user = auth_parts[1].decode("utf-8", errors="replace") if len(auth_parts) >= 3 else ""
                agent_pass = auth_parts[2].decode("utf-8", errors="replace") if len(auth_parts) >= 3 else ""
            except Exception:
                agent_user, agent_pass = "", ""

        else:
            # LOGIN command
            agent_user = parts[2].strip('"') if len(parts) > 2 else ""
            # Password may contain spaces if quoted — rejoin and strip quotes
            agent_pass = " ".join(parts[3:]).strip('"') if len(parts) > 3 else ""

        credential = await _verify_agent_credential(agent_user, agent_pass, _db_path)
        if credential is None:
            try:
                client_writer.write(
                    f"* BYE Authentication failed\r\n"
                    f"{login_tag} NO Authentication credentials invalid\r\n".encode()
                )
                await client_writer.drain()
            except OSError:
                pass
            logger.warning("[%s] Agent auth failed for user=%s", peer_str, agent_user)
            client_writer.close()
            return

        logger.info(
            "[%s] Agent authenticated: %s → upstream %s:%d user=%s",
            peer_str, agent_user,
            credential["upstream_host"], credential["upstream_imap_port"],
            credential["upstream_user"],
        )

        # Rewrite LOGIN with upstream credentials for forwarding after connection
        upstream_user = credential["upstream_user"]
        upstream_password = decrypt_credential(credential["upstream_password"])
        login_line_bytes = (
            f"{login_tag} LOGIN {upstream_user} {upstream_password}\r\n".encode()
        )

    # --- Step 3: Open upstream connection using per-agent host/port -----------
    upstream_host = credential["upstream_host"]
    upstream_port = int(credential["upstream_imap_port"])
    ssl_ctx = ssl.create_default_context()
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            upstream_host, upstream_port, ssl=ssl_ctx,
        )
    except Exception as exc:
        logger.error(
            "[%s] Failed to connect to upstream %s:%d — %s",
            peer_str, upstream_host, upstream_port, exc,
        )
        try:
            client_writer.write(
                f"{login_tag} NO Upstream connection failed\r\n".encode()
            )
            await client_writer.drain()
        except OSError:
            pass
        client_writer.close()
        return

    # --- Step 4: Consume upstream greeting (not forwarded — client already got ours)
    try:
        await upstream_reader.readline()
    except (OSError, asyncio.IncompleteReadError) as exc:
        logger.error("[%s] Failed to read upstream greeting: %s", peer_str, exc)
        client_writer.close()
        upstream_writer.close()
        return

    # --- Step 5: Forward the rewritten LOGIN to upstream ---------------------
    try:
        upstream_writer.write(login_line_bytes)
        await upstream_writer.drain()
    except (OSError, BrokenPipeError) as exc:
        logger.error("[%s] Failed to forward LOGIN to upstream: %s", peer_str, exc)
        client_writer.close()
        upstream_writer.close()
        return

    # Read the upstream LOGIN response and forward to client
    try:
        login_resp = await upstream_reader.readline()
        client_writer.write(login_resp)
        await client_writer.drain()
    except (OSError, asyncio.IncompleteReadError) as exc:
        logger.error("[%s] Failed to forward LOGIN response: %s", peer_str, exc)
        client_writer.close()
        upstream_writer.close()
        return

    # Per-connection state shared between c2u (tracks SELECT) and u2c (syncs responses).
    session: dict = {
        "folder": None,              # currently selected folder name
        "folder_id": None,           # cached DB id for current folder
        "select_lines": [],          # untagged lines accumulate during SELECT
        "in_select": False,          # True while accumulating SELECT response lines
        "revert_trigger_tag": None,  # tag of last SELECT/NOOP/FETCH (triggers revert injection)
        "agent_id": credential["id"],  # agent_credentials.id for staging
    }
    c2u = asyncio.create_task(
        _client_to_upstream(client_reader, upstream_writer, client_writer, session, peer_str, _db_path)
    )
    u2c = asyncio.create_task(
        _upstream_to_client(upstream_reader, client_writer, session, _db_path, peer_str)
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
        "Nuvrail IMAP proxy listening on %s:%d (upstream host/port per agent credential)",
        actual[0], actual[1],
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
