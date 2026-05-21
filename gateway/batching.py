"""
Operation batching engine.

Groups write operations targeting the same folder (and protocol) within a
sliding time window into a single batch, so the approval UI can show them
as one card rather than N individual items.

Phase 2 implementation: in-memory dict with TTL expiry.
  - Survives within a single gateway process lifetime.
  - On restart, open batches are lost and new ones begin — acceptable for
    Phase 2 with a single user.
  - Phase 3: migrate to a `batches` table in SQLite/Postgres so batches
    survive restarts and can be inspected via the API.

Batch lifecycle:
  ┌─────────────────────────────────────────────────────────────────┐
  │  first op in folder                                             │
  │        │                                                        │
  │        ▼                                                        │
  │  create batch (id=batch_XXXXXX, folder=X, window starts)        │
  │        │                                                        │
  │  subsequent ops in same folder within window                    │
  │        │                                                        │
  │        ▼                                                        │
  │  return same batch_id                                           │
  │        │                                                        │
  │  window expires (> window_seconds since batch created)          │
  │        │                                                        │
  │        ▼                                                        │
  │  next op creates a NEW batch                                    │
  └─────────────────────────────────────────────────────────────────┘

Usage (in proxy.py or staging.py):
    from gateway.batching import get_or_create_batch
    batch_id = await get_or_create_batch(folder="INBOX", window_seconds=30)
    op_id = await create_operation(..., batch_id=batch_id)
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# In-memory batch registry
# ---------------------------------------------------------------------------

@dataclass
class _Batch:
    id: str
    folder: str
    protocol: str
    created_at: float
    window_seconds: int
    op_ids: list[str] = field(default_factory=list)

    def is_open(self) -> bool:
        """True if the batch window has not yet expired."""
        return (time.monotonic() - self.created_at) < self.window_seconds


# (folder, protocol) → _Batch
_batches: dict[tuple[str, str], _Batch] = {}
_lock = asyncio.Lock()


def _new_batch_id() -> str:
    return "batch_" + secrets.token_urlsafe(6)


async def get_or_create_batch(
    folder: str,
    protocol: str = "imap",
    window_seconds: int = 30,
    agent_id: Optional[int] = None,
) -> str:
    """Return an existing open batch_id for the folder+agent, or create a new one.

    Thread-safe (asyncio lock). Idempotent within the window.

    Args:
        folder: IMAP folder name (e.g. "INBOX", "[Gmail]/Sent Mail").
        protocol: "imap" or "smtp". SMTP sends are never batched (each
                  send is its own approval card), so pass protocol="smtp"
                  only if you explicitly want SMTP batching.
        window_seconds: How long (seconds) to keep a batch open for new ops.
        agent_id: Scopes the batch to a specific agent so different agents
                  don't share a batch even if they write to the same folder.

    Returns:
        A batch_id string (e.g. "batch_AbCdEf12").
    """
    key = (folder, protocol, agent_id)
    async with _lock:
        existing = _batches.get(key)
        if existing is not None and existing.is_open():
            return existing.id

        # Open a new batch
        batch = _Batch(
            id=_new_batch_id(),
            folder=folder,
            protocol=protocol,
            created_at=time.monotonic(),
            window_seconds=window_seconds,
        )
        _batches[key] = batch
        return batch.id


async def record_op_in_batch(batch_id: str, op_id: str) -> None:
    """Associate an op_id with an open batch (for audit/display purposes).

    Silently no-ops if the batch_id is not found (e.g. after a restart).
    """
    async with _lock:
        for batch in _batches.values():
            if batch.id == batch_id:
                batch.op_ids.append(op_id)
                return


async def get_batch_op_ids(batch_id: str) -> list[str]:
    """Return the list of op_ids associated with a batch.

    Returns an empty list if the batch is not found.
    """
    async with _lock:
        for batch in _batches.values():
            if batch.id == batch_id:
                return list(batch.op_ids)
        return []


def purge_expired_batches() -> int:
    """Remove expired batches from the in-memory registry. Returns count removed.

    Call periodically (e.g. from a background task) to prevent unbounded growth.
    In practice, with window_seconds=30 and low op volume, the dict stays tiny.
    """
    expired = [k for k, v in _batches.items() if not v.is_open()]
    for k in expired:
        del _batches[k]
    return len(expired)
