#!/usr/bin/env python3
"""
migrate_secrets.py — move long-lived credentials into the configured secret backend.

Reads every agent_credentials row and, for each long-lived secret column that
is still stored locally (legacy plaintext or a v1 AES-256-GCM envelope),
re-stores it via the backend selected by NUVRAIL_SECRET_BACKEND and rewrites
the DB column to the v2 reference envelope. Rows already migrated (v2
references) are skipped, so the script is idempotent and safe to re-run.

It runs ONLINE: gateway.credentials.fetch_credential reads v1, v2 and plaintext
transparently, so the proxy keeps working while you migrate gradually.

The short-lived oauth2_access_token is no longer persisted; this script NULLs
any stale cached value it finds.

Usage (inside the gateway container):

    # Dry run — show what would change, touch nothing
    NUVRAIL_SECRET_BACKEND=gcp-sm NUVRAIL_GCP_PROJECT=my-proj \
        python3 scripts/migrate_secrets.py --dry-run

    # Migrate for real
    NUVRAIL_SECRET_BACKEND=gcp-sm NUVRAIL_GCP_PROJECT=my-proj \
        python3 scripts/migrate_secrets.py

For the AES->cloud path the OLD master key must still be available (env var or
master.key) so existing v1 envelopes can be decrypted before re-storing.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = Path(os.environ.get("NUVRAIL_DB_PATH", "/data/nuvrail.db"))

# Long-lived columns that belong in the secret store. The short-lived
# oauth2_access_token is intentionally excluded (it is no longer persisted).
LONG_LIVED_COLUMNS = [
    "upstream_password",
    "oauth2_refresh_token",
    "oauth2_client_secret",
]


async def migrate(dry_run: bool) -> None:
    from gateway.credentials import fetch_credential, is_reference, store_credential
    from gateway.secret_store import configured_backend
    from gateway.state_db import get_db

    backend = configured_backend()
    print(f"Target backend: {backend}")
    if backend == "local":
        print(
            "NOTE: NUVRAIL_SECRET_BACKEND is 'local' — this will (re-)encrypt any "
            "plaintext rows with AES-256-GCM but will NOT move anything to a cloud "
            "secret manager. Set aws-sm or gcp-sm to migrate off local storage."
        )

    async with get_db(DB_PATH) as db, db.execute(
        "SELECT id, user_id, agent_username, upstream_password, "
        "oauth2_refresh_token, oauth2_client_secret, oauth2_access_token "
        "FROM agent_credentials"
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    migrated_rows = 0
    migrated_secrets = 0
    for row in rows:
        updates: dict[str, object] = {}

        for col in LONG_LIVED_COLUMNS:
            val = row.get(col)
            if not val or is_reference(val):
                continue  # empty or already in the external store
            try:
                plaintext = await fetch_credential(val)
            except Exception as exc:
                print(
                    f"ERROR: could not resolve {col} for {row['agent_username']}: {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if dry_run:
                print(f"  would migrate {row['agent_username']}.{col}")
                migrated_secrets += 1
                continue
            ref_envelope = await store_credential(
                plaintext, field=col, owner_user_id=row.get("user_id")
            )
            updates[col] = ref_envelope
            migrated_secrets += 1

        # Clear any stale, no-longer-used cached access token.
        if row.get("oauth2_access_token") is not None:
            if dry_run:
                print(f"  would clear stale oauth2_access_token for {row['agent_username']}")
            else:
                updates["oauth2_access_token"] = None
                updates["oauth2_access_token_expires_at"] = None

        if updates and not dry_run:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = [*list(updates.values()), row["id"]]
            async with get_db(DB_PATH) as db:
                await db.execute(
                    f"UPDATE agent_credentials SET {set_clause} WHERE id = ?",
                    values,
                )
                await db.commit()
            migrated_rows += 1
            print(f"  migrated: {row['agent_username']}")

    verb = "would migrate" if dry_run else "migrated"
    print(f"\nDone. {verb} {migrated_secrets} secret(s) across {migrated_rows} row(s).")
    if not dry_run and backend != "local":
        print(
            "Verify logins still work, then the local NUVRAIL_MASTER_KEY is only "
            "needed for any rows not yet migrated."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Nuvrail secrets to the configured backend")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to the DB or secret store",
    )
    args = parser.parse_args()
    asyncio.run(migrate(args.dry_run))


if __name__ == "__main__":
    main()
