"""
Proxy-level tests for the fail-closed command path.

Drives _client_to_upstream / _upstream_to_client directly with hand-fed
asyncio.StreamReaders and fake writers — no real upstream needed.

    agent bytes ──► [StreamReader] ──► _client_to_upstream ──┬─► upstream FakeWriter
                                                             └─► client FakeWriter (local replies)

The invariant under test: a verb the classifier does not know, and any command
carrying a non-synchronizing literal, never produces a single byte upstream.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gateway.proxy import _MAX_APPEND_BYTES, _client_to_upstream, _upstream_to_client
from gateway.state_db import get_db, init_db


class FakeWriter:
    """Collects written bytes; satisfies the write/drain surface the code uses."""

    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        pass


def _reader_with(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def _session() -> dict:
    return {
        "folder": None,
        "folder_id": None,
        "select_lines": [],
        "in_select": False,
        "revert_trigger_tag": None,
        "agent_id": 1,
        "user_id": 1,
        "search_uid_mode": False,
        "capability_tag": None,
        "provider_profile": None,
        "pending_copy_intent": None,
        "special_use": None,
    }


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    await init_db(path)
    return path


async def _run_c2u(data: bytes, db_path: Path) -> tuple[bytes, bytes, dict]:
    upstream, client, session = FakeWriter(), FakeWriter(), _session()
    await _client_to_upstream(
        _reader_with(data), upstream, client, session, peer="test", db_path=db_path
    )
    return upstream.written, client.written, session


@pytest.mark.parametrize("line", [
    b"a1 SETACL INBOX attacker@example.com lrswipkxtea\r\n",
    b"a1 DELETEACL INBOX owner@example.com\r\n",
    b"a1 SETMETADATA INBOX (/private/comment \"x\")\r\n",
    b"a1 SETQUOTA \"\" (STORAGE 1)\r\n",
    b"a1 COMPRESS DEFLATE\r\n",
    b"a1 STARTTLS\r\n",
    b"a1 LOGIN user pass\r\n",
    b"a1 XSOMETHING arg\r\n",
    b"a1 UID REPLACE 4 INBOX x\r\n",
])
async def test_unclassified_verb_never_reaches_upstream(line: bytes, db_path: Path) -> None:
    upstream, client, _ = await _run_c2u(line, db_path)
    assert upstream == b""
    assert client.startswith(b"a1 NO [CANNOT] Nuvrail: ")


async def test_nonsync_literal_is_consumed_and_refused(db_path: Path) -> None:
    """REPLACE with LITERAL+ = APPEND + EXPUNGE upstream. Nothing may be forwarded,
    and the literal body must not be parsed as commands (a body line that looks
    like a read would otherwise be forwarded)."""
    body = b"a9 FETCH 1 (FLAGS)\r\nSubject: hi\r\n\r\nbody\r\n"
    data = (
        b"a1 REPLACE 4 INBOX {" + str(len(body)).encode() + b"+}\r\n" + body + b"\r\n"
        b"a2 NOOP\r\n"
    )
    upstream, client, _ = await _run_c2u(data, db_path)
    # Only the NOOP after the refused command is forwarded.
    assert upstream == b"a2 NOOP\r\n"
    assert client == (
        b"a1 NO [CANNOT] Nuvrail: non-synchronizing literals are not supported, use {N}\r\n"
    )


async def test_chained_nonsync_literals_are_all_consumed(db_path: Path) -> None:
    data = (
        b"a1 APPEND INBOX {3+}\r\nabc (\\Seen) {4-}\r\ndefg\r\n"
        b"a2 NOOP\r\n"
    )
    upstream, client, _ = await _run_c2u(data, db_path)
    assert upstream == b"a2 NOOP\r\n"
    assert client.startswith(b"a1 NO [CANNOT]")


async def test_oversized_nonsync_literal_closes_connection(db_path: Path) -> None:
    data = b"a1 APPEND INBOX {" + str(_MAX_APPEND_BYTES + 1).encode() + b"+}\r\na2 NOOP\r\n"
    upstream, client, _ = await _run_c2u(data, db_path)
    assert upstream == b""
    assert client == b"* BYE Nuvrail: literal too large\r\n"


async def test_reads_and_idle_done_still_forwarded(db_path: Path) -> None:
    data = b"a1 IDLE\r\nDONE\r\na2 UID FETCH 1:* (FLAGS)\r\na3 ENABLE CONDSTORE\r\n"
    upstream, client, _ = await _run_c2u(data, db_path)
    assert upstream == data
    assert client == b""


async def test_blank_line_is_ignored_and_bare_tag_gets_bad(db_path: Path) -> None:
    upstream, client, _ = await _run_c2u(b"\r\na7\r\n", db_path)
    assert upstream == b""
    assert client == b"a7 BAD Nuvrail: missing command\r\n"


async def test_capability_reply_is_filtered_only_while_outstanding(db_path: Path) -> None:
    """The agent's own CAPABILITY reply loses LITERAL+/COMPRESS; once its tagged
    OK has passed, a body line that happens to look like a capability line is
    relayed byte-for-byte."""
    _, _, session = await _run_c2u(b"c1 CAPABILITY\r\n", db_path)
    assert session["capability_tag"] == "C1"

    upstream = _reader_with(
        b"* CAPABILITY IMAP4rev1 LITERAL+ IDLE COMPRESS=DEFLATE\r\n"
        b"c1 OK done\r\n"
        b"* CAPABILITY IMAP4rev1 LITERAL+\r\n"
    )
    client = FakeWriter()
    await _upstream_to_client(upstream, client, session, db_path, peer="test")
    assert client.written == (
        b"* CAPABILITY IMAP4rev1 IDLE\r\n"
        b"c1 OK done\r\n"
        b"* CAPABILITY IMAP4rev1 LITERAL+\r\n"
    )
    assert session["capability_tag"] is None



# ---------------------------------------------------------------------------
# nuvrail-internal#41 items 1-5
#
# Sync literal routing (items 1-2): only "APPEND ... {N}" gets the "+"
# continuation and is staged. Anything else is refused BEFORE "+", so per
# RFC 3501 the client never sends the literal and the next line is a fresh
# command:
#
#   "a1 UID REPLACE 4 INBOX {3}" ──► a1 NO [CANNOT]  (no "+", nothing upstream)
#   "a1 APPEND INBOX ~{5}"       ──► a1 NO [CANNOT]  (literal8; BINARY hidden)
#   "a1 APPEND INBOX {5}"        ──► "+" ──► 5 bytes ──► a1 OK [STAGED]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line,what", [
    (b"a1 UID REPLACE 4 INBOX {3}\r\n", b"a literal on UID REPLACE"),
    (b"a1 SEARCH CHARSET UTF-8 TEXT {5}\r\n", b"a literal on SEARCH"),
    (b"a1 SELECT {5}\r\n", b"a literal on SELECT"),
    (b"a1 APPEND INBOX ~{5}\r\n", b"literal8 (~{N})"),
    (b"a1 APPEND INBOX (\\Seen) ~{5}\r\n", b"literal8 (~{N})"),
])
async def test_sync_literal_on_non_append_or_literal8_refused_before_continuation(
    line: bytes, what: bytes, db_path: Path
) -> None:
    upstream, client, _ = await _run_c2u(line + b"a2 NOOP\r\n", db_path)
    # No "+" continuation, no staging: the refusal is the first and only reply,
    # and the next line is read as a fresh command (forwarded NOOP).
    assert client == b"a1 NO [CANNOT] Nuvrail: " + what + b" is not supported\r\n"
    assert upstream == b"a2 NOOP\r\n"


async def test_sync_literal_on_non_append_is_not_staged(db_path: Path) -> None:
    """The old path staged "a1 UID REPLACE 4 INBOX {3}" as an APPEND to a folder
    named "REPLACE" (a misleading approval card)."""
    # A non-compliant client that sends the literal anyway: "abc" is then read
    # as a (malformed) command, never as message data.
    await _run_c2u(b"a1 UID REPLACE 4 INBOX {3}\r\nabc\r\n", db_path)
    async with get_db(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM staged_operations")
        (count,) = await cur.fetchone()
    assert count == 0


async def test_append_sync_literal_still_staged(db_path: Path) -> None:
    upstream, client, _ = await _run_c2u(b"a1 APPEND INBOX {5}\r\nhello\r\n", db_path)
    assert upstream == b""
    assert client.startswith(b"+ Ready for literal data\r\na1 OK [STAGED]")


# MULTIAPPEND (#41 follow-up C) -----------------------------------------------
#
# Before: the first message was staged, the second literal never got its "+",
# and the agent got a tagged OK for the whole command. Now the whole command is
# refused (RFC 3502 MULTIAPPEND is atomic) and nothing is staged:
#
#   "a1 APPEND INBOX {5}" ──► "+" ──► "hello" ──► " {7}" ──► a1 NO [CANNOT]
# ---------------------------------------------------------------------------

_MULTIAPPEND_NO = (
    b"a1 NO [CANNOT] Nuvrail: MULTIAPPEND is not supported, append one message per command\r\n"
)


async def _staged_count(db_path: Path) -> int:
    async with get_db(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM staged_operations")
        (count,) = await cur.fetchone()
    return count


@pytest.mark.parametrize("rest", [
    b" {7}",
    b" (\\Seen) {7}",
    b' (\\Draft) "19-Oct-2026 10:00:00 +0000" {7}',
    b" ~{7}",
])
async def test_multiappend_sync_second_literal_refused_before_continuation(
    rest: bytes, db_path: Path
) -> None:
    data = b"a1 APPEND INBOX {5}\r\nhello" + rest + b"\r\na2 NOOP\r\n"
    upstream, client, _ = await _run_c2u(data, db_path)
    # Exactly one "+" (for the first literal), then the refusal. No second "+",
    # so a compliant client never sends message 2 and the next line is a command.
    assert client == b"+ Ready for literal data\r\n" + _MULTIAPPEND_NO
    assert upstream == b"a2 NOOP\r\n"
    assert await _staged_count(db_path) == 0


async def test_multiappend_nonsync_second_literal_consumed_and_refused(db_path: Path) -> None:
    """A client that ignores the hidden LITERAL+ sends message 2 without waiting.
    Its bytes are discarded (never read as commands) and nothing is staged."""
    msg2 = b"a9 FETCH 1 (FLAGS)\r\n"
    data = (
        b"a1 APPEND INBOX {5}\r\nhello {" + str(len(msg2)).encode() + b"+}\r\n"
        + msg2 + b"\r\na2 NOOP\r\n"
    )
    upstream, client, _ = await _run_c2u(data, db_path)
    assert client == b"+ Ready for literal data\r\n" + _MULTIAPPEND_NO
    assert upstream == b"a2 NOOP\r\n"
    assert await _staged_count(db_path) == 0


async def test_multiappend_oversized_nonsync_second_literal_closes(db_path: Path) -> None:
    data = (
        b"a1 APPEND INBOX {5}\r\nhello {" + str(_MAX_APPEND_BYTES + 1).encode() + b"+}\r\n"
        b"a2 NOOP\r\n"
    )
    upstream, client, _ = await _run_c2u(data, db_path)
    assert client == b"+ Ready for literal data\r\n* BYE Nuvrail: literal too large\r\n"
    assert upstream == b""
    assert await _staged_count(db_path) == 0


async def test_single_append_with_empty_rest_still_staged_once(db_path: Path) -> None:
    upstream, client, _ = await _run_c2u(
        b"a1 APPEND INBOX {5}\r\nhello\r\na2 APPEND INBOX {3}\r\nabc\r\n", db_path
    )
    assert upstream == b""
    assert client.count(b"+ Ready for literal data\r\n") == 2
    assert b"a1 OK [STAGED]" in client and b"a2 OK [STAGED]" in client
    assert await _staged_count(db_path) == 2


# ENABLE allowlist (item 3) ---------------------------------------------------

@pytest.mark.parametrize("line,forwarded", [
    (b"a1 ENABLE CONDSTORE\r\n", b"a1 ENABLE CONDSTORE\r\n"),
    (b"a1 ENABLE CONDSTORE QRESYNC\r\n", b"a1 ENABLE CONDSTORE\r\n"),
    (b"a1 ENABLE UTF8=ACCEPT condstore\r\n", b"a1 ENABLE condstore\r\n"),
])
async def test_enable_forwards_only_allowlisted_extensions(
    line: bytes, forwarded: bytes, db_path: Path
) -> None:
    upstream, client, _ = await _run_c2u(line, db_path)
    assert upstream == forwarded
    assert client == b""


@pytest.mark.parametrize("line", [
    b"a1 ENABLE QRESYNC\r\n",
    b"a1 ENABLE UTF8=ACCEPT\r\n",
    b"a1 ENABLE\r\n",
])
async def test_enable_with_nothing_allowlisted_answered_locally(line: bytes, db_path: Path) -> None:
    upstream, client, _ = await _run_c2u(line, db_path)
    assert upstream == b""
    assert client == b"* ENABLED\r\na1 OK ENABLE completed\r\n"


# Bare CR / NUL (item 5) ------------------------------------------------------

@pytest.mark.parametrize("line", [
    b"a1 NOOP\rb1 UID REPLACE 4 INBOX x\r\n",
    b"a1 NOOP\x00\r\n",
    b"a1 FETCH 1 (FLAGS)\rb1 STORE 1 +FLAGS (\\Deleted)\n",
])
async def test_bare_cr_or_nul_command_line_never_forwarded(line: bytes, db_path: Path) -> None:
    upstream, client, _ = await _run_c2u(line + b"a2 NOOP\r\n", db_path)
    assert upstream == b"a2 NOOP\r\n"
    assert client == b"a1 BAD Nuvrail: bare CR or NUL in command line\r\n"


async def test_lf_only_and_crlf_lines_still_accepted(db_path: Path) -> None:
    upstream, client, _ = await _run_c2u(b"a1 NOOP\na2 NOOP\r\n", db_path)
    assert upstream == b"a1 NOOP\na2 NOOP\r\n"
    assert client == b""


# u2c literals (item 4) -------------------------------------------------------

async def _run_u2c(data: bytes, session: dict, db_path: Path) -> bytes:
    client = FakeWriter()
    await _upstream_to_client(_reader_with(data), client, session, db_path, peer="test")
    return client.written


async def test_capability_lookalike_inside_fetch_literal_is_not_rewritten(db_path: Path) -> None:
    """A hostile body line "* CAPABILITY ..." inside a FETCH literal, while the
    agent's own CAPABILITY is outstanding (pipelined), must be relayed byte for
    byte: rewriting it would shrink the literal below its {N} and desync."""
    session = _session()
    session["capability_tag"] = "C1"
    body = b"* CAPABILITY IMAP4rev1 LITERAL+ IDLE\r\nhi\r\n"
    data = (
        b"* 1 FETCH (BODY[TEXT] {" + str(len(body)).encode() + b"}\r\n" + body + b")\r\n"
        b"* CAPABILITY IMAP4rev1 LITERAL+ IDLE\r\n"
        b"c1 OK done\r\n"
    )
    out = await _run_u2c(data, session, db_path)
    assert out == (
        b"* 1 FETCH (BODY[TEXT] {" + str(len(body)).encode() + b"}\r\n" + body + b")\r\n"
        b"* CAPABILITY IMAP4rev1 IDLE\r\n"     # the real reply is still filtered
        b"c1 OK done\r\n"
    )
    assert session["capability_tag"] is None


async def test_second_literal_in_same_fetch_is_also_verbatim(db_path: Path) -> None:
    session = _session()
    session["capability_tag"] = "C1"
    a = b"x\r\n"
    b = b"* CAPABILITY LITERAL+\r\n"
    data = (
        b"* 1 FETCH (BODY[HEADER] {" + str(len(a)).encode() + b"}\r\n" + a
        + b" BODY[TEXT] {" + str(len(b)).encode() + b"}\r\n" + b + b")\r\n"
    )
    assert await _run_u2c(data, session, db_path) == data


async def test_literal8_and_mailbox_literal_relayed_verbatim(db_path: Path) -> None:
    session = _session()
    session["capability_tag"] = "C1"
    body = b"* CAPABILITY X LITERAL+\r\n"
    data = (
        b'* LIST () "/" {' + str(len(body)).encode() + b"}\r\n" + body + b"\r\n"
        b"* 2 FETCH (BINARY[1] ~{" + str(len(body)).encode() + b"}\r\n" + body + b")\r\n"
    )
    assert await _run_u2c(data, session, db_path) == data


async def test_status_text_ending_in_braces_is_not_a_literal(db_path: Path) -> None:
    """resp-text may end in "{N}"; treating it as a literal would let the next N
    bytes skip the capability filter."""
    session = _session()
    session["capability_tag"] = "C1"
    data = (
        b"* OK [ALERT] see {28}\r\n"
        b"* CAPABILITY IMAP4rev1 LITERAL+ IDLE\r\n"
        b"c1 OK done {3}\r\n"
    )
    out = await _run_u2c(data, session, db_path)
    assert out == (
        b"* OK [ALERT] see {28}\r\n"
        b"* CAPABILITY IMAP4rev1 IDLE\r\n"
        b"c1 OK done {3}\r\n"
    )


async def test_literal_split_across_reads_is_relayed_whole(db_path: Path) -> None:
    """The literal arrives over several upstream reads; every byte is relayed
    once, in order, and line processing resumes after it."""
    session = _session()
    session["capability_tag"] = "C1"
    body = (b"* CAPABILITY LITERAL+\r\n" * 400)  # > one 4 KB read
    reader = asyncio.StreamReader()
    head = b"* 1 FETCH (BODY[TEXT] {" + str(len(body)).encode() + b"}\r\n"
    tail = b")\r\n* CAPABILITY IMAP4rev1 LITERAL+\r\nc1 OK done\r\n"
    for piece in (head, body[:1000], body[1000:5000], body[5000:], tail):
        reader.feed_data(piece)
    reader.feed_eof()
    client = FakeWriter()
    await _upstream_to_client(reader, client, session, db_path, peer="test")
    assert client.written == head + body + b")\r\n* CAPABILITY IMAP4rev1\r\nc1 OK done\r\n"

