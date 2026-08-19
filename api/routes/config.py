"""
GET /api/v1/config — unauthenticated public deployment config for the web app.

The React app needs to know a few deployment-wide facts *before* a user is
authenticated so it can render the right first-run experience. Today that is
just the signup mode, so the web app can hide (not merely reject) the account-
creation UI on a closed/invite deployment.

    signup_mode  closed | invite | open   (see api.signup.current_signup_mode)

Security note — this endpoint is UX plumbing, not an access control.
``signup_mode`` is not a secret (an operator sets it; the register route
already reveals it via its 403), and the server-side 403 in /auth/register
remains the *only* source of truth for whether a registration is allowed. A
client that lies about the mode to itself gains nothing: submitting anyway
still hits the fail-closed enforcement in api.signup / /auth/register. Keep it
that way — never move a security decision onto this response.

Resolution reuses current_signup_mode(), so this endpoint and the enforced
policy can never drift: they read the same fail-closed function against the
same live environment.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.signup import current_signup_mode

router = APIRouter(tags=["config"])


@router.get("/config")
async def get_config() -> dict:
    """Public, unauthenticated deployment config for the web client.

    Fail-closed: current_signup_mode() degrades any unknown/unset mode to
    "closed", so a misconfigured box advertises the *safe* state (no signup UI)
    rather than a permissive one.
    """
    return {"signup_mode": current_signup_mode()}
