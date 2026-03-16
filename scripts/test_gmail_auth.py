"""
OAuth2 smoke test — sub-milestone 0.0.

Run this first. If it works, the auth plumbing is solid.

Usage:
    python scripts/test_gmail_auth.py

What it does:
    1. Opens browser for Google OAuth2 consent
    2. Stores access + refresh tokens to ~/.nuvrail/tokens.json
    3. Connects to imap.gmail.com:993
    4. SELECT INBOX and prints EXISTS count
    5. SEARCH ALL and prints UID count
"""
# TODO: implement in sub-milestone 0.0
