"""
Auto-approval rules engine.

Evaluates staged operations against rows in auto_approval_rules, in priority
order (priority DESC, id ASC), returning the first matching action.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Optional

from gateway.state_db import DB_PATH, get_db


def _extract_sender(op: dict) -> Optional[str]:
    """Extract a sender address from operation data when available."""
    sender = op.get("sender")
    if isinstance(sender, str) and sender.strip():
        return sender.strip()

    envelope = op.get("smtp_envelope")
    if isinstance(envelope, str):
        try:
            envelope = json.loads(envelope)
        except json.JSONDecodeError:
            envelope = None
    if isinstance(envelope, dict):
        env_from = envelope.get("from")
        if isinstance(env_from, str) and env_from.strip():
            return env_from.strip()

    snapshot = op.get("snapshot")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = None
    if isinstance(snapshot, dict):
        senders: list[str] = []
        for state in snapshot.values():
            if not isinstance(state, dict):
                continue
            value = state.get("sender")
            if isinstance(value, str) and value.strip():
                senders.append(value.strip())
        if senders:
            return senders[0]

    return None


def _matches(rule: dict, op: dict) -> bool:
    """Return True when a rule matches an operation."""
    op_type = op.get("op_type")
    folder_from = op.get("folder_from")
    sender = _extract_sender(op)

    rule_op_type = rule.get("op_type")
    if rule_op_type is not None and rule_op_type != op_type:
        return False

    rule_sender_pattern = rule.get("sender_pattern")
    if rule_sender_pattern is not None:
        if not sender:
            return False
        if not fnmatch.fnmatch(sender, rule_sender_pattern):
            return False

    rule_folder_from = rule.get("folder_from")
    if rule_folder_from is not None and rule_folder_from != folder_from:
        return False

    return True


async def get_matching_rule(op: dict, db_path: Path = DB_PATH) -> Optional[dict]:
    """Return the first enabled rule matching the operation, else None."""
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT *
            FROM auto_approval_rules
            WHERE enabled = 1
            ORDER BY priority DESC, id ASC
            """
        ) as cur:
            rows = await cur.fetchall()

    for row in rows:
        rule = dict(row)
        if _matches(rule, op):
            return rule
    return None


async def evaluate_rules(op: dict, db_path: Path = DB_PATH) -> str | None:
    """Return 'approve', 'reject', or None for a staged operation."""
    rule = await get_matching_rule(op, db_path=db_path)
    if rule is None:
        return None
    action = rule.get("action")
    return action if action in {"approve", "reject"} else None
