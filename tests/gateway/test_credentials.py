"""
Unit tests for gateway/credentials.py — AES-256-GCM credential encryption.

Three properties under test:
  1. Stored value is NOT the plaintext password (encrypt_credential is not a no-op)
  2. decrypt_credential(encrypt_credential(p)) == p  (round-trip fidelity)
  3. Wrong master key → ValueError with a clear message (no silent corruption)

Plus:
  4. is_encrypted() correctly distinguishes envelopes from plaintext strings
  5. decrypt_credential() accepts legacy plaintext (backwards-compat migration path)
  6. Each encrypt_credential() call produces a unique ciphertext (nonce randomness)
  7. Tampered ciphertext raises ValueError (GCM authentication tag check)
"""
from __future__ import annotations

import json
import os

import pytest

# ---------------------------------------------------------------------------
# Fixture: isolate master key per test so tests don't interfere with each
# other or with any real master.key file on disk.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_master_key(tmp_path, monkeypatch):
    """
    Each test gets its own fresh master key, injected via env var.
    Monkeypatches NUVRAIL_MASTER_KEY and clears the in-process key cache
    so tests are fully isolated from each other and from the filesystem.
    """
    import gateway.credentials as creds_module

    key_hex = os.urandom(32).hex()
    monkeypatch.setenv("NUVRAIL_MASTER_KEY", key_hex)

    # Clear the module-level cache so the env var is re-read for each test.
    creds_module._cached_master_key = None
    yield key_hex
    creds_module._cached_master_key = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stored_value_is_not_plaintext():
    """Encrypted value must not equal the original password."""
    from gateway.credentials import encrypt_credential

    password = "super-secret-upstream-password"
    stored = encrypt_credential(password)

    assert stored != password, (
        "encrypt_credential returned the plaintext password unchanged — encryption is broken"
    )


def test_round_trip_decrypts_to_original():
    """decrypt_credential(encrypt_credential(p)) must equal p for any input."""
    from gateway.credentials import decrypt_credential, encrypt_credential

    for password in [
        "simple",
        "p@$$w0rd!",
        "unicode: こんにちは",
        "a" * 256,              # long password
        "",                     # empty string (edge case)
        " leading space",
        "trailing space ",
    ]:
        stored = encrypt_credential(password)
        recovered = decrypt_credential(stored)
        assert recovered == password, (
            f"Round-trip failed for {password!r}: got {recovered!r}"
        )


def test_wrong_master_key_raises_value_error(monkeypatch):
    """Decrypting with a different master key must raise ValueError, not silently corrupt."""
    import gateway.credentials as creds_module
    from gateway.credentials import decrypt_credential, encrypt_credential

    password = "correct-horse-battery-staple"
    stored = encrypt_credential(password)

    # Swap to a different key
    different_key = os.urandom(32).hex()
    monkeypatch.setenv("NUVRAIL_MASTER_KEY", different_key)
    creds_module._cached_master_key = None  # force re-read

    with pytest.raises(ValueError, match="Failed to decrypt credential"):
        decrypt_credential(stored)


def test_is_encrypted_detects_envelope():
    """is_encrypted() must return True for our JSON envelopes, False for plaintext."""
    from gateway.credentials import encrypt_credential, is_encrypted

    plaintext = "plain-password"
    assert not is_encrypted(plaintext), "is_encrypted returned True for plaintext"
    assert not is_encrypted(""), "is_encrypted returned True for empty string"
    assert not is_encrypted('{"not": "our-format"}'), (
        "is_encrypted returned True for unrelated JSON"
    )

    envelope = encrypt_credential(plaintext)
    assert is_encrypted(envelope), (
        f"is_encrypted returned False for a valid envelope: {envelope!r}"
    )


def test_decrypt_accepts_legacy_plaintext():
    """decrypt_credential must return plaintext strings unchanged (migration path)."""
    from gateway.credentials import decrypt_credential

    # Existing rows written before encryption was added — must not break
    for legacy in ["old-password", "hunter2", "correct-horse-battery-staple"]:
        result = decrypt_credential(legacy)
        assert result == legacy, (
            f"decrypt_credential modified legacy plaintext {legacy!r} → {result!r}"
        )


def test_each_encryption_produces_unique_ciphertext():
    """Two calls with the same password must produce different stored values (nonce randomness)."""
    from gateway.credentials import encrypt_credential

    password = "same-password-twice"
    stored1 = encrypt_credential(password)
    stored2 = encrypt_credential(password)

    assert stored1 != stored2, (
        "Two encryptions of the same password produced identical output — nonce is not random"
    )

    # Both must still decrypt to the original (sanity check)
    from gateway.credentials import decrypt_credential
    assert decrypt_credential(stored1) == password
    assert decrypt_credential(stored2) == password


def test_tampered_ciphertext_raises_value_error():
    """GCM authentication tag must reject any modification to the ciphertext."""
    from gateway.credentials import decrypt_credential, encrypt_credential

    stored = encrypt_credential("tamper-me")
    envelope = json.loads(stored)

    # Flip the last byte of the ciphertext hex string
    ct_bytes = bytearray(bytes.fromhex(envelope["ct"]))
    ct_bytes[-1] ^= 0xFF  # flip all bits in last byte
    envelope["ct"] = ct_bytes.hex()
    tampered = json.dumps(envelope, separators=(",", ":"))

    with pytest.raises(ValueError, match="Failed to decrypt credential"):
        decrypt_credential(tampered)


def test_envelope_schema():
    """Envelope must be valid JSON with the expected v/iv/ct fields."""
    from gateway.credentials import encrypt_credential

    stored = encrypt_credential("schema-check")
    envelope = json.loads(stored)  # must not raise

    assert envelope.get("v") == 1, f"Expected v=1, got: {envelope.get('v')!r}"
    assert "iv" in envelope, "Missing 'iv' field in envelope"
    assert "ct" in envelope, "Missing 'ct' field in envelope"

    # iv must be 12 bytes (96 bits) hex-encoded = 24 hex chars
    assert len(envelope["iv"]) == 24, (
        f"Expected 24-char iv (12 bytes), got {len(envelope['iv'])}"
    )
