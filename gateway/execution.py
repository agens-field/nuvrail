"""
Upstream execution of approved operations — SMTP relay / IMAP replay.

This is the single place that actually carries a staged operation out against
the upstream mail server. It is shared by:

  - the manual approval endpoints (``api.routes.operations``), and
  - the auto-approval rule path (``gateway.staging``),

so a human-approved operation and an auto-approved operation execute through
exactly the same code.

It lives in ``gateway/`` (not ``api/``) so the proxy processes and the staging
engine can import it without pulling in FastAPI, and it raises a plain
``ExecutionError`` rather than an HTTP exception — the HTTP layer translates
that into a 500 itself.

On success: status → ``executed`` and an ``executed`` audit row is written.
On failure: status → ``failed``, an ``execution_failed`` audit row is written,
and ``ExecutionError`` is raised.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import email.message

import aioimaplib
from aioimaplib import quoted as imap_quoted
import aiosmtplib

from gateway.agent_auth import get_agent_credential
from gateway.audit import record_audit_event
from gateway.send_rate_limiter import (
    SendRateLimitExceeded,
    enforce_send_rate,
)
from gateway.staging import get_operation, update_operation_status
from gateway.state_db import decode_json_list, get_db

logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    """Raised when upstream execution of an approved operation fails.

    For relay/replay failures the operation has already been marked ``failed``
    and an ``execution_failed`` audit row written before this is raised. For
    pre-flight failures (e.g. missing credentials) it is raised before any
    status change, leaving the operation untouched.
    """


# ---------------------------------------------------------------------------
# Shared helpers — used by both forward execution and undo (gateway.undo)
# ---------------------------------------------------------------------------


@dataclass
class ImapCredentials:
    """Resolved upstream IMAP connection details for one operation."""

    host: str
    port: int
    user: str
    password: Optional[str]          # decrypted; None for OAuth2 agents
    oauth2_provider: Optional[str]   # e.g. 'google'; None for password auth
    cred: Optional[dict]             # the agent_credentials row, or None (env fallback)


async def resolve_imap_credentials(row: dict, db_path: Path) -> ImapCredentials:
    """Resolve the upstream IMAP credentials for a staged operation.

    Uses the operation's agent_credentials row when present (decrypting the
    stored password), otherwise falls back to the NUVRAIL_TEST_IMAP_* env vars
    (for ops staged before agent_id tracking, and for integration tests).

    Raises RuntimeError if neither an agent nor a fallback host is available.
    """
    from gateway.credentials import fetch_credential  # noqa: PLC0415

    cred = await get_agent_credential(row.get("agent_id"), db_path)
    if cred:
        raw_pass = cred.get("upstream_password")
        return ImapCredentials(
            host=cred["upstream_host"],
            port=int(cred["upstream_imap_port"]),
            user=cred["upstream_user"],
            password=await fetch_credential(raw_pass) if raw_pass else None,
            oauth2_provider=cred.get("oauth2_provider"),
            cred=cred,
        )

    host = os.environ.get("NUVRAIL_TEST_IMAP_HOST", "")
    if not host:
        raise RuntimeError(
            f"Operation {row.get('id')} has no agent_id and no fallback IMAP env vars set"
        )
    return ImapCredentials(
        host=host,
        port=int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993")),
        user=os.environ.get("NUVRAIL_TEST_IMAP_USER", ""),
        password=os.environ.get("NUVRAIL_TEST_IMAP_PASS", ""),
        oauth2_provider=None,
        cred=None,
    )


# ---------------------------------------------------------------------------
# Sent-folder discovery
# ---------------------------------------------------------------------------

_LIST_LINE_RE = re.compile(
    r"\("                         # opening paren
    r"([^)]*)"                    # flags  (group 1)
    r"\)\s+"
    r'(?:"[^"]*"|NIL)\s+'        # delimiter (quoted or NIL — discard)
    r'(?:"([^"]+)"|(\S+))'       # folder name: quoted (group 2) or bare (group 3)
    r"\s*$",
    re.IGNORECASE,
)


def _parse_list_line(line: bytes) -> "tuple[set[str], str | None]":
    """
    Parse one raw ``* LIST`` response line into (flags, folder_name).

    IMAP LIST lines look like::

        * LIST (\\Sent \\HasNoChildren) "." "Sent"
        * LIST (\\Sent) "/" "[Gmail]/Sent Mail"
        * LIST (\\HasNoChildren) NIL INBOX.Sent

    Returns (set_of_lowercase_flags, folder_name) or (set(), None) on parse
    failure.
    """
    try:
        decoded = line.decode("utf-8", errors="replace").strip()
        m = _LIST_LINE_RE.search(decoded)
        if not m:
            return set(), None
        raw_flags = m.group(1)
        folder = m.group(2) or m.group(3)
        flags = {f.strip().lower() for f in raw_flags.split() if f.strip()}
        return flags, folder
    except Exception:  # noqa: BLE001
        return set(), None


async def _discover_sent_folder(client: "aioimaplib.IMAP4_SSL") -> "str | None":
    """
    Discover the Sent folder on an already-authenticated IMAP client.

    Strategy
    --------
    Pass 1 — LIST "" "*":
        Walk every folder's flags and return the first one carrying the
        ``\\Sent`` special-use attribute (RFC 6154).  All modern servers
        set this; no extra capability is needed.

    Pass 2 — name probe:
        If no ``\\Sent`` flag was found (very old server or quirky config),
        check whether any of the common Sent-folder names exist using
        individual ``LIST "" <name>`` calls.

    Returns the exact folder name as the server advertises it, or None if
    neither pass succeeds.
    """
    # Pass 1: look for \Sent flag in full listing
    status, lines = await client.list("", "*")
    if status == "OK":
        for line in lines:
            if not isinstance(line, bytes):
                continue
            flags, folder = _parse_list_line(line)
            if "\\sent" in flags and folder:
                logger.debug("[sent-discovery] Found via \\Sent flag: %r", folder)
                return folder

    # Pass 2: probe common names
    for candidate in ("Sent", "Sent Items", "Sent Messages", "INBOX.Sent"):
        probe_status, probe_lines = await client.list("", candidate)
        if probe_status == "OK":
            for pline in probe_lines:
                if not isinstance(pline, bytes):
                    continue
                _, found = _parse_list_line(pline)
                if found:
                    logger.debug("[sent-discovery] Found via name probe: %r", found)
                    return found

    logger.warning("[sent-discovery] Could not discover Sent folder")
    return None


async def _update_agent_sent_folder(cred_id: int, folder: str, db_path: Path) -> None:
    """Persist the discovered Sent folder name so subsequent sends skip discovery."""
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE agent_credentials SET sent_folder = ? WHERE id = ?",
            (folder, cred_id),
        )
        await db.commit()


async def _save_to_sent_folder(
    msg: "email.message.Message",
    op_id: str,
    cred: dict,
    db_path: Path,
) -> None:
    """
    Append a copy of the relayed message to the upstream Sent folder.

    Called after a successful SMTP relay. Non-fatal: errors are logged but
    never propagate — the email was already sent and this is best-effort.

    Folder resolution order
    -----------------------
    1. ``cred['sent_folder']``  — cached in DB from a previous discovery.
       Fast path: no IMAP round-trip beyond the APPEND itself.
    2. RFC 6154 ``\\Sent`` flag via ``LIST "" "*"``  — works on all modern
       servers regardless of folder name or locale.
    3. Name probe  — tries Sent / Sent Items / Sent Messages / INBOX.Sent
       for servers that pre-date RFC 6154 or don't set the flag.
    4. Profile default (``profile.sent_folder``)  — hostname-based guess
       of last resort (Generic IMAP → "Sent").

    Once discovered the name is persisted to ``agent_credentials.sent_folder``
    so steps 2-4 only ever run once per agent.

    Skipped entirely for providers that auto-save on relay (Gmail, Outlook —
    their profile has ``sent_folder = None``).
    """
    from gateway.provider_profiles import detect_provider  # noqa: PLC0415

    imap_host = cred["upstream_host"]
    profile = detect_provider(imap_host)

    # Skip for providers that auto-save on relay (Gmail, Outlook)
    if profile.sent_folder is None:
        logger.info(
            "[approve] Sent-folder save skipped op=%s — %s auto-saves on relay",
            op_id, profile.name,
        )
        return

    imap_port = int(cred["upstream_imap_port"])
    imap_user = cred["upstream_user"]
    is_oauth2 = bool(cred.get("oauth2_provider"))

    # Use cached folder if already discovered; otherwise discover via IMAP LIST
    target_folder: "str | None" = cred.get("sent_folder") or None
    if target_folder:
        logger.info(
            "[approve] Sent-folder APPEND start op=%s host=%s folder=%r (cached)",
            op_id, imap_host, target_folder,
        )
    else:
        logger.info(
            "[approve] Sent-folder APPEND start op=%s host=%s (discovery needed)",
            op_id, imap_host,
        )

    client = aioimaplib.IMAP4_SSL(host=imap_host, port=imap_port)
    try:
        await client.wait_hello_from_server()

        # Authenticate
        if is_oauth2:
            from gateway.oauth2_tokens import OAuth2Error, get_access_token  # noqa: PLC0415
            try:
                imap_email, access_token = await get_access_token(str(cred["id"]), db_path)
            except OAuth2Error as exc:
                logger.warning(
                    "[approve] Sent-folder APPEND skipped op=%s — OAuth2 error: %s", op_id, exc
                )
                return
            response = await client.xoauth2(imap_email, access_token)
            if response.result != "OK":
                logger.warning(
                    "[approve] Sent-folder APPEND skipped op=%s — XOAUTH2 failed: %s",
                    op_id, response.lines,
                )
                return
        else:
            from gateway.credentials import fetch_credential as _fc  # noqa: PLC0415
            raw_pass = cred.get("upstream_password")
            if not raw_pass:
                logger.warning(
                    "[approve] Sent-folder APPEND skipped op=%s — no upstream_password", op_id
                )
                return
            status, data = await client.login(imap_user, await _fc(raw_pass))
            if status != "OK":
                logger.warning(
                    "[approve] Sent-folder APPEND skipped op=%s — LOGIN failed: %s", op_id, data
                )
                return

        # Discover and cache the Sent folder name if not already known
        if not target_folder:
            discovered = await _discover_sent_folder(client)
            if discovered:
                target_folder = discovered
                await _update_agent_sent_folder(int(cred["id"]), target_folder, db_path)
                logger.info(
                    "[approve] Sent folder discovered and cached op=%s folder=%r",
                    op_id, target_folder,
                )
            else:
                # Last resort: use profile default
                target_folder = profile.sent_folder
                logger.warning(
                    "[approve] Sent folder discovery failed, using profile default "
                    "op=%s folder=%r",
                    op_id, target_folder,
                )

        # APPEND with \Seen (it's a sent message — already read by definition)
        msg_bytes = msg.as_bytes()
        status, data = await client.append(
            msg_bytes,
            mailbox=imap_quoted(target_folder),
            flags="(\\Seen)",
        )
        if status == "OK":
            logger.info(
                "[approve] Sent-folder APPEND succeeded op=%s folder=%r", op_id, target_folder
            )
        else:
            logger.warning(
                "[approve] Sent-folder APPEND non-OK op=%s folder=%r status=%s data=%s",
                op_id, target_folder, status, data,
            )

    except Exception as exc:
        logger.warning(
            "[approve] Sent-folder APPEND failed (non-fatal) op=%s: %s", op_id, exc
        )
    finally:
        try:
            await client.logout()
        except Exception:
            pass


async def _execute_imap_upstream(row: dict, db_path: Path) -> None:
    """
    Replay a staged IMAP operation against the upstream server.

    Opens a fresh IMAP4_SSL connection using the agent's registered upstream
    credentials (looked up from agent_credentials by agent_id on the operation).
    Falls back to NUVRAIL_TEST_IMAP_* env vars if agent_id is not set
    (for backwards compatibility with older staged operations).

    Supported op_types: store, move, copy, create, rename, trash, mark_read,
    flag, unflag, mark_unread (all map to UID STORE or UID MOVE/COPY/etc).
    APPEND is skipped — the message body is not stored in the staging DB.

    Raises RuntimeError on any upstream error so the caller can set
    operation status → 'failed'.
    """
    op_type = row.get("op_type", "")

    # Legacy/oversized APPEND with no stored body cannot be replayed — no-op
    # (and no credentials/connection needed). APPENDs WITH a stored body fall
    # through to the normal auth + dispatch path below.
    if op_type == "append" and not row.get("append_message"):
        logger.info(
            "[imap_execute] APPEND op %s has no stored body — skipping (legacy/oversized)",
            row["id"],
        )
        return

    creds = await resolve_imap_credentials(row, db_path)

    imap_command = row.get("imap_command") or ""
    folder_from = row.get("folder_from") or "INBOX"
    folder_to = row.get("folder_to") or ""

    message_ids = decode_json_list(row.get("message_ids"))
    uid_set = ",".join(message_ids) if message_ids else "1"
    flags_add = decode_json_list(row.get("flags_add"))
    flags_remove = decode_json_list(row.get("flags_remove"))

    client = aioimaplib.IMAP4_SSL(host=creds.host, port=creds.port)
    try:
        await client.wait_hello_from_server()
        if creds.oauth2_provider:
            # OAuth2 agent — use AUTHENTICATE XOAUTH2 instead of LOGIN.
            from gateway.oauth2_tokens import OAuth2Error, get_access_token  # noqa: PLC0415
            try:
                _email, _access_token = await get_access_token(str(creds.cred["id"]), db_path)
            except OAuth2Error as exc:
                raise RuntimeError(f"IMAP OAuth2 token fetch failed: {exc}") from exc
            response = await client.xoauth2(_email, _access_token)
            if response.result != "OK":
                raise RuntimeError(f"IMAP XOAUTH2 authentication failed: {response.lines}")
            # aioimaplib's xoauth2() doesn't seed the capability set the way
            # login() does (login() scans response.lines for CAPABILITY at
            # aioimaplib line 469, then calls capability() explicitly). Without
            # it, uid("move", ...) raises "server has not MOVE capability" even
            # though Gmail advertises MOVE in the AUTHENTICATE response.
            #
            # Mirror login()'s inline parse first — Gmail always sends
            # '* CAPABILITY ... MOVE ...' as part of the AUTHENTICATE response,
            # so we can seed the set from that line and avoid a round trip.
            for _line in response.lines:
                if isinstance(_line, bytes) and b"CAPABILITY" in _line:
                    client.protocol.capabilities = client.protocol.capabilities.union(
                        set(_line.decode().replace("CAPABILITY", "").strip().split())
                    )
                    break
            if not client.protocol.capabilities:
                # Fallback: no capability line in response — fetch explicitly.
                await client.capability()
        else:
            status, data = await client.login(creds.user, creds.password)
            if status != "OK":
                raise RuntimeError(f"IMAP LOGIN failed: {data}")

        # Most write ops require a folder context (SELECT folder_from first)
        if op_type in ("store", "trash", "mark_read", "flag", "unflag", "mark_unread"):
            status, data = await client.select(imap_quoted(folder_from))
            if status != "OK":
                raise RuntimeError(f"IMAP SELECT {folder_from!r} failed: {data}")

            # Build the flag change string
            if flags_add:
                flag_str = " ".join(flags_add)
                status, data = await client.uid("store", uid_set, "+FLAGS", f"({flag_str})")
            elif flags_remove:
                flag_str = " ".join(flags_remove)
                status, data = await client.uid("store", uid_set, "-FLAGS", f"({flag_str})")
            else:
                raise RuntimeError(f"STORE op {row['id']} has no flags_add or flags_remove")
            if status != "OK":
                raise RuntimeError(f"IMAP UID STORE failed: {data}")

        elif op_type == "move":
            if not folder_to:
                raise RuntimeError(f"MOVE op {row['id']} missing folder_to")
            status, data = await client.select(imap_quoted(folder_from))
            if status != "OK":
                raise RuntimeError(f"IMAP SELECT {folder_from!r} failed: {data}")
            status, data = await client.uid("move", uid_set, imap_quoted(folder_to))
            if status != "OK":
                raise RuntimeError(f"IMAP UID MOVE failed: {data}")

        elif op_type == "copy":
            if not folder_to:
                raise RuntimeError(f"COPY op {row['id']} missing folder_to")
            status, data = await client.select(imap_quoted(folder_from))
            if status != "OK":
                raise RuntimeError(f"IMAP SELECT {folder_from!r} failed: {data}")
            status, data = await client.uid("copy", uid_set, imap_quoted(folder_to))
            if status != "OK":
                raise RuntimeError(f"IMAP UID COPY failed: {data}")

        elif op_type == "create":
            # folder_to holds the new folder name for CREATE; fall back to the
            # last token of the raw command if folder_to wasn't recorded.
            if folder_to:
                folder_name = folder_to
            elif imap_command:
                folder_name = imap_command.split()[-1]
            else:
                folder_name = ""
            if not folder_name:
                raise RuntimeError(f"CREATE op {row['id']} missing folder name")
            status, data = await client.create(imap_quoted(folder_name))
            if status != "OK":
                raise RuntimeError(f"IMAP CREATE failed: {data}")

        elif op_type == "rename":
            # folder_from = old name, folder_to = new name
            if not folder_to:
                raise RuntimeError(f"RENAME op {row['id']} missing folder_to")
            status, data = await client.rename(imap_quoted(folder_from), imap_quoted(folder_to))
            if status != "OK":
                raise RuntimeError(f"IMAP RENAME failed: {data}")

        elif op_type == "append":
            # Replay the stored message into the target folder. The raw RFC822
            # bytes were captured by the proxy and base64-encoded into
            # append_message; flags_add carries the original APPEND flags.
            import base64 as _b64  # noqa: PLC0415
            raw_b64 = row.get("append_message")
            if not raw_b64:
                # The legacy guard at the top should have returned already.
                raise RuntimeError(f"APPEND op {row['id']} has no stored message body")
            try:
                msg_bytes = _b64.b64decode(raw_b64)
            except Exception as exc:
                raise RuntimeError(
                    f"APPEND op {row['id']}: cannot decode stored body: {exc}"
                ) from exc
            target_folder = folder_to or "INBOX"
            flag_str = f"({' '.join(flags_add)})" if flags_add else None
            if flag_str:
                status, data = await client.append(
                    msg_bytes, mailbox=imap_quoted(target_folder), flags=flag_str
                )
            else:
                status, data = await client.append(
                    msg_bytes, mailbox=imap_quoted(target_folder)
                )
            if status != "OK":
                raise RuntimeError(f"IMAP APPEND to {target_folder!r} failed: {data}")

        else:
            # Unknown/unsupported op type — log and treat as no-op
            logger.warning(
                "[imap_execute] Unrecognised op_type %r for op %s — skipping upstream exec",
                op_type,
                row["id"],
            )

        logger.info("[imap_execute] Op %s (%s) executed successfully", row["id"], op_type)

    finally:
        try:
            await client.logout()
        except Exception:
            pass


async def _record_execution_failure(
    op_id: str, row: dict, exc: Exception, db_path: Path
) -> None:
    """Mark an operation failed and write an execution_failed audit row."""
    await update_operation_status(op_id, "failed", error=str(exc), db_path=db_path)
    await record_audit_event(
        db_path, timestamp=int(time.time()), event='execution_failed', actor='system',
        operation_id=op_id, agent_id=row.get('agent_id'), op_type=row.get('op_type'),
        detail=json.dumps({'error': str(exc)}),
    )


async def execute_operation(
    op_id: str, row: dict, db_path: Path, *, actor: str = "human"
) -> dict:
    """Execute a staged operation against the upstream server.

    SMTP ops are relayed via aiosmtplib; IMAP ops are replayed via a fresh
    aioimaplib connection. On success the operation is marked ``executed`` and
    an ``executed`` audit row (attributed to ``actor``) is written. On failure
    the operation is marked ``failed``, an ``execution_failed`` audit row is
    written, and ``ExecutionError`` is raised.

    ``actor`` distinguishes a human approval ("human") from an auto-approval
    rule ("auto_rule") in the audit trail.

    Returns ``{"executed_at": <int|None>}`` on success.
    """
    protocol = row.get("protocol", "imap")

    if protocol == "smtp":
        # Deserialize smtp_envelope
        envelope_raw = row.get("smtp_envelope")
        if isinstance(envelope_raw, str):
            envelope = json.loads(envelope_raw)
        elif isinstance(envelope_raw, dict):
            envelope = envelope_raw
        else:
            envelope = {}

        recipients = envelope.get("to", [])
        subject = envelope.get("subject", "<no subject>")
        # Use full body when available; fall back to body_preview for ops staged
        # before this field was introduced.
        body_text = envelope.get("body") or envelope.get("body_preview", "")

        from gateway.credentials import fetch_credential  # noqa: PLC0415
        agent_id = row.get("agent_id")
        cred = await get_agent_credential(agent_id, db_path)
        if cred:
            smtp_host = cred["upstream_smtp_host"] or cred["upstream_host"]
            smtp_port = int(cred["upstream_smtp_port"])
            smtp_user = cred["upstream_user"]
            is_oauth2 = bool(cred.get("oauth2_provider"))
            smtp_pass: str | None = None
            if not is_oauth2:
                raw_pass = cred.get("upstream_password")
                if not raw_pass:
                    raise ExecutionError(
                        f"Agent for operation {op_id} has no upstream_password. "
                        "Re-add the agent credentials."
                    )
                smtp_pass = await fetch_credential(raw_pass)
        else:
            is_oauth2 = False
            smtp_host = os.environ.get("NUVRAIL_TEST_SMTP_HOST", "")
            smtp_port = int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587"))
            smtp_user = os.environ.get("NUVRAIL_TEST_SMTP_USER", "")
            smtp_pass = os.environ.get("NUVRAIL_TEST_SMTP_PASS", "")
            if not smtp_host:
                raise ExecutionError(
                    f"Operation {op_id} has no agent_id and no fallback env vars set"
                )

        # body_text is the raw RFC 2822 DATA payload from the agent
        # (headers + blank line + body). Parse it properly so we don't wrap
        # the original headers as the text body of a new MIMEText.
        # If it has no recognisable headers, fall back to a plain MIMEText.
        import email as _email_stdlib  # noqa: PLC0415
        _parsed = _email_stdlib.message_from_string(body_text or "")
        if _parsed.keys():  # at least one header present — treat as full RFC 2822
            msg = _parsed
        else:
            msg = MIMEText(body_text or "(no body)", "plain")
            msg["To"] = ", ".join(recipients) if isinstance(recipients, list) else recipients
            msg["Subject"] = subject
        # Always use the real authenticated upstream address as From.
        if "From" in msg:
            del msg["From"]
        msg["From"] = smtp_user

        # Normalise recipients to a flat list of strings.
        # Use the SMTP envelope captured at staging time (RCPT TO), not the
        # message To: header — they can legitimately differ.
        relay_recipients: list[str] = (
            recipients if isinstance(recipients, list)
            else [recipients] if recipients
            else []
        )
        if not relay_recipients:
            raise ExecutionError(
                f"Operation {op_id} has no recipients in the staged envelope."
            )

        # Anti-spam (R13): enforce the per-agent outbound send cap BEFORE we
        # relay. Counts recipients (messages), fails closed, and applies to
        # both human-approved and auto-rule-approved sends since both reach
        # here. On exceedance we mark the op failed + write a
        # 'send_rate_exceeded' audit row (shared failure path), then raise
        # ExecutionError so the HTTP/auto layers treat it like any other
        # execution failure.
        recipient_count = len(relay_recipients)
        try:
            await enforce_send_rate(agent_id, recipient_count, db_path=db_path)
        except SendRateLimitExceeded as exc:
            await update_operation_status(
                op_id, "failed", error=str(exc), db_path=db_path
            )
            await record_audit_event(
                db_path, timestamp=int(time.time()),
                event='send_rate_exceeded', actor='system',
                operation_id=op_id, agent_id=agent_id, op_type=row.get('op_type'),
                detail=json.dumps({
                    'used': exc.used,
                    'requested': exc.requested,
                    'cap': exc.cap,
                    'window_seconds': exc.window_seconds,
                }),
            )
            raise ExecutionError(str(exc)) from exc

        logger.info(
            "[execute] SMTP relay start op=%s host=%s port=%d user=%s recipients=%s",
            op_id, smtp_host, smtp_port, smtp_user, relay_recipients,
        )

        try:
            if is_oauth2:
                # OAuth2 agents: authenticate with XOAUTH2 via aiosmtplib native support.
                from gateway.oauth2_tokens import OAuth2Error, get_access_token  # noqa: PLC0415
                try:
                    _email, access_token = await get_access_token(str(cred["id"]), db_path)
                except OAuth2Error as exc:
                    raise ExecutionError(
                        f"OAuth2 token error for operation {op_id}: {exc}"
                    ) from exc
                async with aiosmtplib.SMTP(
                    hostname=smtp_host, port=smtp_port, start_tls=True
                ) as smtp:
                    await smtp.auth_xoauth2(smtp_user, access_token)
                    errors, server_msg = await smtp.sendmail(
                        smtp_user, relay_recipients, msg.as_string()
                    )
                    if errors:
                        logger.warning(
                            "[execute] SMTP relay partial errors op=%s errors=%s",
                            op_id, errors,
                        )
            else:
                # Explicitly pass sender and recipients from the staged envelope.
                # Do NOT rely on header extraction — the To: header and the RCPT TO
                # envelope can differ; using envelope recipients is correct.
                relay_errors, relay_server_msg = await aiosmtplib.send(
                    msg,
                    sender=smtp_user,
                    recipients=relay_recipients,
                    hostname=smtp_host,
                    port=smtp_port,
                    username=smtp_user,
                    password=smtp_pass,
                    start_tls=True,
                )
                if relay_errors:
                    logger.warning(
                        "[execute] SMTP relay partial errors op=%s errors=%s server_msg=%r",
                        op_id, relay_errors, relay_server_msg,
                    )
            logger.info(
                "[execute] SMTP relay succeeded op=%s host=%s recipients=%s",
                op_id, smtp_host, relay_recipients,
            )
            # Best-effort: save a copy to the upstream Sent folder.
            # Non-fatal — _save_to_sent_folder catches its own exceptions.
            # Skipped for Gmail/Outlook which auto-save on relay.
            if cred:
                await _save_to_sent_folder(msg, op_id, cred, db_path)
        except Exception as exc:
            # Covers relay failures and the OAuth2 ExecutionError raised above.
            logger.error("[execute] SMTP relay failed for %s: %s", op_id, exc)
            await _record_execution_failure(op_id, row, exc, db_path)
            raise ExecutionError(f"SMTP relay failed: {exc}") from exc

    else:
        # IMAP ops: replay the stored command against the upstream IMAP server.
        try:
            await _execute_imap_upstream(row, db_path)
        except Exception as exc:
            logger.error("[execute] IMAP execution failed for %s: %s", op_id, exc)
            await _record_execution_failure(op_id, row, exc, db_path)
            raise ExecutionError(f"IMAP execution failed: {exc}") from exc

    # Mark operation as executed and insert audit log.
    # For smtp_send we record recipient_count in the audit detail so the
    # anti-spam send-rate limiter (gateway.send_rate_limiter) can SUM messages
    # — not operations — when counting an agent's recent sends.
    await update_operation_status(op_id, "executed", db_path=db_path)
    executed_detail: Optional[str] = None
    if protocol == "smtp":
        executed_detail = json.dumps({"recipient_count": len(relay_recipients)})
    await record_audit_event(
        db_path, timestamp=int(time.time()), event='executed', actor=actor,
        operation_id=op_id, agent_id=row.get('agent_id'), op_type=row.get('op_type'),
        detail=executed_detail,
    )

    updated = await get_operation(op_id, db_path=db_path)
    return {"executed_at": updated.get("executed_at") if updated else None}
