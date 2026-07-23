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


def decode_sasl_plain(payload: str | bytes) -> tuple[str, str] | None:
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
    except Exception:
        return None
    parts = raw.split(b"\x00")
    if len(parts) < 3:
        return None
    username = parts[1].decode("utf-8", errors="replace")
    password = parts[2].decode("utf-8", errors="replace")
    return username, password


async def get_agent_credential(
    agent_id: int | None,
    db_path: Path,
    *,
    require_active: bool = True,
) -> dict | None:
    """Return the agent_credentials row for ``agent_id`` as a dict, or None.

    When ``require_active`` is True (the default, used on the execution path),
    the row is returned only if the credential is **not revoked** and the
    owning user is **not suspended** (ToS §4/§13 enforcement, issue #65). This
    ensures a revocation or suspension is honored even for operations that were
    staged *before* the revocation/suspension — the approve/execute path won't
    relay for a now-dead credential or a suspended account.

    Set ``require_active=False`` only for read/inspection use cases that must
    see revoked/suspended credentials (none on the send path).
    """
    if agent_id is None:
        return None
    from gateway.state_db import get_db

    if require_active:
        sql = (
            "SELECT ac.* FROM agent_credentials ac "
            "JOIN users u ON u.id = ac.user_id "
            "WHERE ac.id = ? AND ac.revoked_at IS NULL "
            "AND u.suspended_at IS NULL AND u.deleted_at IS NULL"
        )
    else:
        sql = "SELECT * FROM agent_credentials WHERE id = ?"

    async with get_db(db_path) as db, db.execute(sql, (agent_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def verify_agent_login(
    agent_user: str, agent_token: str, db_path: Path
) -> dict | None:
    """Verify an agent's username + token against agent_credentials.

    Returns the credential row dict when the username exists, is not revoked,
    the owning user is not suspended or deleted, and the token matches the
    stored bcrypt hash; otherwise None. Used on every IMAP LOGIN / SMTP AUTH
    (both proxies call this), so token hashing uses bcrypt rounds=10.

    The JOIN to ``users`` enforces account suspension (ToS §4/§13, issue #65)
    at the proxy edge: a suspended account's agents fail LOGIN/AUTH and can
    therefore neither stage nor send. ``revoked_at`` handles per-credential
    revocation; ``suspended_at``/``deleted_at`` handle the whole account.
    """
    if not agent_user:
        return None
    from gateway.state_db import get_db

    async with get_db(db_path) as db, db.execute(
        "SELECT ac.* FROM agent_credentials ac "
        "JOIN users u ON u.id = ac.user_id "
        "WHERE ac.agent_username = ? AND ac.revoked_at IS NULL "
        "AND u.suspended_at IS NULL AND u.deleted_at IS NULL",
        (agent_user,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None

    # Imported lazily so the proxy processes don't pull in the API/bcrypt stack
    # at module-import time.
    from api.auth import verify_password

    if not verify_password(agent_token, row["hashed_token"]):
        return None
    return dict(row)
