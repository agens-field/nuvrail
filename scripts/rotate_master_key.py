#!/usr/bin/env python3
"""
rotate_master_key.py — rotate the AES-256-GCM master key

Run this if the master key has been compromised (e.g. accidentally committed
to git). It decrypts every encrypted credential in the DB using the OLD key
and re-encrypts it with a NEW key.

Usage (on the server, inside the gateway container):

    # Step 1: generate a new key and print it
    docker compose exec gateway python3 scripts/rotate_master_key.py --generate

    # Step 2: rotate all credentials using the old key from env + new key from stdin
    docker compose exec gateway python3 scripts/rotate_master_key.py --rotate --new-key <hex>

    # Or pipe it:
    docker compose exec gateway python3 scripts/rotate_master_key.py --generate | \\
        xargs -I{} docker compose exec gateway python3 scripts/rotate_master_key.py --rotate --new-key {}

After rotation:
  1. Update NUVRAIL_MASTER_KEY in your .env with the new hex key.
  2. Restart the gateway: docker compose restart gateway
  3. Verify logins still work.

The old key is read from NUVRAIL_MASTER_KEY env var (same as normal startup).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = Path(os.environ.get("NUVRAIL_DB_PATH", "/data/nuvrail.db"))

# Columns in agent_credentials that are AES-256-GCM encrypted.
ENCRYPTED_COLUMNS = [
    "upstream_password",
    "oauth2_refresh_token",
    "oauth2_client_secret",
    "oauth2_access_token",
]


def generate_key() -> str:
    """Generate a new 32-byte key and return it as hex."""
    return secrets.token_hex(32)


async def rotate(new_key_hex: str) -> None:
    import json

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from gateway.credentials import decrypt_credential, is_encrypted
    from gateway.state_db import get_db

    new_key = bytes.fromhex(new_key_hex)
    if len(new_key) != 32:
        print("ERROR: new key must be 64 hex chars (32 bytes)", file=sys.stderr)
        sys.exit(1)

    new_aesgcm = AESGCM(new_key)

    def reencrypt(plaintext: str) -> str:
        import os as _os
        nonce = _os.urandom(12)
        ct = new_aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return json.dumps({"v": 1, "iv": nonce.hex(), "ct": ct.hex()})

    async with get_db(DB_PATH) as db:
        async with db.execute(
            "SELECT id, agent_username, upstream_password, oauth2_refresh_token, "
            "oauth2_client_secret, oauth2_access_token FROM agent_credentials"
        ) as cur:
            rows = await cur.fetchall()

        updated = 0
        for row in rows:
            row = dict(row)
            updates: dict[str, str | None] = {}

            for col in ENCRYPTED_COLUMNS:
                val = row.get(col)
                if val and is_encrypted(val):
                    try:
                        plaintext = decrypt_credential(val)
                        updates[col] = reencrypt(plaintext)
                    except Exception as exc:
                        print(
                            f"ERROR: failed to decrypt {col} for agent "
                            f"{row['agent_username']}: {exc}",
                            file=sys.stderr,
                        )
                        sys.exit(1)

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                values = [*list(updates.values()), row["id"]]
                await db.execute(
                    f"UPDATE agent_credentials SET {set_clause} WHERE id = ?",
                    values,
                )
                updated += 1
                print(f"  rotated: {row['agent_username']}")

        await db.commit()

    print(f"\nDone. {updated} agent(s) re-encrypted.")
    print(f"\nNext: set NUVRAIL_MASTER_KEY={new_key_hex} in your .env, then restart gateway.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate Nuvrail master key")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="Print a new random key and exit")
    group.add_argument("--rotate", action="store_true", help="Rotate all credentials to new key")
    parser.add_argument("--new-key", help="New key as 64-char hex string (required with --rotate)")
    args = parser.parse_args()

    if args.generate:
        print(generate_key())
        return

    if args.rotate:
        if not args.new_key:
            print("ERROR: --new-key is required with --rotate", file=sys.stderr)
            sys.exit(1)
        if not os.environ.get("NUVRAIL_MASTER_KEY"):
            print(
                "ERROR: NUVRAIL_MASTER_KEY must be set (the OLD key) to decrypt existing credentials.",
                file=sys.stderr,
            )
            sys.exit(1)
        asyncio.run(rotate(args.new_key))


if __name__ == "__main__":
    main()
