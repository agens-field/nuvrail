"""
Web Push notification sender — Milestone MVP.

VAPID key pair is generated on first run and stored at:
  NUVRAIL_DATA_DIR/vapid_private.pem   (private key — keep secret)
  NUVRAIL_DATA_DIR/vapid_public.pem    (public key — sent to browser)

Environment:
  NUVRAIL_VAPID_EMAIL — contact email for VAPID header (required for push)
                        e.g. "mailto:admin@nuvrail.com"
                        Defaults to "mailto:admin@nuvrail.com" if not set.

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
from typing import Optional

from gateway.state_db import DB_PATH, get_db

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NUVRAIL_DATA_DIR", str(Path.home() / ".nuvrail")))
_VAPID_PRIVATE_PEM = _DATA_DIR / "vapid_private.pem"
_VAPID_PUBLIC_PEM = _DATA_DIR / "vapid_public.pem"
_VAPID_EMAIL = os.environ.get("NUVRAIL_VAPID_EMAIL", "mailto:admin@nuvrail.com")

# Cached after first load
_vapid_public_key_b64: Optional[str] = None


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
        from py_vapid import Vapid
        v = Vapid.from_file(_VAPID_PRIVATE_PEM)
        # applicationServerKey format expected by browser: base64url-encoded raw public key
        _vapid_public_key_b64 = v.public_key.public_bytes(
            encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
            format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint,
        )
        import base64
        _vapid_public_key_b64 = base64.urlsafe_b64encode(_vapid_public_key_b64).rstrip(b"=").decode()
        return _vapid_public_key_b64
    except Exception as exc:
        logger.error("[push] Failed to load VAPID public key: %s", exc)
        raise


async def _get_subscriptions(db_path: Path = DB_PATH) -> list[dict]:
    """Fetch all push subscriptions from the DB."""
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def notify_staged(
    operation_id: str,
    description: str,
    is_urgent: bool = False,
    db_path: Path = DB_PATH,
) -> None:
    """
    Send a Web Push notification to all registered subscriptions.

    Non-fatal: any errors are logged and swallowed so staging is never blocked.
    Runs fire-and-forget from create_operation().
    """
    try:
        _ensure_vapid_keys()
        subscriptions = await _get_subscriptions(db_path=db_path)
        if not subscriptions:
            logger.debug("[push] No subscriptions registered, skipping notify")
            return

        payload = json.dumps({
            "title": "⚠️ Nuvrail — Action required" if is_urgent else "Nuvrail — Action required",
            "body": description,
            "urgent": is_urgent,
            "operation_id": operation_id,
            "url": "/",
        }).encode()

        import asyncio as _asyncio  # noqa: PLC0415
        from pywebpush import webpush_async, WebPushException  # noqa: PLC0415

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
