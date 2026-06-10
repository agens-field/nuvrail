"""
Data-retention purge — GDPR storage limitation (R5 / Art. 5(1)(e) + Art. 17).

Account erasure is two-stage (see api/routes/account.py for stage 1):

  ┌──────────────────────────────────────────────────────────────────┐
  │ STAGE 1 — at DELETE /account (immediate)                          │
  │   pseudonymize: email → deleted+<id>@nuvrail.invalid,             │
  │   null name/password/tokens/upstream creds, revoke agents,        │
  │   set users.deleted_at = now. Audit log RETAINED (Art. 17(3)).    │
  └────────────────────────────┬─────────────────────────────────────┘
                               │  ... NUVRAIL_RETENTION_DAYS later (default 365)
                               ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ STAGE 2 — this module (background sweep)                          │
  │   find users WHERE deleted_at IS NOT NULL                         │
  │                    AND deleted_at <= now - retention_window       │
  │   for each: HARD DELETE every row linked to that user, in FK-safe │
  │   child→parent order, in one transaction:                         │
  │     pending_reverts → staged_operations → audit_log →             │
  │     auto_approval_rules → push_subscriptions →                    │
  │     agent_credentials → users                                     │
  └──────────────────────────────────────────────────────────────────┘

Why a 12-month gap (default): retention under Art. 17(3)(b)/(e) for security
and legal-claims purposes is time-bounded, not indefinite. After the window the
legitimate-interest basis lapses and the data is hard-deleted, satisfying the
storage-limitation principle. The window is configurable via
``NUVRAIL_RETENTION_DAYS``.

Scope note — what is and is NOT purged:
  PURGED (user-scoped data):
    users, agent_credentials, auto_approval_rules, push_subscriptions,
    audit_log (user_id), staged_operations (via agent), pending_reverts (via op),
    folders + messages (the per-user IMAP state mirror).
  NOTE (issue #73): the mailbox mirror is now tenant-scoped (folders.user_id +
    UNIQUE(user_id, name)), so it IS the deleted user's personal data and must
    be purged here. This reverses the earlier R5 single-tenant assumption that
    the mirror was global and out of scope for per-user deletion.

This module provides:
  purge_expired_deleted_users(db_path) — run once, purge all past-window accounts
  run_retention_loop(db_path, interval_seconds) — asyncio task for continuous use
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from gateway.credentials import purge_agent_upstream_secrets
from gateway.state_db import DB_PATH, get_db

logger = logging.getLogger(__name__)

# Days a tombstoned (deleted_at) account is retained before hard purge.
# Default 365 (12 months). Overridable via env (e.g. tiny value in tests).
_RETENTION_DAYS: float = float(os.environ.get("NUVRAIL_RETENTION_DAYS", "365"))

# Max accounts to purge in one run (bounds the transaction / lock window).
_BATCH_SIZE = 100


async def find_purgeable_users(
    db_path: Path = DB_PATH, *, now: int | None = None
) -> list[int]:
    """Return user ids whose deletion is older than the retention window."""
    now_ts = now if now is not None else int(time.time())
    cutoff = now_ts - int(_RETENTION_DAYS * 86400)
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT id FROM users
            WHERE deleted_at IS NOT NULL
              AND deleted_at <= ?
            ORDER BY deleted_at ASC
            LIMIT ?
            """,
            (cutoff, _BATCH_SIZE),
        ) as cur:
            return [int(row["id"]) for row in await cur.fetchall()]


async def _purge_user(user_id: int, db_path: Path) -> None:
    """Hard-delete every row linked to ``user_id`` in FK-safe child→parent order.

    Done in a single transaction so a crash mid-purge can't leave orphans: the
    account either survives intact (retry next run) or is fully gone.
    """
    async with get_db(db_path) as db:
        # Resolve this user's agent ids + operation ids first (needed to reach
        # the agent_id/operation_id-scoped child tables, which have no user_id).
        # We also pull each agent's stored upstream secrets here so we can tear
        # them down in the external secret store (#72) BEFORE step 7 deletes the
        # rows that hold the references.
        async with db.execute(
            """
            SELECT id, oauth2_provider,
                   upstream_password, oauth2_refresh_token,
                   oauth2_access_token, oauth2_client_secret
            FROM agent_credentials WHERE user_id = ?
            """,
            (user_id,),
        ) as cur:
            agent_rows = [dict(r) for r in await cur.fetchall()]
        agent_ids = [str(r["id"]) for r in agent_rows]

        op_ids: list[str] = []
        if agent_ids:
            ph = ",".join("?" * len(agent_ids))
            async with db.execute(
                f"SELECT id FROM staged_operations WHERE agent_id IN ({ph})",  # noqa: S608
                agent_ids,
            ) as cur:
                op_ids = [str(r["id"]) for r in await cur.fetchall()]

        # 1. pending_reverts (child of staged_operations, via operation_id)
        if op_ids:
            ph_ops = ",".join("?" * len(op_ids))
            await db.execute(
                f"DELETE FROM pending_reverts WHERE operation_id IN ({ph_ops})",  # noqa: S608
                op_ids,
            )
            # 2. staged_operations (child of agent_credentials, via agent_id)
            await db.execute(
                f"DELETE FROM staged_operations WHERE id IN ({ph_ops})",  # noqa: S608
                op_ids,
            )

        # 3. audit_log (user-scoped). Includes the 'account_deleted' tombstone
        #    row itself — by design: after the window nothing about this user
        #    remains.
        await db.execute("DELETE FROM audit_log WHERE user_id = ?", (user_id,))
        # 4. auto_approval_rules (user-scoped)
        await db.execute(
            "DELETE FROM auto_approval_rules WHERE user_id = ?", (user_id,)
        )
        # 5. push_subscriptions (user-scoped; usually already gone from stage 1)
        await db.execute(
            "DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,)
        )
        # 6. mailbox mirror (issue #73): messages first (child of folders via
        #    folder_id), then folders (now user-scoped). Deleting the user's
        #    folder rows then their messages removes all mirrored mail metadata
        #    (subject/sender/message_id/flags) — required per-user data now that
        #    the mirror is tenant-scoped.
        async with db.execute(
            "SELECT id FROM folders WHERE user_id = ?", (user_id,)
        ) as cur:
            folder_ids = [str(r["id"]) for r in await cur.fetchall()]
        if folder_ids:
            ph_f = ",".join("?" * len(folder_ids))
            await db.execute(
                f"DELETE FROM messages WHERE folder_id IN ({ph_f})",  # noqa: S608
                folder_ids,
            )
        await db.execute("DELETE FROM folders WHERE user_id = ?", (user_id,))
        # 6b. Tear down upstream secrets in the external store + revoke OAuth2
        #     grants (#72) as a backstop for anything stage-1 deletion missed,
        #     BEFORE step 7 deletes the rows holding the references. Best-effort;
        #     failures are logged inside the helper and do not abort the purge.
        for row in agent_rows:
            await purge_agent_upstream_secrets(row)
        # 7. agent_credentials (child of users)
        await db.execute(
            "DELETE FROM agent_credentials WHERE user_id = ?", (user_id,)
        )
        # 8. users (parent) — last
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))

        await db.commit()


async def purge_expired_deleted_users(
    db_path: Path = DB_PATH, *, now: int | None = None
) -> int:
    """Hard-purge all accounts whose retention window has elapsed.

    Returns the number of accounts purged this pass. Safe to call repeatedly;
    each user is purged in its own transaction.
    """
    user_ids = await find_purgeable_users(db_path=db_path, now=now)
    if not user_ids:
        return 0

    logger.info(
        "[retention] %d account(s) past the %.0f-day window — purging",
        len(user_ids), _RETENTION_DAYS,
    )
    count = 0
    for user_id in user_ids:
        try:
            await _purge_user(user_id, db_path)
            count += 1
            logger.info("[retention] purged user id=%d", user_id)
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the sweep
            logger.error("[retention] failed to purge user id=%d: %s", user_id, exc)
    return count


async def run_retention_loop(
    db_path: Path = DB_PATH,
    interval_seconds: float = 86400.0,
    initial_delay_seconds: float = 120.0,
) -> None:
    """Asyncio background task: periodically purge past-window deleted accounts.

    interval_seconds: how often to sweep (default daily — the window is months,
        so daily granularity is far more than enough).
    initial_delay_seconds: delay before the first sweep after startup.

    Run via asyncio.create_task() in the FastAPI lifespan handler. Exits cleanly
    on asyncio.CancelledError.
    """
    logger.info(
        "[retention] Loop starting — interval=%.0fs, window=%.0f days",
        interval_seconds, _RETENTION_DAYS,
    )
    try:
        await asyncio.sleep(initial_delay_seconds)
        while True:
            try:
                count = await purge_expired_deleted_users(db_path=db_path)
                if count > 0:
                    logger.info("[retention] Purged %d account(s)", count)
            except Exception as exc:  # noqa: BLE001
                logger.error("[retention] Unexpected error during sweep: %s", exc)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("[retention] Loop cancelled — shutting down")
        raise
