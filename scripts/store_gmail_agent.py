#!/usr/bin/env python3
"""
store_gmail_agent.py — Issue #54 (part 2)

Run this on the server (inside the gateway container) to create an
agent_credentials row wired for Gmail XOAUTH2.

Run AFTER fetch_google_token.py has given you a refresh token.

Usage (on server):
    docker compose exec gateway python3 scripts/store_gmail_agent.py

Required env vars (all must be set in .env or passed inline):
    GMAIL_ADDRESS          - the Gmail / Google Workspace address, e.g. martin@animalhorde.com
    GOOGLE_CLIENT_ID       - OAuth2 client ID from Google Cloud Console
    GOOGLE_CLIENT_SECRET   - OAuth2 client secret
    GOOGLE_REFRESH_TOKEN   - refresh token from fetch_google_token.py
    NUVRAIL_BEARER_TOKEN   - your Nuvrail human API token (from the web UI Settings page)
    NUVRAIL_API_URL        - base URL, e.g. http://localhost:8080 (inside container)

The script will:
  1. Call POST /agents to create the agent with OAuth2 credentials.
  2. Print the agent_username and agent_token — save these, the token is shown ONCE.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

NUVRAIL_API_URL = os.environ.get("NUVRAIL_API_URL", "http://localhost:8080").rstrip("/")
NUVRAIL_BEARER_TOKEN = os.environ.get("NUVRAIL_BEARER_TOKEN", "")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")


def _require(name: str, value: str) -> str:
    if not value.strip():
        print(f"ERROR: {name} must be set.", file=sys.stderr)
        sys.exit(1)
    return value.strip()


def main() -> None:
    bearer = _require("NUVRAIL_BEARER_TOKEN", NUVRAIL_BEARER_TOKEN)
    gmail = _require("GMAIL_ADDRESS", GMAIL_ADDRESS)
    client_id = _require("GOOGLE_CLIENT_ID", GOOGLE_CLIENT_ID)
    client_secret = _require("GOOGLE_CLIENT_SECRET", GOOGLE_CLIENT_SECRET)
    refresh_token = _require("GOOGLE_REFRESH_TOKEN", GOOGLE_REFRESH_TOKEN)

    payload = json.dumps({
        "upstream_host": "imap.gmail.com",
        "upstream_smtp_host": "smtp.gmail.com",
        "upstream_imap_port": 993,
        "upstream_smtp_port": 587,
        "upstream_user": gmail,
        "oauth2_provider": "google",
        "oauth2_client_id": client_id,
        "oauth2_client_secret": client_secret,
        "oauth2_refresh_token": refresh_token,
        "label": f"gmail-xoauth2-{gmail}",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{NUVRAIL_API_URL}/api/v1/agents",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        body = exc.read().decode("utf-8", errors="replace")
        print(f"\nERROR: Agent creation failed (HTTP {exc.code}):\n{body}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("SUCCESS — Gmail XOAUTH2 agent created")
    print("=" * 60)
    print(f"\nagent_username : {data.get('agent_username')}")
    print(f"agent_token    : {data.get('agent_token')}")
    print(f"label          : {data.get('label')}")
    print(f"upstream_user  : {data.get('upstream_user')}")
    print("\nSave the agent_token now — it is shown exactly once.")
    print("Configure your AI client with:")
    print("  IMAP host : test.nuvrail.com  port: 993")
    print("  SMTP host : test.nuvrail.com  port: 465")
    print(f"  Username  : {data.get('agent_username')}")
    print("  Password  : <agent_token above>\n")


if __name__ == "__main__":
    main()
