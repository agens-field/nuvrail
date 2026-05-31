"""
Tests for the Pattern B secret-store abstraction.

Covers:
  - store_credential / fetch_credential round-trip via an injected fake backend
  - fetch_credential dispatches on the stored value (plaintext, v1 AES, v2 ref)
  - delete_credential removes the external secret (and is a no-op for v1/plaintext)
  - is_reference classification
  - CachingSecretStore: single-flight + TTL expiry
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

import gateway.secret_store as ss
from gateway import credentials as creds


# ---------------------------------------------------------------------------
# A fake external backend that records calls and stores plaintext in a dict.
# ---------------------------------------------------------------------------
class FakeStore:
    backend = "aws-sm"

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.put_calls = 0
        self.get_calls = 0
        self.delete_calls = 0
        self._n = 0

    async def put(self, plaintext, *, ctx):
        self.put_calls += 1
        self._n += 1
        ref = f"fakeref://{ctx.field}/{ctx.secret_id}/{self._n}"
        self.data[ref] = plaintext
        return ref

    async def get(self, ref):
        self.get_calls += 1
        return self.data[ref]

    async def delete(self, ref):
        self.delete_calls += 1
        self.data.pop(ref, None)


@pytest.fixture()
def fake_backend(monkeypatch):
    """Select the aws-sm backend and inject a FakeStore singleton for it."""
    monkeypatch.setenv("NUVRAIL_SECRET_BACKEND", "aws-sm")
    # Master key still needed so any v1 AES paths in a test work.
    monkeypatch.setenv("NUVRAIL_MASTER_KEY", os.urandom(32).hex())
    creds._cached_master_key = None
    store = FakeStore()
    ss.reset_secret_store_cache()
    ss._stores["aws-sm"] = store
    yield store
    ss.reset_secret_store_cache()
    creds._cached_master_key = None


# ---------------------------------------------------------------------------
# store / fetch round-trip
# ---------------------------------------------------------------------------
async def test_store_returns_v2_reference_not_plaintext(fake_backend):
    stored = await creds.store_credential("hunter2", field="upstream_password", owner_user_id=7)

    assert "hunter2" not in stored, "plaintext leaked into the DB value"
    env = json.loads(stored)
    assert env["v"] == 2
    assert env["backend"] == "aws-sm"
    assert creds.is_reference(stored)
    assert fake_backend.put_calls == 1
    # Plaintext lives in the backend, addressed by the ref.
    assert fake_backend.data[env["ref"]] == "hunter2"


async def test_fetch_resolves_v2_reference(fake_backend):
    stored = await creds.store_credential("refresh-tok", field="oauth2_refresh_token")
    assert await creds.fetch_credential(stored) == "refresh-tok"


async def test_delete_removes_external_secret(fake_backend):
    stored = await creds.store_credential("secret", field="oauth2_client_secret")
    ref = json.loads(stored)["ref"]
    assert ref in fake_backend.data

    await creds.delete_credential(stored)
    assert ref not in fake_backend.data
    assert fake_backend.delete_calls == 1


# ---------------------------------------------------------------------------
# Dispatch on the stored value, not the configured backend
# ---------------------------------------------------------------------------
async def test_fetch_still_reads_v1_aes_with_cloud_backend_configured(fake_backend):
    """A v1 AES envelope must decrypt locally even though aws-sm is configured."""
    v1 = creds.encrypt_credential("legacy-aes-value")
    assert creds.is_encrypted(v1)
    assert not creds.is_reference(v1)
    assert await creds.fetch_credential(v1) == "legacy-aes-value"
    assert fake_backend.get_calls == 0  # never hit the cloud store


async def test_fetch_returns_legacy_plaintext(fake_backend):
    assert await creds.fetch_credential("bare-plaintext") == "bare-plaintext"


async def test_delete_noop_for_v1_and_plaintext(fake_backend):
    await creds.delete_credential(creds.encrypt_credential("x"))
    await creds.delete_credential("plaintext")
    await creds.delete_credential(None)
    assert fake_backend.delete_calls == 0


async def test_local_backend_round_trips_via_aes(monkeypatch):
    """With the default local backend, store/fetch use AES and write no ref."""
    monkeypatch.delenv("NUVRAIL_SECRET_BACKEND", raising=False)
    monkeypatch.setenv("NUVRAIL_MASTER_KEY", os.urandom(32).hex())
    creds._cached_master_key = None
    try:
        stored = await creds.store_credential("pw", field="upstream_password")
        assert creds.is_encrypted(stored)
        assert not creds.is_reference(stored)
        assert await creds.fetch_credential(stored) == "pw"
    finally:
        creds._cached_master_key = None


# ---------------------------------------------------------------------------
# CachingSecretStore behaviour
# ---------------------------------------------------------------------------
class CountingStore:
    backend = "aws-sm"

    def __init__(self):
        self.gets = 0

    async def get(self, ref):
        self.gets += 1
        await asyncio.sleep(0)  # yield so concurrent callers interleave
        return f"value-for-{ref}"

    async def put(self, plaintext, *, ctx):  # pragma: no cover - unused here
        return "ref"

    async def delete(self, ref):  # pragma: no cover - unused here
        pass


async def test_caching_store_single_flight():
    inner = CountingStore()
    cache = ss.CachingSecretStore(inner, ttl=60, max_entries=8)

    results = await asyncio.gather(*[cache.get("r1") for _ in range(10)])
    assert results == ["value-for-r1"] * 10
    assert inner.gets == 1, "concurrent reads of the same ref should fetch once"


async def test_caching_store_ttl_expiry(monkeypatch):
    inner = CountingStore()
    clock = {"t": 1000.0}
    monkeypatch.setattr(ss.time, "monotonic", lambda: clock["t"])

    cache = ss.CachingSecretStore(inner, ttl=30, max_entries=8)
    assert await cache.get("r") == "value-for-r"
    assert inner.gets == 1

    clock["t"] += 10  # within TTL → cached
    await cache.get("r")
    assert inner.gets == 1

    clock["t"] += 30  # past TTL → refetch
    await cache.get("r")
    assert inner.gets == 2
