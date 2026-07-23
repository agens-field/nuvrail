#!/usr/bin/env python3
"""Admin CLI for provisioning Nuvrail human accounts.

This is how accounts are created when NUVRAIL_SIGNUP_MODE=closed (the default),
where the public /auth/register endpoint is disabled. Run inside the gateway
container so it uses the same DB as the API:

    # create a user (prompts for password unless --password given)
    docker compose exec gateway python3 scripts/manage_users.py create alice@example.com --name "Alice"

    # list users
    docker compose exec gateway python3 scripts/manage_users.py list

The account is created identically to a normal registration (bcrypt password,
a bearer token issued once). The bearer token is printed ONCE — copy it or the
user can log in with their email + password to mint a fresh one.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.auth import (  # noqa: E402
    generate_token,
    hash_password,
    hash_token_for_storage,
)
from gateway.state_db import DB_PATH, get_db, init_db  # noqa: E402


async def _create(email: str, password: str, display_name: str | None) -> None:
    await init_db(DB_PATH)
    now = int(time.time())
    api_token = generate_token()
    async with get_db(DB_PATH) as db:
        async with db.execute("SELECT id FROM users WHERE email = ?", (email,)) as cur:
            if await cur.fetchone() is not None:
                raise SystemExit(f"error: {email!r} is already registered.")
        await db.execute(
            "INSERT INTO users (email, display_name, hashed_password, api_token, "
            "api_token_created_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (email, display_name, hash_password(password),
             hash_token_for_storage(api_token), now, now),
        )
        await db.commit()
    print(f"Created user {email!r}.")
    print("\nBearer token (shown once — copy it now):\n")
    print(f"    {api_token}\n")
    print("Or the user can log in with email + password to issue a fresh token.")


async def _list() -> None:
    await init_db(DB_PATH)
    async with get_db(DB_PATH) as db:
        async with db.execute(
            "SELECT id, email, display_name, created_at FROM users ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        print("No users.")
        return
    print(f"{'id':>4}  {'created':21}  {'email':32} display_name")
    for r in rows:
        created = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(r["created_at"]))
        print(f"{r['id']:>4}  {created:21}  {r['email']:32} {r['display_name'] or '-'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Provision Nuvrail human accounts.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create a human account.")
    p_create.add_argument("email")
    p_create.add_argument("--name", default=None, help="Display name.")
    p_create.add_argument(
        "--password",
        default=None,
        help="Password (omit to be prompted securely; preferred).",
    )

    sub.add_parser("list", help="List human accounts.")

    args = parser.parse_args(argv)
    if args.cmd == "create":
        password = args.password
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Confirm password: "):
                raise SystemExit("error: passwords did not match.")
        if not password:
            raise SystemExit("error: password must not be empty.")
        asyncio.run(_create(args.email, password, args.name))
    elif args.cmd == "list":
        asyncio.run(_list())


if __name__ == "__main__":
    main()
