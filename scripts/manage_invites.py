#!/usr/bin/env python3
"""Admin CLI for Nuvrail invite codes (NUVRAIL_SIGNUP_MODE=invite).

Run it inside the gateway container so it talks to the same DB the API uses:

    # mint a single-use code (raw code printed ONCE — copy it now)
    docker compose exec gateway python3 scripts/manage_invites.py mint
    docker compose exec gateway python3 scripts/manage_invites.py mint --expires-in 7d --note "for alice"

    # list codes (shows status, never the raw code — only a hash prefix)
    docker compose exec gateway python3 scripts/manage_invites.py list

    # revoke an unspent code by its full code_hash (from `list`)
    docker compose exec gateway python3 scripts/manage_invites.py revoke <code_hash>

Design notes:
  - The raw code is generated here and shown exactly once; only its SHA-256
    hash is stored (same discipline as bearer tokens). Lose it -> mint another.
  - Single-use is enforced at redemption time in /auth/register (atomic
    conditional UPDATE), not here.
  - --expires-in is OPTIONAL. Default is no expiry. Accepts Nd / Nh / Nm
    (days/hours/minutes), e.g. 7d, 48h, 90m.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

# Make the package importable when run as a bare script inside the container.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.auth import generate_token, hash_token_for_storage
from gateway.state_db import (
    DB_PATH,
    init_db,
    insert_invite_code,
    list_invite_codes,
    revoke_invite_code,
)

_DURATION_RE = re.compile(r"^(\d+)([dhm])$")
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60}


def _parse_expires_in(spec: str | None) -> int | None:
    """Turn a duration like '7d' into an absolute epoch expiry, or None."""
    if not spec:
        return None
    m = _DURATION_RE.match(spec.strip().lower())
    if not m:
        raise SystemExit(
            f"error: --expires-in {spec!r} is not valid; use Nd/Nh/Nm (e.g. 7d, 48h, 90m)."
        )
    amount, unit = int(m.group(1)), m.group(2)
    if amount <= 0:
        raise SystemExit("error: --expires-in must be a positive duration.")
    return int(time.time()) + amount * _UNIT_SECONDS[unit]


def _fmt_ts(ts: int | None) -> str:
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(ts))


def _status(row: dict) -> str:
    if row["revoked_at"] is not None:
        return "revoked"
    if row["redeemed_at"] is not None:
        return "redeemed"
    if row["expires_at"] is not None and row["expires_at"] <= int(time.time()):
        return "expired"
    return "active"


async def _mint(expires_in: str | None, note: str | None) -> None:
    await init_db(DB_PATH)
    expires_at = _parse_expires_in(expires_in)
    raw = generate_token()
    await insert_invite_code(hash_token_for_storage(raw), expires_at=expires_at, note=note)
    print("Invite code (shown once — copy it now):\n")
    print(f"    {raw}\n")
    print(f"  expires: {_fmt_ts(expires_at)}")
    if note:
        print(f"  note:    {note}")
    print("\nHand this to the intended person. It works once, then it's spent.")


async def _list() -> None:
    await init_db(DB_PATH)
    rows = await list_invite_codes(DB_PATH)
    if not rows:
        print("No invite codes.")
        return
    print(f"{'status':9} {'created':21} {'expires':21} {'redeemed_by':24} code_hash")
    for r in rows:
        print(
            f"{_status(r):9} "
            f"{_fmt_ts(r['created_at']):21} "
            f"{_fmt_ts(r['expires_at']):21} "
            f"{(r['redeemed_by_email'] or '-'):24} "
            f"{r['code_hash']}"
        )


async def _revoke(code_hash: str) -> None:
    await init_db(DB_PATH)
    ok = await revoke_invite_code(code_hash.strip(), DB_PATH)
    if ok:
        print(f"Revoked {code_hash}.")
    else:
        print(
            f"Nothing revoked for {code_hash} — it is unknown, already redeemed, "
            "or already revoked."
        )
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Manage Nuvrail invite codes.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mint = sub.add_parser("mint", help="Mint a single-use invite code.")
    p_mint.add_argument(
        "--expires-in",
        default=None,
        help="Optional TTL: Nd/Nh/Nm (e.g. 7d). Default: never expires.",
    )
    p_mint.add_argument("--note", default=None, help="Optional admin label.")

    sub.add_parser("list", help="List invite codes and their status.")

    p_rev = sub.add_parser("revoke", help="Revoke an unspent invite code by code_hash.")
    p_rev.add_argument("code_hash", help="Full code_hash from `list`.")

    args = parser.parse_args(argv)
    if args.cmd == "mint":
        asyncio.run(_mint(args.expires_in, args.note))
    elif args.cmd == "list":
        asyncio.run(_list())
    elif args.cmd == "revoke":
        asyncio.run(_revoke(args.code_hash))


if __name__ == "__main__":
    main()
