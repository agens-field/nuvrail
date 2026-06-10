"""
Unit tests for the proxy's proactive SPECIAL-USE discovery (RFC 6154).

Drives _discover_special_use / _read_upstream_until_tagged directly with a
hand-fed asyncio.StreamReader and a fake writer — no real upstream needed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gateway.proxy import _discover_special_use, _read_upstream_until_tagged
from gateway.state_db import get_special_use_folders, init_db


class FakeWriter:
    """Collects written bytes; satisfies the write/drain surface the code uses."""

    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        pass


def _reader_with(*lines: str) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data(line.encode() + b"\r\n")
    reader.feed_eof()
    return reader


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    await init_db(path)
    return path


# ---------------------------------------------------------------------------
# _read_upstream_until_tagged
# ---------------------------------------------------------------------------


async def test_read_until_tagged_collects_untagged_lines() -> None:
    reader = _reader_with(
        '* LIST (\\Trash) "/" "Trash"',
        "* OK still going",
        "tag1 OK LIST completed",
    )
    lines = await _read_upstream_until_tagged(reader, b"tag1", peer="test")
    assert lines == ['* LIST (\\Trash) "/" "Trash"', "* OK still going"]


async def test_read_until_tagged_eof_without_completion_returns_none() -> None:
    reader = _reader_with('* LIST (\\Trash) "/" "Trash"')  # no tagged line
    assert await _read_upstream_until_tagged(reader, b"tag1", peer="test") is None


async def test_read_until_tagged_ignores_other_tags() -> None:
    reader = _reader_with("othertag OK something else", "tag1 OK done")
    lines = await _read_upstream_until_tagged(reader, b"tag1", peer="test")
    assert lines == ["othertag OK something else"]


# ---------------------------------------------------------------------------
# _discover_special_use
# ---------------------------------------------------------------------------


async def test_discovery_with_known_capability_issues_only_list(db_path: Path) -> None:
    """SPECIAL-USE already seen during auth → straight to the extended LIST."""
    reader = _reader_with(
        '* LIST (\\HasNoChildren \\Trash) "/" "Papierkorb"',
        '* LIST (\\HasNoChildren \\Junk) "/" "Spamverdacht"',
        '* LIST (\\HasNoChildren) "/" "INBOX"',
        "nuvlist0 OK LIST completed",
    )
    writer = FakeWriter()
    await _discover_special_use(
        reader, writer, "* CAPABILITY IMAP4rev1 SPECIAL-USE",
        user_id=1, db_path=db_path, peer="test",
    )
    assert b"CAPABILITY\r\n" not in writer.written  # no extra round-trip
    assert b'nuvlist0 LIST "" "*" RETURN (SPECIAL-USE)\r\n' in writer.written
    roles = await get_special_use_folders(user_id=1, db_path=db_path)
    assert roles == {"papierkorb": "trash", "spamverdacht": "junk"}


async def test_discovery_asks_capability_when_unknown(db_path: Path) -> None:
    reader = _reader_with(
        "* CAPABILITY IMAP4rev1 SPECIAL-USE MOVE",
        "nuvcap0 OK done",
        '* LIST (\\Archive) "/" "Arkiv"',
        "nuvlist0 OK done",
    )
    writer = FakeWriter()
    await _discover_special_use(
        reader, writer, "", user_id=1, db_path=db_path, peer="test"
    )
    assert b"nuvcap0 CAPABILITY\r\n" in writer.written
    roles = await get_special_use_folders(user_id=1, db_path=db_path)
    assert roles == {"arkiv": "archive"}


async def test_discovery_skipped_without_special_use_capability(db_path: Path) -> None:
    """Server doesn't advertise SPECIAL-USE → no LIST is sent at all."""
    reader = _reader_with(
        "* CAPABILITY IMAP4rev1 IDLE",
        "nuvcap0 OK done",
    )
    writer = FakeWriter()
    await _discover_special_use(
        reader, writer, "", user_id=1, db_path=db_path, peer="test"
    )
    assert b"LIST" not in writer.written
    assert await get_special_use_folders(user_id=1, db_path=db_path) == {}


async def test_discovery_incomplete_list_response_is_nonfatal(db_path: Path) -> None:
    """EOF before the tagged LIST completion → nothing stored, no exception."""
    reader = _reader_with(
        '* LIST (\\Trash) "/" "Trash"',  # tagged OK never arrives
    )
    writer = FakeWriter()
    await _discover_special_use(
        reader, writer, "SPECIAL-USE", user_id=1, db_path=db_path, peer="test"
    )
    assert await get_special_use_folders(user_id=1, db_path=db_path) == {}


async def test_discovery_roles_are_tenant_scoped(db_path: Path) -> None:
    reader = _reader_with(
        '* LIST (\\Trash) "/" "Trash"',
        "nuvlist0 OK done",
    )
    await _discover_special_use(
        reader, FakeWriter(), "SPECIAL-USE", user_id=7, db_path=db_path, peer="test"
    )
    assert await get_special_use_folders(user_id=7, db_path=db_path) == {"trash": "trash"}
    assert await get_special_use_folders(user_id=8, db_path=db_path) == {}
