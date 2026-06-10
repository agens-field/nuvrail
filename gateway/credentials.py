"""
Credential encryption for upstream email passwords — Milestone MVP (#37).

Upstream usernames and passwords stored in agent_credentials are encrypted
at rest using AES-256-GCM with a per-value random 96-bit nonce.

Master key:
  1. Read from NUVRAIL_MASTER_KEY env var (64 hex chars = 32 bytes), OR
  2. Load from NUVRAIL_DATA_DIR/master.key if it exists, OR
  3. Generate a new random 32-byte key, save to master.key, warn loudly.

Envelope format (stored as UTF-8 text in the DB column):
  {"v":1,"iv":"<12-byte nonce as hex>","ct":"<ciphertext+tag as hex>"}

Migration / backwards compatibility:
  - is_encrypted(value) returns True if the value looks like our JSON envelope
  - decrypt_credential() accepts both encrypted and plaintext values
  - Plaintext is returned as-is with a deprecation warning
  - Encrypted values are decrypted with AES-256-GCM

This design means existing plaintext rows continue to work until they are
next written (e.g. via a future /agents PUT endpoint), at which point they
will be re-encrypted automatically.

Usage:
  from gateway.credentials import encrypt_credential, decrypt_credential

  # On write (POST /agents):
  stored_password = encrypt_credential(body.upstream_password)

  # On read (proxy LOGIN):
  plaintext_password = decrypt_credential(row["upstream_password"])
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from pathlib import Path

from gateway.secret_store import (
    LOCAL_BACKEND,
    SecretContext,
    configured_backend,
    get_secret_store,
)

logger = logging.getLogger(__name__)

# expanduser() is required: if NUVRAIL_DATA_DIR is set to "~/.nuvrail" in a .env
# file, the shell does NOT expand ~ — Python must do it explicitly. Without this,
# Path("~/.nuvrail") creates a literal '~' directory in the cwd, which can end
# up committed to git if the cwd is the repo root.
_DATA_DIR = Path(os.environ.get("NUVRAIL_DATA_DIR", str(Path.home() / ".nuvrail"))).expanduser()
_MASTER_KEY_FILE = _DATA_DIR / "master.key"
_MASTER_KEY_ENV = "NUVRAIL_MASTER_KEY"

_cached_master_key: bytes | None = None


def _load_or_generate_master_key() -> bytes:
    """Load master key from env var or file; generate and save if neither exists."""
    global _cached_master_key
    if _cached_master_key is not None:
        return _cached_master_key

    # 1. Environment variable (highest priority — CI, Docker, fly.io secrets)
    env_val = os.environ.get(_MASTER_KEY_ENV, "").strip()
    if env_val:
        if len(env_val) != 64:
            raise ValueError(
                f"{_MASTER_KEY_ENV} must be 64 hex characters (32 bytes). Got {len(env_val)} chars."
            )
        try:
            key = bytes.fromhex(env_val)
            _cached_master_key = key
            return key
        except ValueError as exc:
            raise ValueError(f"{_MASTER_KEY_ENV} is not valid hex: {exc}") from exc

    # 2. Key file
    if _MASTER_KEY_FILE.exists():
        hex_key = _MASTER_KEY_FILE.read_text().strip()
        try:
            key = bytes.fromhex(hex_key)
            _cached_master_key = key
            return key
        except ValueError as exc:
            raise ValueError(f"Invalid master.key file at {_MASTER_KEY_FILE}: {exc}") from exc

    # 3. Generate new key — warn loudly
    logger.warning(
        "[credentials] No master key found. Generating a new one at %s. "
        "IMPORTANT: Back up this file. Losing it means existing encrypted "
        "credentials cannot be decrypted. "
        "Set %s env var for production deployments.",
        _MASTER_KEY_FILE,
        _MASTER_KEY_ENV,
    )
    key = secrets.token_bytes(32)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _MASTER_KEY_FILE.write_text(key.hex())
    _MASTER_KEY_FILE.chmod(0o600)
    _cached_master_key = key
    return key


def get_master_key() -> bytes:
    """Return the 32-byte master encryption key."""
    return _load_or_generate_master_key()


def encrypt_credential(plaintext: str) -> str:
    """Encrypt a plaintext credential string with AES-256-GCM.

    Returns a JSON envelope string safe to store in TEXT columns.
    Each call uses a fresh random 96-bit nonce.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = get_master_key()
    nonce = secrets.token_bytes(12)  # 96-bit nonce — GCM recommended size
    aesgcm = AESGCM(key)
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    envelope = json.dumps({
        "v": 1,
        "iv": nonce.hex(),
        "ct": ciphertext_and_tag.hex(),
    }, separators=(",", ":"))
    return envelope


def is_encrypted(value: str) -> bool:
    """Return True if the value looks like our JSON encryption envelope."""
    if not value.startswith('{"v":'):
        return False
    try:
        obj = json.loads(value)
        return obj.get("v") == 1 and "iv" in obj and "ct" in obj
    except (json.JSONDecodeError, AttributeError):
        return False


def _aes_decrypt_envelope(envelope: dict) -> str:
    """Decrypt a parsed v1 AES-256-GCM envelope dict to plaintext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        nonce = bytes.fromhex(envelope["iv"])
        ciphertext_and_tag = bytes.fromhex(envelope["ct"])
        key = get_master_key()
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext_and_tag, None)
        return plaintext.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Failed to decrypt credential: {exc}") from exc


def decrypt_credential(stored: str) -> str:
    """Decrypt a stored *local* credential value (AES-256-GCM or plaintext).

    Accepts both v1 encrypted envelopes and legacy plaintext values.
    Plaintext is returned as-is with a deprecation warning.

    NOTE: this handles only the local AES path. Code that may encounter
    external secret-store references (v2 envelopes) must use the async
    fetch_credential() instead.
    """
    if not is_encrypted(stored):
        # Legacy plaintext — return as-is, log a deprecation notice
        logger.warning(
            "[credentials] Decrypting a plaintext credential — "
            "this field should be re-encrypted. See migration notes in gateway/credentials.py."
        )
        return stored

    return _aes_decrypt_envelope(json.loads(stored))


# ---------------------------------------------------------------------------
# Backend-agnostic credential API (Pattern B)
#
# A stored credential is one of:
#   - legacy plaintext                       (pre-encryption rows)
#   - v1 AES-256-GCM envelope                {"v":1,"iv":…,"ct":…}
#   - v2 external secret reference           {"v":2,"backend":…,"ref":…}
#
# WRITES go to whatever NUVRAIL_SECRET_BACKEND selects (store_credential).
# READS dispatch on the stored value's own version/backend (fetch_credential),
# so existing v1/plaintext rows keep working regardless of the configured
# backend — which is what makes an online migration safe.
# ---------------------------------------------------------------------------
def _parse_envelope(stored: str) -> dict | None:
    """Return the parsed envelope dict, or None if `stored` is plaintext."""
    if not isinstance(stored, str) or not stored.startswith('{"v":'):
        return None
    try:
        obj = json.loads(stored)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "v" not in obj:
        return None
    return obj


def is_reference(stored: str) -> bool:
    """True if `stored` is a v2 external secret-store reference envelope."""
    env = _parse_envelope(stored)
    return bool(env and env.get("v") == 2 and env.get("backend") and "ref" in env)


async def store_credential(
    plaintext: str,
    *,
    field: str,
    owner_user_id: int | None = None,
) -> str:
    """Persist a secret to the configured backend and return what to store in the DB.

    - local backend → returns a v1 AES-256-GCM envelope (unchanged behaviour).
    - aws-sm / gcp-sm → writes the secret to the manager and returns a v2
      reference envelope; the plaintext never touches the DB.

    `field` and `owner_user_id` are used only for naming/tagging/auditing in
    the external store.
    """
    backend = configured_backend()
    if backend == LOCAL_BACKEND:
        return encrypt_credential(plaintext)

    store = get_secret_store(backend)
    if store is None:  # pragma: no cover - defensive
        raise ValueError(f"No secret store available for backend {backend!r}")
    ctx = SecretContext(
        field=field,
        secret_id=uuid.uuid4().hex,
        owner_user_id=owner_user_id,
    )
    ref = await store.put(plaintext, ctx=ctx)
    return json.dumps({"v": 2, "backend": backend, "ref": ref}, separators=(",", ":"))


async def fetch_credential(stored: str) -> str:
    """Resolve a stored credential to plaintext, whatever its form.

    Dispatches on the stored value itself (not the configured backend) so that
    plaintext, v1 AES envelopes, and v2 external references all resolve
    correctly during and after a migration.
    """
    env = _parse_envelope(stored)
    if env is None:
        logger.warning(
            "[credentials] Resolving a plaintext credential — "
            "this field should be migrated. See gateway/credentials.py."
        )
        return stored

    version = env.get("v")
    if version == 1:
        return _aes_decrypt_envelope(env)
    if version == 2:
        backend = env.get("backend")
        store = get_secret_store(backend)
        if store is None:
            raise ValueError(
                f"Credential references backend {backend!r} but no store is "
                "configured for it (is NUVRAIL_SECRET_BACKEND / its env set?)"
            )
        return await store.get(env["ref"])
    raise ValueError(f"Unknown credential envelope version: {version!r}")


async def delete_credential(stored: str | None) -> None:
    """Best-effort delete of the external secret behind a stored credential.

    No-op for plaintext and v1 AES envelopes (nothing external to delete) and
    for None. Used to clean up orphaned secrets when a DB write fails, and by
    purge/GC tooling. Soft-revoking an agent does NOT call this — secrets are
    retained so the agent can be un-revoked.
    """
    if not stored:
        return
    env = _parse_envelope(stored)
    if not env or env.get("v") != 2:
        return
    store = get_secret_store(env.get("backend"))
    if store is not None:
        await store.delete(env["ref"])


# The four agent_credentials columns that may hold an upstream secret (a v2
# reference into the external secret store in hosted mode, or a v1 AES envelope
# / plaintext otherwise). delete_credential() is a safe no-op for the non-v2
# forms, so it is correct to call it across all backends.
UPSTREAM_SECRET_FIELDS = (
    "upstream_password",
    "oauth2_refresh_token",
    "oauth2_access_token",
    "oauth2_client_secret",
)


async def purge_agent_upstream_secrets(
    agent_row: dict,
    *,
    revoke_oauth2: bool = True,
) -> None:
    """Best-effort teardown of one agent's upstream secrets on deletion (#72).

    Both deletion paths (self-serve account deletion and the retention
    hard-purge) MUST call this BEFORE nulling or deleting the credential
    columns/row — otherwise the reference is destroyed first and the actual
    secret is orphaned in the external secret store (Secret Manager) forever,
    leaving Nuvrail with live access to a deleted user's mailbox.

    For each stored secret field on the row this:
      1. (OAuth2 refresh token, when ``revoke_oauth2`` and a Google provider)
         revokes the grant at the provider so live access dies immediately,
         not just whenever Google eventually expires the token; then
      2. deletes the secret from the external store via delete_credential().

    Every step is best-effort and independently guarded: a failure on one
    field is logged and the rest still run, so deletion never blocks on an
    external call. The retention purge is the backstop for anything that
    fails here.

    Order matters (#72):
        purge_agent_upstream_secrets(row)
              │
              ├─ revoke_google_refresh_token(refresh)   (kills live access)
              └─ delete_credential(ref) × every field    (frees the store)
                    │
            ... only THEN does the caller NULL / DELETE the columns/row
    """
    # 1. Revoke the OAuth2 grant at the provider first (kills live access).
    if revoke_oauth2 and agent_row.get("oauth2_provider") == "google":
        stored_refresh = agent_row.get("oauth2_refresh_token")
        if stored_refresh:
            try:
                from gateway.oauth2_tokens import (  # noqa: PLC0415
                    revoke_google_refresh_token,
                )

                plaintext_refresh = await fetch_credential(stored_refresh)
                await revoke_google_refresh_token(plaintext_refresh)
            except Exception:  # noqa: BLE001 - revoke is best-effort
                logger.warning(
                    "[credentials] Failed to revoke OAuth2 grant for agent %r "
                    "on deletion; secret-store delete still proceeds",
                    agent_row.get("id"),
                )

    # 2. Delete each stored secret from the external store.
    for field in UPSTREAM_SECRET_FIELDS:
        stored = agent_row.get(field)
        if not stored:
            continue
        try:
            await delete_credential(stored)
        except Exception:  # noqa: BLE001 - per-field best-effort
            logger.warning(
                "[credentials] Failed to delete upstream secret %r for agent %r "
                "on deletion; retention purge is the backstop",
                field,
                agent_row.get("id"),
            )
