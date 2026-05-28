"""
/api/v1/push endpoints — Web Push subscription management.

GET  /push/vapid-key   — return public VAPID key for browser subscription
POST /push/subscribe   — register a browser PushSubscription
DELETE /push/subscribe — unregister (by endpoint)

No auth required on vapid-key (public info).
Auth required on subscribe/unsubscribe.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from api.models import PushSubscribeRequest, PushSubscribeResponse, VapidKeyResponse
from api.routes.operations import get_db_path
from gateway.push import get_vapid_public_key
from gateway.state_db import get_db

router = APIRouter()


@router.get("/push/vapid-key", response_model=VapidKeyResponse)
async def vapid_key() -> VapidKeyResponse:
    """Return the VAPID public key. No auth required — this is public info."""
    try:
        key = get_vapid_public_key()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"VAPID key unavailable: {exc}") from exc
    return VapidKeyResponse(public_key=key)


@router.post("/push/subscribe", response_model=PushSubscribeResponse, status_code=201)
async def subscribe(
    body: PushSubscribeRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> PushSubscribeResponse:
    """Register or update a browser push subscription for the current user."""
    now = int(time.time())
    async with get_db(db_path) as db:
        # Upsert: update keys if endpoint already registered. Also (re)claim
        # ownership — an endpoint re-registered while logged in as another user
        # belongs to that user now, so notifications never cross tenants.
        await db.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                p256dh  = excluded.p256dh,
                auth    = excluded.auth
            """,
            (current_user["id"], body.endpoint, body.p256dh, body.auth, now),
        )
        await db.commit()
    return PushSubscribeResponse(subscribed=True, endpoint=body.endpoint)


@router.delete("/push/subscribe", status_code=204)
async def unsubscribe(
    body: PushSubscribeRequest,
    db_path: Path = Depends(get_db_path),
    current_user: dict = Depends(get_current_user),
) -> None:
    """Remove a push subscription owned by the current user."""
    async with get_db(db_path) as db:
        await db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
            (body.endpoint, current_user["id"]),
        )
        await db.commit()
