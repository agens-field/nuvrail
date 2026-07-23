"""
Web Push notification sender — Milestone MVP.

VAPID key pair is generated on first run and stored at:
  NUVRAIL_DATA_DIR/vapid_private.pem   (private key — keep secret)
  NUVRAIL_DATA_DIR/vapid_public.pem    (public key — sent to browser)

Environment:
  NUVRAIL_VAPID_EMAIL — contact email for VAPID header (required for push)
                        e.g. "mailto:admin@example.com"
                        Defaults to "mailto:admin@example.com" if not set.

Usage:
  notify_staged(op_id, description, is_urgent, db_path) — fire and forget
  get_vapid_public_key() -> str — base64url-encoded uncompressed EC point

Push payload:
  {title: "Nuvrail — Action required",
   body: "<description>",
   urgent: <bool>,
   operation_id: "<op_id>",
   url: "/"}
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from gateway.state_db import DB_PATH, get_db

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NUVRAIL_DATA_DIR", str(Path.home() / ".nuvrail")))
_VAPID_PRIVATE_PEM = _DATA_DIR / "vapid_private.pem"
_VAPID_PUBLIC_PEM = _DATA_DIR / "vapid_public.pem"
_VAPID_EMAIL = os.environ.get("NUVRAIL_VAPID_EMAIL", "").strip() or "mailto:admin@example.com"

# Cached after first load
_vapid_public_key_b64: str | None = None


def _ensure_vapid_keys() -> None:
    """Generate VAPID key pair on first run, storing to NUVRAIL_DATA_DIR."""
    if _VAPID_PRIVATE_PEM.exists() and _VAPID_PUBLIC_PEM.exists():
        return
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        v.save_key(_VAPID_PRIVATE_PEM)
        v.save_public_key(_VAPID_PUBLIC_PEM)
        logger.info("[push] Generated new VAPID key pair at %s", _DATA_DIR)
    except Exception as exc:
        logger.error("[push] Failed to generate VAPID keys: %s", exc)
        raise


def get_vapid_public_key() -> str:
    """Return the VAPID public key as a base64url-encoded string for the browser."""
    global _vapid_public_key_b64
    if _vapid_public_key_b64:
        return _vapid_public_key_b64
    _ensure_vapid_keys()
    try:
        import base64

        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from py_vapid import Vapid

        v = Vapid.from_file(_VAPID_PRIVATE_PEM)
        # applicationServerKey format expected by browser: base64url-encoded raw public key
        raw_point = v.public_key.public_bytes(
            encoding=Encoding.X962,
            format=PublicFormat.UncompressedPoint,
        )
        _vapid_public_key_b64 = base64.urlsafe_b64encode(raw_point).rstrip(b"=").decode()
        return _vapid_public_key_b64
    except Exception as exc:
        logger.error("[push] Failed to load VAPID public key: %s", exc)
        raise


async def _get_subscriptions(
    user_id: int | None, db_path: Path = DB_PATH
) -> list[dict]:
    """Fetch a user's push subscriptions from the DB.

    Returns [] when user_id is None (fail closed) — a notification must never
    be sent to subscriptions that can't be attributed to the owning user.
    """
    if user_id is None:
        return []
    async with get_db(db_path) as db, db.execute(
        "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def notify_staged(
    operation_id: str,
    description: str,
    is_urgent: bool = False,
    db_path: Path = DB_PATH,
    user_id: int | None = None,
) -> None:
    """
    Send a Web Push notification to the owning user's subscriptions only.

    Non-fatal: any errors are logged and swallowed so staging is never blocked.
    Runs fire-and-forget from create_operation(). With user_id=None (ownerless
    operation) no notification is sent — operation descriptions can contain
    sender/subject metadata and must never be pushed to another tenant.
    """
    try:
        _ensure_vapid_keys()
        subscriptions = await _get_subscriptions(user_id, db_path=db_path)
        if not subscriptions:
            logger.debug("[push] No subscriptions for user_id=%s, skipping notify", user_id)
            return

        payload = json.dumps({
            "title": "⚠️ Nuvrail — Action required" if is_urgent else "Nuvrail — Action required",
            "body": description,
            "urgent": is_urgent,
            "operation_id": operation_id,
            "url": "/",
        }).encode()

        import asyncio as _asyncio

        from pywebpush import WebPushException, webpush_async

        payload_str = payload.decode()

        async def _send_one(sub: dict) -> bool:
            # Give each subscriber a fresh claims dict: pywebpush mutates it
            # to add 'aud' (derived from the endpoint URL) and 'exp', so
            # sharing one dict across concurrent sends would corrupt 'aud'
            # for subscribers on different push services.
            claims = {"sub": _VAPID_EMAIL}
            try:
                await webpush_async(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=payload_str,
                    vapid_private_key=str(_VAPID_PRIVATE_PEM),
                    vapid_claims=claims,
                    ttl=3600,
                )
                return True
            except WebPushException as exc:
                logger.warning(
                    "[push] WebPushException for endpoint %s: %s",
                    sub["endpoint"][:40], exc,
                )
                return False
            except Exception as exc:
                logger.warning("[push] Unexpected push error: %s", exc)
                return False

        results = await _asyncio.gather(*[_send_one(s) for s in subscriptions])
        sent = sum(results)
        logger.info("[push] Notified %d/%d subscriber(s) for op=%s", sent, len(subscriptions), operation_id)

    except Exception as exc:
        logger.error("[push] notify_staged failed (non-fatal): %s", exc)
