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
  8. A generated master.key is never group/other-readable, even if the process
     dies mid-generation, and a new data dir is 0700 (nuvrail-internal#43 F1)
  9. First-boot race: a process that loses O_EXCL uses the winner's key
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import threading
import time
from pathlib import Path

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


# ---------------------------------------------------------------------------
# master.key generation (nuvrail-internal#43 F1)
#
#   no env var, no file ──► _create_master_key_file()
#                           ├─ mkdir data dir 0700
#                           ├─ os.open(O_CREAT|O_EXCL, 0600) ──► write key
#                           └─ FileExistsError ──► read the winner's key
#                                                  (wait up to _KEY_RACE_WAIT_S
#                                                   for 64 hex chars)
# ---------------------------------------------------------------------------

_posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


@pytest.fixture
def key_file_env(tmp_path, monkeypatch):
    """No env key; data dir is a fresh subdir of tmp_path; umask 000 (worst case)."""
    import gateway.credentials as creds_module

    monkeypatch.delenv("NUVRAIL_MASTER_KEY", raising=False)
    data_dir = tmp_path / "data"
    monkeypatch.setattr(creds_module, "_DATA_DIR", data_dir)
    monkeypatch.setattr(creds_module, "_MASTER_KEY_FILE", data_dir / "master.key")
    creds_module._cached_master_key = None
    old_umask = os.umask(0)
    try:
        yield creds_module, data_dir
    finally:
        os.umask(old_umask)
        creds_module._cached_master_key = None


@_posix_only
def test_generated_master_key_is_0600_and_dir_0700(key_file_env):
    creds_module, data_dir = key_file_env

    key = creds_module.get_master_key()

    key_file = data_dir / "master.key"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert key_file.read_text(encoding="utf-8") == key.hex()


@_posix_only
def test_master_key_never_world_readable_if_process_dies_mid_generation(
    key_file_env, monkeypatch
):
    """Old code: write_text() (0666 under umask 000) then chmod(0600). A crash
    between the two left the key readable by every local user. The mode must be
    right from creation, so a chmod that never runs changes nothing."""
    creds_module, data_dir = key_file_env

    def _die(self, *a, **kw):
        raise RuntimeError("process died before chmod")

    monkeypatch.setattr(Path, "chmod", _die)
    with contextlib.suppress(RuntimeError):
        creds_module.get_master_key()

    key_file = data_dir / "master.key"
    assert key_file.exists()
    assert stat.S_IMODE(key_file.stat().st_mode) & 0o077 == 0


def test_first_boot_race_loser_uses_winners_key(key_file_env):
    creds_module, data_dir = key_file_env
    data_dir.mkdir()
    winner = os.urandom(32)
    (data_dir / "master.key").write_text(winner.hex(), encoding="utf-8")

    assert creds_module._create_master_key_file() == winner
    assert (data_dir / "master.key").read_text(encoding="utf-8") == winner.hex()


def test_first_boot_race_loser_waits_for_partial_write(key_file_env):
    """The winner has created the file but not written the key yet."""
    creds_module, data_dir = key_file_env
    data_dir.mkdir()
    key_file = data_dir / "master.key"
    key_file.write_text("", encoding="utf-8")
    winner = os.urandom(32)

    def _finish_write():
        time.sleep(0.05)
        key_file.write_text(winner.hex(), encoding="utf-8")

    t = threading.Thread(target=_finish_write)
    t.start()
    try:
        assert creds_module._create_master_key_file() == winner
    finally:
        t.join()


def test_first_boot_race_truncated_key_raises(key_file_env, monkeypatch):
    creds_module, data_dir = key_file_env
    monkeypatch.setattr(creds_module, "_KEY_RACE_WAIT_S", 0.05)
    data_dir.mkdir()
    (data_dir / "master.key").write_text("abcd", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Invalid master\.key"):
        creds_module._create_master_key_file()
