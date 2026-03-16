"""
OAuth2 token storage and refresh.

Loads/saves tokens from ~/.nuvrail/tokens.json.
Handles automatic refresh when access token is expired.

Sub-milestone: 0.3
"""
import json
from pathlib import Path

NUVRAIL_DIR = Path.home() / ".nuvrail"
TOKEN_FILE = NUVRAIL_DIR / "tokens.json"


def build_xoauth2_string(user_email: str, access_token: str) -> str:
    """
    Build the XOAUTH2 base64 string for IMAP AUTHENTICATE.
    Format: user=<email>\x01auth=Bearer <token>\x01\x01
    """
    # TODO: implement in sub-milestone 0.3
    raise NotImplementedError


async def get_access_token() -> str:
    """Return a valid access token, refreshing if necessary."""
    # TODO: implement in sub-milestone 0.3
    raise NotImplementedError
