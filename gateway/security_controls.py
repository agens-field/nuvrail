from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    retry_after_seconds: int
    reason: str


@dataclass(frozen=True)
class FailureResult:
    lockout_applied: bool
    retry_after_seconds: int


class AuthAbuseProtector:
    """In-memory auth abuse controls for brute-force mitigation.

    This enforces:
    - Per-IP and per-account attempt rate limiting
    - Per-IP and per-account failed-login lockouts with exponential backoff
    - Security alert logs when thresholds or lockouts are hit
    """

    def __init__(
        self,
        *,
        namespace: str,
        attempt_window_seconds: int,
        max_attempts_per_ip_window: int,
        max_attempts_per_account_window: int,
        failure_window_seconds: int,
        max_failures_before_lockout: int,
        base_lockout_seconds: int,
        max_lockout_seconds: int,
    ) -> None:
        self.namespace = namespace
        self.attempt_window_seconds = attempt_window_seconds
        self.max_attempts_per_ip_window = max_attempts_per_ip_window
        self.max_attempts_per_account_window = max_attempts_per_account_window
        self.failure_window_seconds = failure_window_seconds
        self.max_failures_before_lockout = max_failures_before_lockout
        self.base_lockout_seconds = base_lockout_seconds
        self.max_lockout_seconds = max_lockout_seconds

        self._lock = asyncio.Lock()
        self._attempts_by_ip: dict[str, deque[int]] = {}
        self._attempts_by_account: dict[str, deque[int]] = {}
        self._failures_by_ip: dict[str, deque[int]] = {}
        self._failures_by_account: dict[str, deque[int]] = {}
        self._lockout_until: dict[str, int] = {}
        self._lockout_count: dict[str, int] = {}

    def _prune(self, bucket: deque[int], now: int, window_seconds: int) -> None:
        threshold = now - window_seconds
        while bucket and bucket[0] <= threshold:
            bucket.popleft()

    def _get_retry_seconds(self, key: str, now: int) -> int:
        until = self._lockout_until.get(key, 0)
        if until <= now:
            return 0
        return max(1, until - now)

    def _activate_lockout(self, key: str, now: int, reason: str) -> int:
        count = self._lockout_count.get(key, 0) + 1
        self._lockout_count[key] = count
        duration = min(self.base_lockout_seconds * (2 ** (count - 1)), self.max_lockout_seconds)
        until = now + duration
        self._lockout_until[key] = until
        logger.warning(
            "[SECURITY][%s] lockout activated key=%s reason=%s duration=%ss",
            self.namespace,
            key,
            reason,
            duration,
        )
        return duration

    async def start_attempt(self, *, ip: str, account: str) -> AuthDecision:
        """Check lockout/rate limits and reserve one attempt."""
        now = int(time.time())
        ip_key = f"ip:{ip}"
        account_key = f"acct:{account.lower()}"

        async with self._lock:
            for key in (ip_key, account_key):
                retry = self._get_retry_seconds(key, now)
                if retry > 0:
                    logger.warning(
                        "[SECURITY][%s] auth attempt blocked key=%s reason=active_lockout retry_after=%ss",
                        self.namespace,
                        key,
                        retry,
                    )
                    return AuthDecision(False, retry, "temporary_lockout")

            ip_attempts = self._attempts_by_ip.setdefault(ip_key, deque())
            acct_attempts = self._attempts_by_account.setdefault(account_key, deque())
            self._prune(ip_attempts, now, self.attempt_window_seconds)
            self._prune(acct_attempts, now, self.attempt_window_seconds)

            if len(ip_attempts) >= self.max_attempts_per_ip_window:
                logger.warning(
                    "[SECURITY][%s] rate limit exceeded scope=ip key=%s attempts=%d window=%ss",
                    self.namespace,
                    ip_key,
                    len(ip_attempts),
                    self.attempt_window_seconds,
                )
                return AuthDecision(False, self.attempt_window_seconds, "rate_limited")

            if len(acct_attempts) >= self.max_attempts_per_account_window:
                logger.warning(
                    "[SECURITY][%s] rate limit exceeded scope=account key=%s attempts=%d window=%ss",
                    self.namespace,
                    account_key,
                    len(acct_attempts),
                    self.attempt_window_seconds,
                )
                return AuthDecision(False, self.attempt_window_seconds, "rate_limited")

            ip_attempts.append(now)
            acct_attempts.append(now)
            return AuthDecision(True, 0, "allowed")

    async def record_success(self, *, ip: str, account: str) -> None:
        now = int(time.time())
        ip_key = f"ip:{ip}"
        account_key = f"acct:{account.lower()}"
        async with self._lock:
            self._failures_by_ip.pop(ip_key, None)
            self._failures_by_account.pop(account_key, None)
            # Keep lockout counters for backoff memory, but clear active lockout.
            self._lockout_until.pop(ip_key, None)
            self._lockout_until.pop(account_key, None)
            logger.info(
                "[SECURITY][%s] authentication success key_ip=%s key_account=%s",
                self.namespace,
                ip_key,
                account_key,
            )

    async def record_failure(self, *, ip: str, account: str) -> FailureResult:
        now = int(time.time())
        ip_key = f"ip:{ip}"
        account_key = f"acct:{account.lower()}"
        async with self._lock:
            ip_failures = self._failures_by_ip.setdefault(ip_key, deque())
            acct_failures = self._failures_by_account.setdefault(account_key, deque())
            self._prune(ip_failures, now, self.failure_window_seconds)
            self._prune(acct_failures, now, self.failure_window_seconds)
            ip_failures.append(now)
            acct_failures.append(now)

            logger.warning(
                "[SECURITY][%s] authentication failure key_ip=%s key_account=%s ip_failures=%d acct_failures=%d",
                self.namespace,
                ip_key,
                account_key,
                len(ip_failures),
                len(acct_failures),
            )

            retry_after = 0
            lockout_applied = False
            if len(ip_failures) >= self.max_failures_before_lockout:
                retry_after = max(
                    retry_after,
                    self._activate_lockout(ip_key, now, "too_many_failed_auth_attempts"),
                )
                lockout_applied = True

            if len(acct_failures) >= self.max_failures_before_lockout:
                retry_after = max(
                    retry_after,
                    self._activate_lockout(account_key, now, "too_many_failed_auth_attempts"),
                )
                lockout_applied = True

            return FailureResult(lockout_applied, retry_after)


def build_auth_abuse_protector(namespace: str) -> AuthAbuseProtector:
    return AuthAbuseProtector(
        namespace=namespace,
        attempt_window_seconds=int(os.environ.get("NUVRAIL_AUTH_ATTEMPT_WINDOW_SECONDS", "60")),
        max_attempts_per_ip_window=int(os.environ.get("NUVRAIL_AUTH_MAX_ATTEMPTS_PER_IP", "20")),
        max_attempts_per_account_window=int(
            os.environ.get("NUVRAIL_AUTH_MAX_ATTEMPTS_PER_ACCOUNT", "10")
        ),
        failure_window_seconds=int(os.environ.get("NUVRAIL_AUTH_FAILURE_WINDOW_SECONDS", "300")),
        max_failures_before_lockout=int(os.environ.get("NUVRAIL_AUTH_MAX_FAILURES_BEFORE_LOCKOUT", "5")),
        base_lockout_seconds=int(os.environ.get("NUVRAIL_AUTH_BASE_LOCKOUT_SECONDS", "120")),
        max_lockout_seconds=int(os.environ.get("NUVRAIL_AUTH_MAX_LOCKOUT_SECONDS", "3600")),
    )
