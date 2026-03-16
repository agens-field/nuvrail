"""
/api/v1/push endpoints.

POST /push/subscribe   — register a browser PushSubscription
GET  /push/vapid-key   — return public VAPID key for client

Sub-milestone: 2.2
"""
from fastapi import APIRouter

router = APIRouter()

# TODO: implement in sub-milestone 2.2
