#!/usr/bin/env python3
"""
check_oauth2_token.py — diagnostic tool

Checks the OAuth2 credentials stored in the DB for a given agent:
- Fetches a fresh access token via the refresh token
- Calls Google's tokeninfo endpoint to confirm the granted scopes and audience
- Logs scope, audience (client_id), and expiry — NOT the token itself

Run inside the gateway container:
    docker compose exec gateway python3 scripts/check_oauth2_token.py <agent_username>

e.g.:
    docker compose exec gateway python3 scripts/check_oauth2_token.py nuvrail_edda3807f21c3c9b
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

# Ensure gateway package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = Path(os.environ.get("NUVRAIL_DB_PATH", "/data/nuvrail.db"))
TOKENINFO_URL = "https://www.googleapis.com/oauth2/v3/tokeninfo"


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_oauth2_token.py <agent_username>", file=sys.stderr)
        sys.exit(1)

    agent_username = sys.argv[1]

    from gateway.state_db import get_db
    from gateway.credentials import fetch_credential
    from gateway.oauth2_tokens import _refresh_google_token

    # 1. Load the agent credentials row.
    async with get_db(DB_PATH) as db:
        async with db.execute(
            """
            SELECT id, agent_username, upstream_user, oauth2_provider,
                   oauth2_refresh_token, oauth2_client_id, oauth2_client_secret,
                   oauth2_access_token, oauth2_access_token_expires_at
            FROM agent_credentials
            WHERE agent_username = ?
            """,
            (agent_username,),
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        print(f"ERROR: No agent_credentials row found for {agent_username!r}", file=sys.stderr)
        sys.exit(1)

    row = dict(row)
    print(f"\nAgent          : {agent_username}")
    print(f"upstream_user  : {row['upstream_user']}")
    print(f"oauth2_provider: {row['oauth2_provider']}")
    print(f"client_id      : {row['oauth2_client_id'] or '(not set)'}")
    print(f"refresh_token  : {'(set)' if row['oauth2_refresh_token'] else '(NOT SET)'}")
    print(f"client_secret  : {'(set)' if row['oauth2_client_secret'] else '(NOT SET)'}")
    cached_exp = row.get("oauth2_access_token_expires_at") or 0
    import time
    print(f"cached token   : {'(set, expires in ' + str(int(cached_exp - time.time())) + 's)' if row['oauth2_access_token'] else '(none)'}")

    # 2. Verify the cached access token if one exists.
    if row["oauth2_access_token"]:
        print("\n--- Checking cached access token via tokeninfo ---")
        try:
            cached_access_token = await fetch_credential(row["oauth2_access_token"])
        except Exception as exc:
            print(f"ERROR: Failed to decrypt cached token: {exc}", file=sys.stderr)
            print("      NUVRAIL_MASTER_KEY mismatch or corrupt cached token — proxy is sending garbage to Gmail.")
            cached_access_token = None

        if cached_access_token:
            url = f"{TOKENINFO_URL}?access_token={cached_access_token}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    info = json.loads(resp.read().decode("utf-8"))
                print(f"Cached scope   : {info.get('scope', '(none)')}")
                print(f"Cached expires : {info.get('exp', '?')}")
                if "https://mail.google.com/" in info.get("scope", ""):
                    print("✓  Cached token is valid and has the required scope")
                else:
                    print("✗  Cached token is MISSING required scope — proxy is using this bad token")
            except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
                body = exc.read().decode("utf-8", errors="replace")
                print(f"✗  Cached token INVALID (tokeninfo HTTP {exc.code}): {body}")
                print("   Proxy is sending this invalid token to Gmail — that's your 400.")
                print("   Fix: clear the cached token from the DB so the proxy force-refreshes.")
                print("   Run inside the container:")
                print(f"     sqlite3 /data/nuvrail.db \"UPDATE agent_credentials SET oauth2_access_token=NULL, oauth2_access_token_expires_at=NULL WHERE agent_username='{agent_username}'\"")
    else:
        print("\nNo cached access token in DB — proxy will refresh on next connection.")

    # 3. Force a fresh token refresh (bypass cache) to confirm the refresh token works.
    print("\n--- Refreshing access token from Google ---")
    if not row["oauth2_refresh_token"] or not row["oauth2_client_id"] or not row["oauth2_client_secret"]:
        print("ERROR: Missing one or more OAuth2 credential fields in DB.", file=sys.stderr)
        sys.exit(1)

    try:
        refresh_token = await fetch_credential(row["oauth2_refresh_token"])
        client_secret = await fetch_credential(row["oauth2_client_secret"])
        client_id = row["oauth2_client_id"]
    except Exception as exc:
        print(f"ERROR: Failed to decrypt credentials: {exc}", file=sys.stderr)
        print("      (This means NUVRAIL_MASTER_KEY is wrong or credentials are corrupt.)")
        sys.exit(1)

    try:
        access_token, expires_at = await _refresh_google_token(client_id, client_secret, refresh_token)
        print(f"Token refresh  : OK (expires in {int(expires_at - time.time())}s)")
    except Exception as exc:
        print(f"Token refresh  : FAILED — {exc}", file=sys.stderr)
        print("\nThis means the refresh token or client credentials are invalid.")
        print("You need to re-run fetch_google_token.py and store_gmail_agent.py.")
        sys.exit(1)

    # 3. Call tokeninfo to check granted scopes — safe, no credential in output.
    print("\n--- Checking granted scopes via Google tokeninfo ---")
    url = f"{TOKENINFO_URL}?access_token={access_token}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        body = exc.read().decode("utf-8", errors="replace")
        print(f"tokeninfo call FAILED (HTTP {exc.code}): {body}", file=sys.stderr)
        sys.exit(1)

    granted_scope = info.get("scope", "(none)")
    audience = info.get("aud", "(unknown)")
    exp_secs = info.get("exp", "?")

    print(f"Granted scopes : {granted_scope}")
    print(f"Audience (aud) : {audience}")
    print(f"Token expires  : {exp_secs}")

    required_scope = "https://mail.google.com/"
    if required_scope in granted_scope:
        print(f"\n✓  Token HAS the required scope ({required_scope})")
        print("   If IMAP is still failing, the issue is likely a Google Workspace")
        print("   domain policy or Gmail API not enabled in your GCP project.")
    else:
        print(f"\n✗  Token is MISSING the required scope: {required_scope}")
        print(f"   Granted: {granted_scope}")
        print("\n   Fix: re-run fetch_google_token.py (it will request the correct scope)")
        print("   then re-run store_gmail_agent.py with the new refresh token.")


if __name__ == "__main__":
    asyncio.run(main())
