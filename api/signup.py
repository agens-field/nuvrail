"""Human-account signup gating (NUVRAIL_SIGNUP_MODE).

One deployment-wide policy controls how human accounts may be created:

    closed  (default)  no public /auth/register; admin CLI provisions users
    invite             /auth/register requires a valid single-use invite code
    open               public self-registration, gated behind an explicit ack

The resolution is centralized here (imported by both the app factory, which
logs the effective mode at startup, and the register route, which enforces it)
so there is exactly ONE fail-closed decision and no chance of the startup log
and the enforced behavior drifting apart.

Fail-closed design (see resolve_signup_mode):

    NUVRAIL_SIGNUP_MODE    ack set?         -> effective mode
    -------------------    --------         ----------------
    unset / "" / garbage   (n/a)            -> closed        (safe default)
    "closed"               (n/a)            -> closed
    "invite"               (n/a)            -> invite
    "open"                 yes (truthy)     -> open
    "open"                 no               -> RuntimeError at startup (loud)

The asymmetry is intentional: an *unknown* mode degrades to closed (a private
box must not silently open), but a *known* open mode without its acknowledg
refuses to boot (an operator who typed "open" clearly meant it, so prove it
rather than silently downgrade and confuse them).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("nuvrail.signup")

VALID_SIGNUP_MODES = ("closed", "invite", "open")

_ACK_TRUTHY = ("1", "true", "yes", "on")


def resolve_signup_mode(mode_raw: str, ack_raw: str) -> str:
    """Resolve the effective signup mode, failing closed. See module docstring."""
    mode = mode_raw.strip().lower()
    if mode not in VALID_SIGNUP_MODES:
        if mode:
            logger.warning(
                "NUVRAIL_SIGNUP_MODE=%r is not one of %s — failing closed "
                "(no public registration). Set it explicitly to silence this.",
                mode_raw,
                VALID_SIGNUP_MODES,
            )
        return "closed"
    if mode == "open":
        if ack_raw.strip().lower() not in _ACK_TRUTHY:
            raise RuntimeError(
                "NUVRAIL_SIGNUP_MODE=open requires NUVRAIL_ALLOW_OPEN_SIGNUP to "
                "be set to a truthy value (1/true/yes/on). Open signup lets "
                "anyone create an account on this deployment — refusing to "
                "start until that is explicitly acknowledged. Use "
                "NUVRAIL_SIGNUP_MODE=invite or =closed for a private instance."
            )
        logger.warning(
            "NUVRAIL_SIGNUP_MODE=open with NUVRAIL_ALLOW_OPEN_SIGNUP acknowledged "
            "— anyone can self-register on this deployment."
        )
    return mode


def current_signup_mode() -> str:
    """Resolve the signup mode from the live environment (request-time).

    Read at request time (not import time) so the effective policy always
    reflects the current environment and so tests can monkeypatch env vars
    without reimporting the app. The startup path logs the same resolution once
    via resolve_signup_mode() directly.
    """
    return resolve_signup_mode(
        os.environ.get("NUVRAIL_SIGNUP_MODE", ""),
        os.environ.get("NUVRAIL_ALLOW_OPEN_SIGNUP", ""),
    )
