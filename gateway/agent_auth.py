"""
Agent credential authentication for the IMAP and SMTP proxies (Lane 2:
AI agent → proxy).

Both proxies authenticate an agent the same way — decode the supplied
credentials, look up the agent_credentials row by username, and verify the
presented token against the stored bcrypt hash. Keeping that logic here means
there is exactly ONE function to review for "how does an agent authenticate?"
and ONE place that parses an untrusted SASL payload.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional


def decode_sasl_plain(payload: "str | bytes") -> Optional[tuple[str, str]]:
    """Decode a SASL PLAIN (RFC 4616) payload into ``(authcid, password)``.

    The plaintext form is ``authzid \\x00 authcid \\x00 passwd`` and ``payload``
    is its base64 encoding. We use ``authcid`` (the agent username) and
    ``passwd``; ``authzid`` is ignored.

    Returns ``(username, password)``, or ``None`` if the payload is not valid
    base64 or does not contain the three NUL-separated fields. Never raises —
    malformed input from an unauthenticated client must not crash the proxy.
    """
    try:
        raw = base64.b64decode(payload)
    except Exception:  # noqa: BLE001 — any decode error is just "invalid creds"
        return None
    parts = raw.split(b"\x00")
    if len(parts) < 3:
        return None
    username = parts[1].decode("utf-8", errors="replace")
    password = parts[2].decode("utf-8", errors="replace")
    return username, password


async def get_agent_credential(agent_id: Optional[int], db_path: Path) -> Optional[dict]:
    """Return the agent_credentials row for ``agent_id`` as a dict, or None."""
    if agent_id is None:
        return None
    from gateway.state_db import get_db  # noqa: PLC0415

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM agent_credentials WHERE id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def verify_agent_login(
    agent_user: str, agent_token: str, db_path: Path
) -> Optional[dict]:
    """Verify an agent's username + token against agent_credentials.

    Returns the credential row dict when the username exists, is not revoked,
    and the token matches the stored bcrypt hash; otherwise None. Used on every
    IMAP LOGIN / SMTP AUTH, so token hashing uses bcrypt rounds=10.
    """
    if not agent_user:
        return None
    from gateway.state_db import get_db  # noqa: PLC0415

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM agent_credentials WHERE agent_username = ? AND revoked_at IS NULL",
            (agent_user,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None

    # Imported lazily so the proxy processes don't pull in the API/bcrypt stack
    # at module-import time.
    from api.auth import verify_password  # noqa: PLC0415

    if not verify_password(agent_token, row["hashed_token"]):
        return None
    return dict(row)
