#!/usr/bin/env python3
"""
test_imap_xoauth2_direct.py — bypass the Nuvrail proxy entirely

Connects directly to imap.gmail.com:993 using the access token stored
for the given agent and attempts AUTHENTICATE XOAUTH2. This isolates
whether the 400 is a proxy bug or a Google/Workspace issue.

Run inside the gateway container:
    docker compose exec gateway python3 scripts/test_imap_xoauth2_direct.py nuvrail_edda3807f21c3c9b
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = Path(os.environ.get("NUVRAIL_DB_PATH", "/data/nuvrail.db"))


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: test_imap_xoauth2_direct.py <agent_username>", file=sys.stderr)
        sys.exit(1)

    agent_username = sys.argv[1]

    from gateway.state_db import get_db
    from gateway.credentials import decrypt_credential
    from gateway.oauth2_tokens import _refresh_google_token

    # Load credentials from DB.
    async with get_db(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM agent_credentials WHERE agent_username = ?",
            (agent_username,),
        ) as cur:
            row = dict(await cur.fetchone() or {})

    if not row:
        print(f"ERROR: agent {agent_username!r} not found", file=sys.stderr)
        sys.exit(1)

    email = row["upstream_user"]
    client_id = row["oauth2_client_id"]
    client_secret = decrypt_credential(row["oauth2_client_secret"])
    refresh_token = decrypt_credential(row["oauth2_refresh_token"])

    # Always get a fresh token for this test (bypass cache).
    print(f"\nFetching fresh access token for {email}...")
    access_token, expires_at = await _refresh_google_token(client_id, client_secret, refresh_token)
    print(f"Token obtained (expires in {int(expires_at - time.time())}s)")

    # Build the XOAUTH2 string.
    raw = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    xoauth2_b64 = base64.b64encode(raw.encode("ascii")).decode("ascii")

    print(f"\nConnecting directly to imap.gmail.com:993...")
    import ssl
    ssl_ctx = ssl.create_default_context()

    reader, writer = await asyncio.open_connection("imap.gmail.com", 993, ssl=ssl_ctx)

    # Read greeting.
    greeting = await reader.readline()
    print(f"Greeting : {greeting.decode(errors='replace').strip()}")

    # Send AUTHENTICATE XOAUTH2.
    tag = "X001"
    cmd = f"{tag} AUTHENTICATE XOAUTH2 {xoauth2_b64}\r\n"
    writer.write(cmd.encode("ascii"))
    await writer.drain()
    print(f"Sent     : {tag} AUTHENTICATE XOAUTH2 [token redacted]")

    # Read first response line.
    resp1 = await reader.readline()
    resp1_str = resp1.decode(errors="replace").strip()

    if resp1.startswith(b"+ "):
        # SASL challenge — auth failed. Decode the error JSON.
        try:
            challenge_json = json.loads(base64.b64decode(resp1[2:].strip()))
            print(f"Challenge: status={challenge_json.get('status')} scope={challenge_json.get('scope')}")
        except Exception:
            print(f"Challenge: {resp1_str} (decode failed)")

        # Send empty abort.
        writer.write(b"\r\n")
        await writer.drain()

        # Read final NO.
        resp2 = await reader.readline()
        print(f"Final    : {resp2.decode(errors='replace').strip()}")
        print(f"\n✗ Gmail rejected XOAUTH2 directly — not a proxy bug.")
        print(f"  This is a Google/Workspace configuration issue.")
        print(f"\n  Things to check in Google Cloud Console:")
        print(f"  1. OAuth consent screen → is the app in 'Testing' or 'Production' mode?")
        print(f"     Testing mode: check that {email} is listed as a test user.")
        print(f"  2. Is the Gmail API enabled for project?")
        print(f"     https://console.cloud.google.com/apis/library/gmail.googleapis.com")
        print(f"  3. In Workspace Admin Console > Security > API controls:")
        print(f"     check whether the OAuth client ID is explicitly blocked.")

    elif b" OK " in resp1.upper():
        print(f"Response : {resp1_str}")
        print(f"\n✓ Gmail accepted XOAUTH2 directly — proxy bug likely.")
        print(f"  The proxy is building a different XOAUTH2 string than this script.")

        # Clean logout.
        writer.write(b"X002 LOGOUT\r\n")
        await writer.drain()
        # Drain logout response
        for _ in range(3):
            line = await reader.readline()
            if b"BYE" in line or b"OK" in line:
                break

    else:
        print(f"Response : {resp1_str}")
        print(f"\n? Unexpected response — neither a challenge nor OK.")

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
