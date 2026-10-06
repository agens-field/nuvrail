"""
Staging-time Trash folder resolution for trash ops (#168).

proxy._resolve_trash_folder picks where an approved "Move to Trash" will go:

    server-declared \\Trash (RFC 6154, exact case) ─► use it
          │ none
          ▼
    provider profile trash_folder (Gmail/iCloud/Outlook) ─► use it
          │ none
          ▼
    None → staged + labelled "Mark deleted (no Trash folder found)"

No folder-name heuristics: a guessed folder could misroute approved deletes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gateway.provider_profiles import GENERIC_PROFILE, GMAIL_PROFILE
from gateway.proxy import _resolve_trash_folder
from gateway.state_db import get_trash_folder, init_db, upsert_folders_from_list


@pytest.fixture()
async def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "trash.db"
    await init_db(path)
    return path


async def test_get_trash_folder_preserves_case(db_path: Path) -> None:
    await upsert_folders_from_list(
        ["INBOX", "INBOX.Papierkorb"],
        user_id=1,
        special_use={"INBOX.Papierkorb": "trash"},
        db_path=db_path,
    )
    assert await get_trash_folder(user_id=1, db_path=db_path) == "INBOX.Papierkorb"


async def test_get_trash_folder_none_when_undeclared(db_path: Path) -> None:
    await upsert_folders_from_list(["INBOX", "Trash"], user_id=1, db_path=db_path)
    # A folder merely *named* Trash is not a declaration.
    assert await get_trash_folder(user_id=1, db_path=db_path) is None


async def test_get_trash_folder_is_tenant_scoped(db_path: Path) -> None:
    await upsert_folders_from_list(
        ["Bin"], user_id=1, special_use={"Bin": "trash"}, db_path=db_path
    )
    assert await get_trash_folder(user_id=2, db_path=db_path) is None


async def test_declared_trash_wins_over_profile(db_path: Path) -> None:
    await upsert_folders_from_list(
        ["[Gmail]/Papierkorb"],
        user_id=1,
        special_use={"[Gmail]/Papierkorb": "trash"},
        db_path=db_path,
    )
    folder = await _resolve_trash_folder({"user_id": 1}, db_path, GMAIL_PROFILE, "t")
    assert folder == "[Gmail]/Papierkorb"


async def test_profile_trash_used_when_nothing_declared(db_path: Path) -> None:
    folder = await _resolve_trash_folder({"user_id": 1}, db_path, GMAIL_PROFILE, "t")
    assert folder == "[Gmail]/Trash"


async def test_no_trash_folder_known_returns_none(db_path: Path) -> None:
    assert await _resolve_trash_folder({"user_id": 1}, db_path, GENERIC_PROFILE, "t") is None
    assert await _resolve_trash_folder({"user_id": 1}, db_path, None, "t") is None


async def test_lookup_failure_is_nonfatal(tmp_path: Path) -> None:
    """A broken DB must not break staging: fall through to the profile."""
    missing = tmp_path / "no-such-dir" / "x.db"
    folder = await _resolve_trash_folder({"user_id": 1}, missing, GMAIL_PROFILE, "t")
    assert folder == "[Gmail]/Trash"
