"""
GET /api/v1/features — the current user's plan, features, and limits.

Backed by the entitlements seam (gateway.entitlements). In the public build this
always reports the open-core plan (everything available, no limits). In the
enterprise build the registered provider reports the user's real plan so the web
app can show/hide gated UI and render upgrade prompts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import get_current_user
from gateway.entitlements import entitlements

router = APIRouter()


@router.get("/features")
async def get_features(current_user: dict = Depends(get_current_user)) -> dict:
    return await entitlements().features(current_user)
