#!/usr/bin/env python3
"""
fetch_google_token.py — Issue #54

Runs a one-time OAuth2 authorization code flow to obtain a Google
refresh token for a Gmail account.  Designed to be run LOCALLY on your
Mac, where a browser is available.

Usage:
    GOOGLE_CLIENT_ID=<id> GOOGLE_CLIENT_SECRET=<secret> python3 scripts/fetch_google_token.py

Or with a .env file:
    export $(grep -v '^#' .env | xargs) && python3 scripts/fetch_google_token.py

The script will:
  1. Start a local HTTP server on port 8080 to catch the OAuth2 redirect.
  2. Open your browser at the Google authorization URL.
  3. Wait for you to sign in and grant access.
  4. Print the refresh token — copy it, you will need it for the next step.

Required scope: https://mail.google.com/
(Full Gmail access — required for IMAP and SMTP via XOAUTH2.)

Prerequisites:
  - The Google Cloud OAuth2 client type must be "Desktop app".
  - http://localhost:8080 must be in the authorized redirect URIs
    in the Google Cloud Console.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCOPE = "https://mail.google.com/"
REDIRECT_URI = "http://localhost:8080"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


# ---------------------------------------------------------------------------
# Local redirect-capture server
# ---------------------------------------------------------------------------

_auth_code: Optional[str] = None
_server_error: Optional[str] = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _auth_code, _server_error
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            _server_error = params["error"][0]
            self._respond("Authorization denied. You may close this tab.")
        elif "code" in params:
            _auth_code = params["code"][0]
            self._respond(
                "Authorization successful! Return to your terminal."
            )
        else:
            self._respond("Unexpected callback — check your terminal.")

    def _respond(self, message: str) -> None:
        body = f"<html><body><p>{message}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # suppress default request logging


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main() -> None:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print(
            "ERROR: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set.\n"
            "Run: export $(grep -v '^#' .env | xargs) && python3 scripts/fetch_google_token.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build the authorization URL.
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # requests a refresh token
        "prompt": "consent",        # forces refresh token even if previously granted
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)

    # Start the local callback server in a daemon thread.
    server = http.server.HTTPServer(("127.0.0.1", 8080), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    print("\n=== Nuvrail — Google OAuth2 Token Fetch ===\n")
    print("Opening your browser for Google authorization...")
    print(f"\nIf the browser doesn't open, visit this URL manually:\n\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for authorization (complete it in your browser)...")
    thread.join(timeout=120)

    if _server_error:
        print(f"\nERROR: Authorization was denied: {_server_error}", file=sys.stderr)
        sys.exit(1)

    if not _auth_code:
        print(
            "\nERROR: Timed out waiting for authorization (120s). "
            "Try again or check your redirect URI configuration.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Authorization code received. Exchanging for tokens...\n")

    # Exchange the auth code for tokens.
    token_payload = urllib.parse.urlencode({
        "code": _auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode("ascii")

    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        body = exc.read().decode("utf-8", errors="replace")
        print(f"\nERROR: Token exchange failed (HTTP {exc.code}):\n{body}", file=sys.stderr)
        sys.exit(1)

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print(
            "\nERROR: No refresh_token in response. "
            "Make sure the OAuth2 client type is 'Desktop app' and "
            "that you passed access_type=offline and prompt=consent.",
            file=sys.stderr,
        )
        print(f"Full response: {json.dumps(data, indent=2)}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("SUCCESS — copy the refresh token below:")
    print("=" * 60)
    print(f"\nREFRESH_TOKEN={refresh_token}\n")
    print("=" * 60)
    print("\nNext step: run scripts/store_gmail_agent.py on the server")
    print("with this refresh token set as GOOGLE_REFRESH_TOKEN.\n")


if __name__ == "__main__":
    main()
