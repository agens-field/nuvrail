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
from pathlib import Path

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


def decrypt_credential(stored: str) -> str:
    """Decrypt a stored credential value.

    Accepts both encrypted envelopes and legacy plaintext values.
    Plaintext is returned as-is with a deprecation warning — re-encrypt
    by rewriting the credential via POST /agents or a migration script.
    """
    if not is_encrypted(stored):
        # Legacy plaintext — return as-is, log a deprecation notice
        logger.warning(
            "[credentials] Decrypting a plaintext credential — "
            "this field should be re-encrypted. See migration notes in gateway/credentials.py."
        )
        return stored

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        envelope = json.loads(stored)
        nonce = bytes.fromhex(envelope["iv"])
        ciphertext_and_tag = bytes.fromhex(envelope["ct"])
        key = get_master_key()
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext_and_tag, None)
        return plaintext.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Failed to decrypt credential: {exc}") from exc
