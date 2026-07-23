"""
External secret stores for Nuvrail — Pattern B (store-of-record).

Long-lived, high-value credentials (the upstream IMAP/SMTP password, the
OAuth2 refresh token, and the OAuth2 client secret) are kept in a managed
secret service — AWS Secrets Manager or GCP Secret Manager — instead of being
encrypted-at-rest in the local SQLite DB.

What the DB stores instead is a small *reference envelope* (built in
gateway/credentials.py):

    {"v":2,"backend":"aws-sm","ref":"arn:aws:secretsmanager:…:secret:nuvrail/prod/<uuid>-AbCdEf"}
    {"v":2,"backend":"gcp-sm","ref":"projects/123/secrets/nuvrail-prod-<uuid>/versions/1"}

Secrets are fetched on demand and held only in a short-TTL in-process cache, so
plaintext lives in memory only transiently and only for accounts that are
actively in use ("as and only as needed").

Short-lived *derived* tokens (the OAuth2 access token) are NOT stored here —
they live in a process-local cache in gateway/oauth2_tokens.py — which keeps
secret-manager write churn to a minimum.

Backend selection (env var):
    NUVRAIL_SECRET_BACKEND = local | aws-sm | gcp-sm        (default: local)

`local` keeps the existing AES-256-GCM behaviour and requires no cloud SDK, so
dev and CI are unaffected. The cloud SDKs are imported lazily inside each
backend; install them via extras:

    pip install nuvrail-gateway[aws]    # aioboto3
    pip install nuvrail-gateway[gcp]    # google-cloud-secret-manager

Other env vars:
    NUVRAIL_ENV                      deployment env name used in secret names (default: dev)
    NUVRAIL_SECRET_CACHE_TTL         plaintext cache TTL seconds (default: 300)
    NUVRAIL_SECRET_CACHE_MAX         max cached plaintext entries (default: 1024)
    NUVRAIL_SECRET_RECOVERY_WINDOW_DAYS  AWS soft-delete recovery window (default: 7)

  AWS:
    NUVRAIL_AWS_SECRET_PREFIX        secret name prefix (default: nuvrail/<env>)
    NUVRAIL_AWS_REGION               region (optional; boto3 resolves default)
    NUVRAIL_AWS_KMS_KEY_ID           CMK to encrypt secrets with (optional)

  GCP:
    NUVRAIL_GCP_PROJECT              project id (required for gcp-sm)
    NUVRAIL_GCP_SECRET_PREFIX        secret id prefix (default: nuvrail-<env>)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

LOCAL_BACKEND = "local"
AWS_BACKEND = "aws-sm"
GCP_BACKEND = "gcp-sm"


# ---------------------------------------------------------------------------
# Context passed to a store on write — used for naming / tagging / auditing.
# Retrieval never needs it: the full ref returned by put() is what gets stored
# in the DB and handed back to get()/delete().
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SecretContext:
    field: str                       # "upstream_password" | "oauth2_refresh_token" | …
    secret_id: str                   # opaque unique id (uuid4) chosen by the caller
    owner_user_id: int | None = None


@runtime_checkable
class SecretStore(Protocol):
    """A place to put/get/delete a single secret string, addressed by a ref."""

    backend: str

    async def put(self, plaintext: str, *, ctx: SecretContext) -> str:
        """Create the secret and return its full reference string."""

    async def get(self, ref: str) -> str:
        """Return the plaintext for a stored reference."""

    async def delete(self, ref: str) -> None:
        """Delete the secret addressed by ref (idempotent best-effort)."""


# ---------------------------------------------------------------------------
# Naming / tagging helpers
# ---------------------------------------------------------------------------
def _env_name() -> str:
    return (os.environ.get("NUVRAIL_ENV", "dev").strip() or "dev").lower()


def _common_tags(ctx: SecretContext, env: str) -> dict[str, str]:
    tags = {"app": "nuvrail", "env": env, "field": ctx.field}
    if ctx.owner_user_id is not None:
        tags["owner_user_id"] = str(ctx.owner_user_id)
    return tags


# ---------------------------------------------------------------------------
# AWS Secrets Manager
# ---------------------------------------------------------------------------
class AwsSecretsManagerStore:
    backend = AWS_BACKEND

    def __init__(
        self,
        *,
        prefix: str,
        env: str,
        region: str | None = None,
        kms_key_id: str | None = None,
        recovery_window_days: int = 7,
    ) -> None:
        self._prefix = prefix.rstrip("/")
        self._env = env
        self._region = region
        self._kms_key_id = kms_key_id
        self._recovery_window_days = recovery_window_days
        self._session = None  # created lazily

    def _client(self):
        # aioboto3 clients are async context managers; we open one per call.
        # Reads are served from CachingSecretStore, so this only runs on
        # cold reads, writes, and deletes — all rare.
        import aioboto3

        if self._session is None:
            self._session = aioboto3.Session()
        return self._session.client("secretsmanager", region_name=self._region)

    async def put(self, plaintext: str, *, ctx: SecretContext) -> str:
        name = f"{self._prefix}/{ctx.secret_id}"
        kwargs: dict = {
            "Name": name,
            "SecretString": plaintext,
            "Tags": [{"Key": k, "Value": v} for k, v in _common_tags(ctx, self._env).items()],
        }
        if self._kms_key_id:
            kwargs["KmsKeyId"] = self._kms_key_id
        async with self._client() as client:
            resp = await client.create_secret(**kwargs)
        return resp["ARN"]

    async def get(self, ref: str) -> str:
        async with self._client() as client:
            resp = await client.get_secret_value(SecretId=ref)
        # SecretString is what we always write; binary secrets are not used.
        return resp["SecretString"]

    async def delete(self, ref: str) -> None:
        async with self._client() as client:
            await client.delete_secret(
                SecretId=ref,
                RecoveryWindowInDays=self._recovery_window_days,
            )


# ---------------------------------------------------------------------------
# GCP Secret Manager
# ---------------------------------------------------------------------------
class GcpSecretManagerStore:
    backend = GCP_BACKEND

    def __init__(self, *, project: str, prefix: str, env: str) -> None:
        if not project:
            raise ValueError("NUVRAIL_GCP_PROJECT must be set for the gcp-sm backend")
        self._project = project
        self._prefix = prefix.rstrip("-")
        self._env = env
        self._client_obj = None  # created lazily

    def _client(self):
        from google.cloud import secretmanager_v1

        if self._client_obj is None:
            self._client_obj = secretmanager_v1.SecretManagerServiceAsyncClient()
        return self._client_obj

    async def put(self, plaintext: str, *, ctx: SecretContext) -> str:
        client = self._client()
        secret_id = f"{self._prefix}-{ctx.secret_id}"
        secret = await client.create_secret(
            request={
                "parent": f"projects/{self._project}",
                "secret_id": secret_id,
                "secret": {
                    "replication": {"automatic": {}},
                    "labels": _common_tags(ctx, self._env),
                },
            }
        )
        version = await client.add_secret_version(
            request={
                "parent": secret.name,
                "payload": {"data": plaintext.encode("utf-8")},
            }
        )
        # Full version resource name, e.g. projects/p/secrets/<id>/versions/1
        return version.name

    async def get(self, ref: str) -> str:
        client = self._client()
        resp = await client.access_secret_version(request={"name": ref})
        return resp.payload.data.decode("utf-8")

    async def delete(self, ref: str) -> None:
        client = self._client()
        # ref is a version name; delete the parent secret (all versions).
        secret_name = ref.split("/versions/")[0]
        import contextlib

        from google.api_core.exceptions import NotFound

        # already gone — deletion is idempotent
        with contextlib.suppress(NotFound):
            await client.delete_secret(request={"name": secret_name})


# ---------------------------------------------------------------------------
# In-process TTL cache in front of any store. This is what implements
# "fetched only as needed, then released": plaintext is held briefly and then
# evicted, and N concurrent reads of the same ref collapse to one fetch.
# ---------------------------------------------------------------------------
class CachingSecretStore:
    def __init__(self, inner: SecretStore, *, ttl: float, max_entries: int) -> None:
        self._inner = inner
        self.backend = inner.backend
        self._ttl = ttl
        self._max = max_entries
        self._cache: dict[str, tuple[str, float]] = {}  # ref -> (plaintext, expires_at)
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    def _fresh(self, ref: str, now: float) -> str | None:
        hit = self._cache.get(ref)
        if hit is not None and hit[1] > now:
            return hit[0]
        return None

    def _store(self, ref: str, value: str, now: float) -> None:
        if len(self._cache) >= self._max:
            # Drop expired first; if still full, drop an arbitrary entry.
            expired = [k for k, (_, exp) in self._cache.items() if exp <= now]
            for k in expired:
                self._cache.pop(k, None)
            while len(self._cache) >= self._max:
                self._cache.pop(next(iter(self._cache)))
        self._cache[ref] = (value, now + self._ttl)

    async def _lock_for(self, ref: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(ref)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[ref] = lock
            return lock

    async def get(self, ref: str) -> str:
        now = time.monotonic()
        cached = self._fresh(ref, now)
        if cached is not None:
            return cached
        lock = await self._lock_for(ref)
        async with lock:  # single-flight: only one fetch per ref at a time
            now = time.monotonic()
            cached = self._fresh(ref, now)
            if cached is not None:
                return cached
            value = await self._inner.get(ref)
            self._store(ref, value, now)
            return value

    async def put(self, plaintext: str, *, ctx: SecretContext) -> str:
        ref = await self._inner.put(plaintext, ctx=ctx)
        self._store(ref, plaintext, time.monotonic())  # warm the cache
        return ref

    async def delete(self, ref: str) -> None:
        self._cache.pop(ref, None)
        await self._inner.delete(ref)

    def invalidate(self, ref: str) -> None:
        self._cache.pop(ref, None)


# ---------------------------------------------------------------------------
# Factory + config
# ---------------------------------------------------------------------------
_stores: dict[str, SecretStore] = {}


def configured_backend() -> str:
    """The backend new writes go to. Reads dispatch on the stored envelope."""
    return (os.environ.get("NUVRAIL_SECRET_BACKEND", LOCAL_BACKEND).strip() or LOCAL_BACKEND).lower()


def _build_store(backend: str) -> SecretStore:
    env = _env_name()
    ttl = float(os.environ.get("NUVRAIL_SECRET_CACHE_TTL", "300"))
    max_entries = int(os.environ.get("NUVRAIL_SECRET_CACHE_MAX", "1024"))

    if backend == AWS_BACKEND:
        prefix = os.environ.get("NUVRAIL_AWS_SECRET_PREFIX", f"nuvrail/{env}")
        inner: SecretStore = AwsSecretsManagerStore(
            prefix=prefix,
            env=env,
            region=os.environ.get("NUVRAIL_AWS_REGION") or None,
            kms_key_id=os.environ.get("NUVRAIL_AWS_KMS_KEY_ID") or None,
            recovery_window_days=int(
                os.environ.get("NUVRAIL_SECRET_RECOVERY_WINDOW_DAYS", "7")
            ),
        )
    elif backend == GCP_BACKEND:
        prefix = os.environ.get("NUVRAIL_GCP_SECRET_PREFIX", f"nuvrail-{env}")
        inner = GcpSecretManagerStore(
            project=os.environ.get("NUVRAIL_GCP_PROJECT", ""),
            prefix=prefix,
            env=env,
        )
    else:
        raise ValueError(f"Unknown secret backend: {backend!r}")

    return CachingSecretStore(inner, ttl=ttl, max_entries=max_entries)


def get_secret_store(backend: str | None = None) -> SecretStore | None:
    """Return the store for `backend` (or the configured one).

    Returns None for the `local` backend, which is handled inline by
    gateway/credentials.py with AES-256-GCM (no external store).
    """
    backend = (backend or configured_backend()).lower()
    if backend == LOCAL_BACKEND:
        return None
    store = _stores.get(backend)
    if store is None:
        store = _build_store(backend)
        _stores[backend] = store
    return store


def reset_secret_store_cache() -> None:
    """Drop cached store singletons (and their plaintext caches). For tests."""
    _stores.clear()
