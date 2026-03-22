"""
Unit tests for gateway/credentials.py — AES-256-GCM credential encryption.

Tests:
  - Stored value is NOT the plaintext password
  - Decrypted value matches the original plaintext
  - Each encrypt call produces a distinct ciphertext (fresh nonce)
  - is_encrypted() correctly distinguishes envelope from plaintext
  - decrypt_credential() handles legacy plaintext gracefully (no exception)
  - Wrong master key → ValueError with a clear message
  - Envelope format is valid JSON with required fields (v, iv, ct)
"""
from __future__ import annotations

import json
import os
import secrets

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_key() -> bytes:
    """Generate a fresh random 32-byte key."""
    return secrets.token_bytes(32)


def _reset_cached_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the module-level key cache so each test starts clean."""
    import gateway.credentials as creds_mod
    monkeypatch.setattr(creds_mod, "_cached_master_key", None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEncryptDecrypt:
    def test_stored_value_is_not_plaintext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """encrypt_credential() must not store the password in recoverable plaintext."""
        _reset_cached_key(monkeypatch)
        key_hex = _make_key().hex()
        monkeypatch.setenv("NUVRAIL_MASTER_KEY", key_hex)

        from gateway.credentials import encrypt_credential

        plaintext = "super-secret-password-123"
        stored = encrypt_credential(plaintext)

        assert plaintext not in stored, (
            "Plaintext password found literally in stored value — encryption not applied"
        )

    def test_decrypt_returns_original_plaintext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """decrypt_credential(encrypt_credential(p)) == p for any plaintext."""
        _reset_cached_key(monkeypatch)
        key_hex = _make_key().hex()
        monkeypatch.setenv("NUVRAIL_MASTER_KEY", key_hex)

        from gateway.credentials import decrypt_credential, encrypt_credential

        for plaintext in [
            "correct-horse-battery-staple",
            "p@$$w0rd!",
            "a" * 128,
            "",  # empty string is a valid (if unusual) password
        ]:
            stored = encrypt_credential(plaintext)
            recovered = decrypt_credential(stored)
            assert recovered == plaintext, (
                f"Round-trip failed for {plaintext!r}: got {recovered!r}"
            )

    def test_each_call_produces_distinct_ciphertext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every encrypt_credential() call uses a fresh nonce — two encryptions of the
        same plaintext must produce different ciphertexts (IND-CPA property)."""
        _reset_cached_key(monkeypatch)
        key_hex = _make_key().hex()
        monkeypatch.setenv("NUVRAIL_MASTER_KEY", key_hex)

        from gateway.credentials import encrypt_credential

        plaintext = "same-password-every-time"
        results = {encrypt_credential(plaintext) for _ in range(10)}
        assert len(results) == 10, (
            "Expected 10 distinct ciphertexts for 10 encryptions of the same plaintext "
            "(nonce reuse detected)"
        )

    def test_envelope_is_valid_json_with_required_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stored envelope must be valid JSON with keys v, iv, ct."""
        _reset_cached_key(monkeypatch)
        monkeypatch.setenv("NUVRAIL_MASTER_KEY", _make_key().hex())

        from gateway.credentials import encrypt_credential

        stored = encrypt_credential("any-password")
        envelope = json.loads(stored)  # raises if not valid JSON
        assert envelope.get("v") == 1
        assert "iv" in envelope and len(envelope["iv"]) == 24  # 12-byte nonce → 24 hex chars
        assert "ct" in envelope and len(envelope["ct"]) > 0


class TestIsEncrypted:
    def test_encrypted_envelope_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_cached_key(monkeypatch)
        monkeypatch.setenv("NUVRAIL_MASTER_KEY", _make_key().hex())

        from gateway.credentials import encrypt_credential, is_encrypted

        stored = encrypt_credential("password")
        assert is_encrypted(stored) is True

    def test_plaintext_not_detected_as_encrypted(self) -> None:
        from gateway.credentials import is_encrypted

        for plaintext in ["password123", "secret", "{not-an-envelope}", ""]:
            assert is_encrypted(plaintext) is False, (
                f"is_encrypted() incorrectly returned True for plaintext: {plaintext!r}"
            )


class TestLegacyPlaintext:
    def test_decrypt_accepts_legacy_plaintext(self) -> None:
        """decrypt_credential() must return plaintext strings unchanged (backwards compat)."""
        from gateway.credentials import decrypt_credential

        legacy = "old-plaintext-password"
        result = decrypt_credential(legacy)
        assert result == legacy

    def test_decrypt_legacy_does_not_raise(self) -> None:
        """decrypt_credential() on plaintext must never raise — legacy rows must keep working."""
        from gateway.credentials import decrypt_credential

        try:
            decrypt_credential("any-old-password")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"decrypt_credential raised on plaintext: {exc}")


class TestWrongKey:
    def test_wrong_master_key_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Decrypting with a different master key must raise ValueError with a clear message."""
        import gateway.credentials as creds_mod

        # Encrypt with key A
        _reset_cached_key(monkeypatch)
        key_a = _make_key().hex()
        monkeypatch.setenv("NUVRAIL_MASTER_KEY", key_a)
        from gateway.credentials import encrypt_credential
        stored = encrypt_credential("secret-password")

        # Switch to key B and attempt to decrypt
        _reset_cached_key(monkeypatch)
        key_b = _make_key().hex()
        monkeypatch.setenv("NUVRAIL_MASTER_KEY", key_b)

        # Re-import decrypt to pick up the new key state
        from gateway.credentials import decrypt_credential

        with pytest.raises(ValueError, match="Failed to decrypt credential"):
            decrypt_credential(stored)
