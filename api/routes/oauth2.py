"""
OAuth2 web flow endpoints — XOAUTH2 in-browser setup (Gmail + Outlook/O365).

Provider-parameterized. The same three-legged flow serves every provider in
_PROVIDERS; only the endpoints, scopes, env var names, and upstream IMAP/SMTP
hosts differ. Google and Microsoft (Azure AD) are wired today.

Route shape (``{provider}`` ∈ {"google", "microsoft"}):
  1. GET /api/v1/oauth2/{provider}/start     (authenticated)
       Generates state nonce, returns {auth_url, state}.
       Browser navigates to auth_url (provider consent).

  2. GET /api/v1/oauth2/{provider}/callback  (unauthenticated — called by provider)
       Exchanges code → refresh_token + access_token.
       Creates agent_credentials row (with the provider's upstream hosts).
       Redirects browser to /#/oauth2/callback?state=<state>.

  3. GET /api/v1/oauth2/{provider}/result    (authenticated)
       Frontend polls for completed result (one-time, clears on read).

Provider dispatch
-----------------

    /oauth2/{provider}/start ---.
    /oauth2/{provider}/callback  >--- _provider_or_404(provider) --> ProviderConfig
    /oauth2/{provider}/result --'          |                              |
                                           |  auth/token endpoints, scope, |
                                           |  env var names, imap/smtp host,|
                                           |  email-from-response extractor |
                                           v                              v
                                    503 if unknown              build auth_url /
                                                                exchange code /
                                                                INSERT credentials

State lifecycle (shared across providers — state nonce is globally unique):
  _pending[state] = {user_id, label, provider, created_at}   (max _STATE_TTL s)
  _results[state] = {agent_username, agent_token, label, upstream_user}
                  | {error: str}                    (cleared on first read)

Phase 2 note: _pending / _results are in-process dicts (single worker).
Phase 3: migrate to DB if/when multi-worker deployment is needed.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse

from api.auth import get_current_user, hash_agent_token
from api.routes.operations import get_db_path
from gateway.credentials import delete_credential, store_credential
from gateway.state_db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

_GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — endpoint URL, not a secret
# openid + email: lets us extract upstream_user from the id_token payload
# without an extra userinfo round-trip.
_SCOPE = "https://mail.google.com/ openid email"
_STATE_TTL = 300  # seconds

# Microsoft identity platform (Azure AD v2.0). The tenant segment defaults to
# "common" (multi-tenant + personal Microsoft accounts) so consumer Outlook.com
# and most Office 365 tenants work out of the box; a single-tenant deployment
# overrides it with MICROSOFT_TENANT.
_MS_TENANT = os.environ.get("MICROSOFT_TENANT", "common").strip() or "common"
_MICROSOFT_AUTH_ENDPOINT = (
    f"https://login.microsoftonline.com/{_MS_TENANT}/oauth2/v2.0/authorize"
)
_MICROSOFT_TOKEN_ENDPOINT = (
    f"https://login.microsoftonline.com/{_MS_TENANT}/oauth2/v2.0/token"
)
# offline_access -> refresh token; IMAP.AccessAsUser.All + SMTP.Send -> mailbox
# access; openid email -> account address. Microsoft echoes these on refresh too.
_MICROSOFT_SCOPE = (
    "offline_access openid email "
    "https://outlook.office.com/IMAP.AccessAsUser.All "
    "https://outlook.office.com/SMTP.Send"
)

# ---------------------------------------------------------------------------
# Module-level state (process-scoped, single-worker)
# ---------------------------------------------------------------------------

_pending: dict[str, dict] = {}  # state → {user_id, label, provider, created_at}
_results: dict[str, dict] = {}  # state → result or error dict


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
#
# Everything provider-specific about the flow lives in one ProviderConfig row.
# The route handlers are provider-agnostic: they resolve a config by the
# ``{provider}`` path segment and read endpoints/scopes/hosts off it. Adding a
# provider = adding a row here (+ a token-refresh branch in
# gateway/oauth2_tokens.py), not forking the flow.


@dataclass(frozen=True)
class ProviderConfig:
    """Static wiring for one OAuth2 provider's XOAUTH2 setup flow.

    Fields
    ------
    key : str
        The ``oauth2_provider`` value stored on agent_credentials and the
        token-refresh dispatch key ("google" / "microsoft").
    auth_endpoint / token_endpoint : str
        Provider authorization + token URLs.
    scope : str
        Space-delimited scopes requested at consent time.
    client_id_env / client_secret_env : str
        Names of the env vars holding this provider's OAuth2 client creds.
    redirect_env / redirect_default : str
        Env var name + fallback for the callback URL registered with the
        provider (must exactly match one registered redirect URI).
    imap_host / smtp_host / imap_port / smtp_port :
        Upstream mail hosts written onto the agent_credentials row.
    default_label : str
        Label used when the caller doesn't pass one.
    email_extractor : Callable[[dict], str]
        Pulls the account email out of the token-exchange response JSON. Google
        carries it in the id_token JWT; Microsoft does too, but under a
        different claim precedence — see the extractor functions below.
    extra_auth_params : dict[str, str]
        Provider-specific query params appended to the auth URL.
    """

    key: str
    auth_endpoint: str
    token_endpoint: str
    scope: str
    client_id_env: str
    client_secret_env: str
    redirect_env: str
    redirect_default: str
    imap_host: str
    smtp_host: str
    imap_port: int
    smtp_port: int
    default_label: str
    email_extractor: Callable[[dict], str]
    extra_auth_params: dict[str, str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prune_expired() -> None:
    """Remove _pending entries older than _STATE_TTL. Called on each /start."""
    cutoff = time.time() - _STATE_TTL
    expired = [k for k, v in _pending.items() if v["created_at"] < cutoff]
    for k in expired:
        del _pending[k]


def _get_oauth2_env(cfg: ProviderConfig) -> tuple[str, str] | None:
    """Return (client_id, client_secret) for *cfg* from env, or None if unset."""
    client_id = os.environ.get(cfg.client_id_env, "").strip()
    client_secret = os.environ.get(cfg.client_secret_env, "").strip()
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def _not_configured_response(cfg: ProviderConfig) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "oauth2_not_configured",
            "detail": (
                f"{cfg.client_id_env} / {cfg.client_secret_env} "
                "not set on this server."
            ),
        },
    )


def _provider_or_404(provider: str) -> ProviderConfig | None:
    """Resolve a provider key to its config, or None for an unknown provider."""
    return _PROVIDERS.get(provider)


def _unknown_provider_response(provider: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": "oauth2_unknown_provider",
            "detail": (
                f"Unknown OAuth2 provider {provider!r}. "
                f"Supported: {', '.join(sorted(_PROVIDERS))}."
            ),
        },
    )


def _get_redirect_uri(cfg: ProviderConfig) -> str:
    return os.environ.get(cfg.redirect_env, cfg.redirect_default)


def _urllib_post(url: str, payload: dict) -> tuple[int, str]:
    """Synchronous form POST via urllib (run in executor from async context)."""
    # Fail closed on any non-https scheme: the only callers pass fixed provider
    # token endpoints, so a file:/custom scheme here would be a bug or tampering
    # (this is the S310 concern, enforced rather than suppressed).
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https token endpoint: {url!r}")
    data = urllib.parse.urlencode(payload).encode("ascii")
    req = urllib.request.Request(  # noqa: S310 — https scheme enforced above; fixed provider endpoints
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — https enforced above
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        return exc.code, exc.read().decode("utf-8", errors="replace")


async def _exchange_code(
    cfg: ProviderConfig,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> tuple[str, str, str]:
    """Exchange an authorization code for (refresh_token, access_token, id_token).

    Provider-agnostic: posts to ``cfg.token_endpoint`` with ``cfg.scope``
    echoed (Microsoft requires the scope on the code grant; Google tolerates
    it). Raises ValueError if the exchange fails or refresh_token is absent.
    """
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "scope": cfg.scope,
    }
    status_code, body = await asyncio.get_event_loop().run_in_executor(
        None, _urllib_post, cfg.token_endpoint, payload
    )
    if status_code != 200:
        try:
            err = json.loads(body)
        except json.JSONDecodeError:
            raise ValueError(
                f"{cfg.key} token exchange failed: HTTP {status_code}"
            ) from None
        raise ValueError(
            f"{cfg.key} token exchange failed: "
            f"{err.get('error')} — {err.get('error_description')}"
        )

    data = json.loads(body)
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            f"{cfg.key} did not return a refresh_token. "
            "Ensure the OAuth2 client requests offline access and forces consent."
        )
    access_token = data.get("access_token", "")
    id_token = data.get("id_token", "")
    return refresh_token, access_token, id_token


def _decode_jwt_payload(jwt: str) -> dict:
    """Base64-decode the payload segment of a JWT (no signature verification).

    We only read non-authoritative display claims (the account email) from the
    id_token the provider just minted for us over TLS in the code exchange — we
    are not authenticating the user, so signature verification is unnecessary
    here. Returns {} on any malformed input.
    """
    if not jwt:
        return {}
    try:
        parts = jwt.split(".")
        if len(parts) != 3:
            return {}
        padding = "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.b64decode(parts[1] + padding).decode("utf-8"))
    except Exception:
        return {}


def _google_email_from_response(data: dict) -> str:
    """Google carries the account email in the id_token's ``email`` claim."""
    return _decode_jwt_payload(data.get("id_token", "")).get("email", "")


def _microsoft_email_from_response(data: dict) -> str:
    """Extract the account email from a Microsoft token response.

    Azure AD's id_token puts the address in ``email`` for accounts that expose
    it, but consumer/work accounts often only populate ``preferred_username``
    (which is the UPN / sign-in address — the correct IMAP/SMTP login). Prefer
    ``email`` when present, fall back to ``preferred_username``, then ``upn``.
    """
    claims = _decode_jwt_payload(data.get("id_token", ""))
    return (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
        or ""
    )


_PROVIDERS: dict[str, ProviderConfig] = {
    "google": ProviderConfig(
        key="google",
        auth_endpoint=_GOOGLE_AUTH_ENDPOINT,
        token_endpoint=_GOOGLE_TOKEN_ENDPOINT,
        scope=_SCOPE,
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",  # noqa: S106 — env var NAME, not the secret value
        redirect_env="GOOGLE_REDIRECT_URI",
        redirect_default="http://localhost:8080/api/v1/oauth2/google/callback",
        imap_host="imap.gmail.com",
        smtp_host="smtp.gmail.com",
        imap_port=993,
        smtp_port=587,
        default_label="gmail",
        email_extractor=_google_email_from_response,
        # access_type=offline + prompt=consent is how Google returns a refresh
        # token on the code grant.
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
    ),
    "microsoft": ProviderConfig(
        key="microsoft",
        auth_endpoint=_MICROSOFT_AUTH_ENDPOINT,
        token_endpoint=_MICROSOFT_TOKEN_ENDPOINT,
        scope=_MICROSOFT_SCOPE,
        client_id_env="MICROSOFT_CLIENT_ID",
        client_secret_env="MICROSOFT_CLIENT_SECRET",  # noqa: S106 — env var NAME, not the secret value
        redirect_env="MICROSOFT_REDIRECT_URI",
        redirect_default="http://localhost:8080/api/v1/oauth2/microsoft/callback",
        imap_host="outlook.office365.com",
        smtp_host="smtp.office365.com",
        imap_port=993,
        smtp_port=587,
        default_label="outlook",
        email_extractor=_microsoft_email_from_response,
        # offline_access (in scope) yields the refresh token; prompt=consent
        # forces the consent screen so the refresh token is reliably returned.
        extra_auth_params={"prompt": "consent"},
    ),
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/oauth2/{provider}/start")
async def oauth2_start(
    provider: str,
    label: str | None = None,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Initiate an OAuth2 XOAUTH2 setup flow for *provider* (google|microsoft).

    Returns {auth_url, state}. The caller should redirect the browser to
    auth_url. On completion the provider will redirect to /callback.
    """
    cfg = _provider_or_404(provider)
    if cfg is None:
        return _unknown_provider_response(provider)

    creds = _get_oauth2_env(cfg)
    if creds is None:
        return _not_configured_response(cfg)
    client_id, _ = creds

    _prune_expired()
    state = secrets.token_hex(32)
    _pending[state] = {
        "user_id": current_user["id"],
        "label": label or cfg.default_label,
        "provider": cfg.key,
        "created_at": time.time(),
    }

    params = {
        "client_id": client_id,
        "redirect_uri": _get_redirect_uri(cfg),
        "response_type": "code",
        "scope": cfg.scope,
        "state": state,
        **cfg.extra_auth_params,
    }
    auth_url = cfg.auth_endpoint + "?" + urllib.parse.urlencode(params)
    return JSONResponse({"auth_url": auth_url, "state": state})


@router.get("/oauth2/{provider}/callback")
async def oauth2_callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db_path: Path = Depends(get_db_path),
) -> RedirectResponse:
    """OAuth2 redirect target (unauthenticated — called by the provider).

    On success: creates agent_credentials row (with the provider's upstream
    hosts), redirects to SPA callback route. On any failure: stores error in
    _results, still redirects so the SPA can show a useful message rather than
    a blank server error page.

    The provider is resolved from BOTH the path segment and the pending state
    entry; if they disagree (a state minted for a different provider) we treat
    it as an invalid state rather than crossing wires.
    """
    frontend_callback = "/#/oauth2/callback"

    cfg = _provider_or_404(provider)
    if cfg is None:
        return _unknown_provider_response(provider)

    # Unknown or expired state — redirect with error flag so SPA can react.
    if not state or state not in _pending:
        qs = urllib.parse.urlencode({"state": state or "", "error": "invalid_state"})
        return RedirectResponse(f"{frontend_callback}?{qs}", status_code=302)

    pending = _pending.pop(state)

    # State was minted for a different provider's flow — refuse to cross wires.
    if pending.get("provider", "google") != cfg.key:
        qs = urllib.parse.urlencode({"state": state, "error": "invalid_state"})
        return RedirectResponse(f"{frontend_callback}?{qs}", status_code=302)

    # User denied access or the provider returned an error before we got a code.
    if error or not code:
        _results[state] = {"error": error or "no_code", "user_id": pending["user_id"]}
        return RedirectResponse(
            f"{frontend_callback}?state={urllib.parse.quote(state)}",
            status_code=302,
        )

    # Attempt code exchange + agent creation.
    creds = _get_oauth2_env(cfg)
    if creds is None:
        _results[state] = {"error": "oauth2_not_configured", "user_id": pending["user_id"]}
        return RedirectResponse(
            f"{frontend_callback}?state={urllib.parse.quote(state)}",
            status_code=302,
        )
    client_id, client_secret = creds

    created_refs: list[str] = []
    try:
        refresh_token, access_token, id_token = await _exchange_code(
            cfg, code, client_id, client_secret, _get_redirect_uri(cfg)
        )
        upstream_user = cfg.email_extractor(
            {"id_token": id_token, "access_token": access_token}
        )

        now = int(time.time())
        agent_username = "nuvrail_" + secrets.token_hex(8)
        agent_token_plain = secrets.token_urlsafe(32)
        hashed = hash_agent_token(agent_token_plain)

        uid = pending["user_id"]
        # Store secrets externally before touching the DB.
        stored_client_secret = await store_credential(
            client_secret, field="oauth2_client_secret", owner_user_id=uid
        )
        stored_refresh_token = await store_credential(
            refresh_token, field="oauth2_refresh_token", owner_user_id=uid
        )
        created_refs = [stored_client_secret, stored_refresh_token]

        async with get_db(db_path) as db:
            await db.execute(
                """
                INSERT INTO agent_credentials
                    (user_id, label, agent_username, hashed_token,
                     upstream_host, upstream_smtp_host,
                     upstream_imap_port, upstream_smtp_port,
                     upstream_user, upstream_password,
                     oauth2_provider, oauth2_client_id,
                     oauth2_client_secret, oauth2_refresh_token,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    pending["label"],
                    agent_username,
                    hashed,
                    cfg.imap_host,
                    cfg.smtp_host,
                    cfg.imap_port,
                    cfg.smtp_port,
                    upstream_user,
                    None,
                    cfg.key,
                    client_id,
                    stored_client_secret,
                    stored_refresh_token,
                    now,
                ),
            )
            await db.commit()

        _results[state] = {
            "user_id": pending["user_id"],
            "agent_username": agent_username,
            "agent_token": agent_token_plain,
            "label": pending["label"],
            "upstream_user": upstream_user,
        }

    except Exception as exc:
        # Clean up any secrets created before the failure to avoid orphans.
        for ref in created_refs:
            try:
                await delete_credential(ref)
            except Exception:
                logger.warning("[oauth2] Failed to clean up orphaned secret after callback error")
        _results[state] = {"error": str(exc), "user_id": pending["user_id"]}

    return RedirectResponse(
        f"{frontend_callback}?state={urllib.parse.quote(state)}",
        status_code=302,
    )


@router.get("/oauth2/{provider}/result")
async def oauth2_result(
    provider: str,
    state: str,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Poll for OAuth2 flow result. One-time: cleared from memory on first read.

    Returns the agent credentials on success, or 404 if the state is unknown,
    expired, already consumed, or owned by a different user.

    The result carries the user_id of the account that initiated the flow.
    A caller who is not that user gets a 404 (indistinguishable from an
    unknown state) and the result is NOT consumed — so a leaked state value
    cannot be used to steal another user's freshly minted agent token.

    ``provider`` is validated for a clear error on a bogus path, but the result
    map is keyed by the globally-unique state nonce, so lookups don't branch on
    it.
    """
    if _provider_or_404(provider) is None:
        return _unknown_provider_response(provider)
    result = _results.get(state)
    if result is None or result.get("user_id") != current_user["id"]:
        return JSONResponse(
            status_code=404,
            content={"error": "oauth2_state_not_found"},
        )
    # Ownership confirmed — consume the one-time result and drop the internal
    # user_id before returning it to the client.
    _results.pop(state, None)
    result = {k: v for k, v in result.items() if k != "user_id"}
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(result)
