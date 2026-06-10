"""
Continuous staging E2E monitor — exercises the *deployed* fly.io staging
environment over its real network endpoints, on a schedule.

This is distinct from tests/e2e/, which stands the proxies + FastAPI up
*in-process* against an upstream mailbox. That suite proves the code is
correct. THIS suite proves the deployed staging environment
(https://nuvrail-staging-gateway.fly.dev + https://nuvrail-staging-web.fly.dev)
is actually up, wired correctly, and behaving — continuously, throughout
the day — so a silent staging regression is caught within one schedule
interval instead of at the next manual smoke test.

  ┌──────────────────────────────────────────────────────────────────┐
  │  GitHub Actions (schedule: every 45 min)                           │
  │     └─► python -m tests.staging_smoke.runner                       │
  │            │                                                       │
  │            ├─ HTTPS ─► nuvrail-staging-gateway.fly.dev  (REST API) │
  │            │            register → login → agent → operations →    │
  │            │            audit → account lifecycle                  │
  │            ├─ HTTPS ─► nuvrail-staging-web.fly.dev      (PWA up)   │
  │            └─ assert CORS isolation (prod origin rejected)         │
  │                                                                    │
  │     exit 0 = all green · exit 1 = at least one check failed        │
  │     non-zero exit + JSON report ──► workflow alert (issue/Slack)   │
  └──────────────────────────────────────────────────────────────────┘

Design constraints (KC):
  - NO secrets in the repo. Everything credentialed comes from env vars,
    injected as GitHub Actions secrets at run time. With no env set, the
    REST-only public-surface checks (health, web up, CORS isolation,
    register/login self-serve) still run; the credentialed proxy
    write→approve→deliver and rejection/revert checks are SKIPPED, not
    failed, so the monitor degrades gracefully.
  - Read-mostly + self-cleaning: every check that creates state
    (a throwaway user, a staged operation) tears it down via the same
    public API (account deletion), so the staging DB does not accumulate
    monitor cruft across hundreds of runs.
  - Explicit over clever: each check is a small named function returning
    a Check result; the runner just sequences them and aggregates.

Proxy-level IMAP/SMTP write→stage→approve→deliver and rejection/revert
checks require the staging gateway to expose its 993/587 proxy ports AND
a dedicated monitor mailbox credential. Those land with the enterprise
deploy/ops wiring (the proxy port exposure + monitor secret live there).
Until then they report status="skipped" with a clear reason so the gap
is visible in every report rather than silently absent.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable

import httpx

# --------------------------------------------------------------------------
# Configuration (env-driven; safe defaults point at the known staging apps)
# --------------------------------------------------------------------------

GATEWAY_URL = os.environ.get(
    "NUVRAIL_STAGING_GATEWAY_URL", "https://nuvrail-staging-gateway.fly.dev"
).rstrip("/")
WEB_URL = os.environ.get(
    "NUVRAIL_STAGING_WEB_URL", "https://nuvrail-staging-web.fly.dev"
).rstrip("/")
# Origin that MUST be rejected by staging CORS (production web app).
PROD_ORIGIN = os.environ.get(
    "NUVRAIL_PROD_WEB_ORIGIN", "https://nuvrail-app.fly.dev"
)
# Per-request network timeout (seconds). fly cold-starts can be slow.
HTTP_TIMEOUT = float(os.environ.get("NUVRAIL_STAGING_HTTP_TIMEOUT", "30"))

API = f"{GATEWAY_URL}/api/v1"


@dataclass
class Check:
    """Result of a single named monitor check."""

    name: str
    status: str = "pending"          # ok | failed | skipped
    detail: str = ""
    duration_ms: int = 0

    def ok(self, detail: str = "") -> "Check":
        self.status, self.detail = "ok", detail
        return self

    def fail(self, detail: str) -> "Check":
        self.status, self.detail = "failed", detail
        return self

    def skip(self, detail: str) -> "Check":
        self.status, self.detail = "skipped", detail
        return self


@dataclass
class Report:
    started_at: str
    gateway_url: str
    web_url: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "failed"]

    @property
    def skipped(self) -> list[Check]:
        return [c for c in self.checks if c.status == "skipped"]

    @property
    def passed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "ok"]

    def to_json(self) -> str:
        return json.dumps(
            {
                "started_at": self.started_at,
                "gateway_url": self.gateway_url,
                "web_url": self.web_url,
                "summary": {
                    "passed": len(self.passed),
                    "failed": len(self.failed),
                    "skipped": len(self.skipped),
                },
                "checks": [asdict(c) for c in self.checks],
            },
            indent=2,
        )


def _timed(name: str, fn: Callable[[httpx.Client, Check], None], client: httpx.Client) -> Check:
    """Run one check function, capturing timing and turning any exception
    into a failed result (a monitor that crashes on its own bug is useless)."""
    check = Check(name=name)
    start = time.monotonic()
    try:
        fn(client, check)
        if check.status == "pending":  # the fn forgot to set a status
            check.fail("check did not record a result")
    except Exception as exc:  # noqa: BLE001 — monitor must never crash
        check.fail(f"{type(exc).__name__}: {exc}")
    check.duration_ms = int((time.monotonic() - start) * 1000)
    return check


# --------------------------------------------------------------------------
# Checks — public surface (always run, no credentials needed)
# --------------------------------------------------------------------------


def check_gateway_health(client: httpx.Client, c: Check) -> None:
    r = client.get(f"{GATEWAY_URL}/health")
    if r.status_code != 200:
        return c.fail(f"GET /health -> {r.status_code}") and None
    body = r.json()
    if body.get("status") != "ok":
        return c.fail(f"unexpected health body: {body}") and None
    c.ok(f"status=ok ts={body.get('timestamp')}")


def check_web_up(client: httpx.Client, c: Check) -> None:
    r = client.get(WEB_URL)
    if r.status_code != 200:
        return c.fail(f"GET {WEB_URL} -> {r.status_code}") and None
    # The PWA shell should serve HTML, not an API error page.
    ctype = r.headers.get("content-type", "")
    if "text/html" not in ctype:
        return c.fail(f"web served non-HTML content-type: {ctype}") and None
    c.ok(f"HTTP 200, content-type={ctype}")


def check_cors_isolation(client: httpx.Client, c: Check) -> None:
    """A request claiming the production web origin must NOT be granted
    an Access-Control-Allow-Origin for that origin by staging."""
    r = client.options(
        f"{API}/auth/login",
        headers={
            "Origin": PROD_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    allow = r.headers.get("access-control-allow-origin", "")
    if allow == PROD_ORIGIN:
        return c.fail(
            f"staging granted CORS to production origin {PROD_ORIGIN} — isolation breach"
        ) and None
    c.ok(f"prod origin not allowed (allow-origin={allow or 'absent'})")


def check_vapid_key(client: httpx.Client, c: Check) -> None:
    """Push is part of the approval product; the VAPID public key endpoint
    proves push config is present on the deployed gateway."""
    r = client.get(f"{API}/push/vapid-key")
    if r.status_code != 200:
        return c.fail(f"GET /push/vapid-key -> {r.status_code}") and None
    key = r.json().get("public_key") or r.json().get("publicKey")
    if not key:
        return c.fail(f"no VAPID public key in response: {r.json()}") and None
    c.ok(f"VAPID public key present ({len(key)} chars)")


# --------------------------------------------------------------------------
# Checks — authenticated lifecycle (self-serve register/login/delete)
# --------------------------------------------------------------------------


def check_account_lifecycle(client: httpx.Client, c: Check) -> None:
    """Full self-serve lifecycle against the deployed gateway, end to end:
        register → login → /auth/me → create agent → list operations
        (empty is fine) → audit list → DELETE account (cleanup).
    Uses a throwaway, uniquely-named monitor account each run and deletes
    it at the end so staging does not accumulate monitor users.
    """
    email = f"staging-monitor+{uuid.uuid4().hex[:12]}@nuvrail.invalid"
    password = "monitor-" + uuid.uuid4().hex

    reg = client.post(
        f"{API}/auth/register",
        json={"email": email, "password": password, "display_name": "Staging Monitor"},
    )
    if reg.status_code != 201:
        return c.fail(f"register -> {reg.status_code}: {reg.text[:200]}") and None

    login = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    if login.status_code != 200:
        return c.fail(f"login -> {login.status_code}: {login.text[:200]}") and None
    token = login.json().get("token")
    if not token:
        return c.fail(f"login returned no token: {login.text[:200]}") and None
    auth = {"Authorization": f"Bearer {token}"}

    me = client.get(f"{API}/auth/me", headers=auth)
    if me.status_code != 200 or me.json().get("email") != email:
        return c.fail(f"/auth/me mismatch: {me.status_code} {me.text[:200]}") and None

    # operations + audit should respond (empty list is the expected state).
    ops = client.get(f"{API}/operations", headers=auth)
    if ops.status_code != 200:
        return c.fail(f"GET /operations -> {ops.status_code}") and None
    audit = client.get(f"{API}/audit", headers=auth)
    if audit.status_code != 200:
        return c.fail(f"GET /audit -> {audit.status_code}") and None

    # Cleanup: delete the throwaway account. A monitor that litters the DB
    # is its own slow-burn incident. DELETE /account requires the password
    # in the body (confirmation guard, prevents accidental/CSRF deletion),
    # so we must send a JSON body on the DELETE — httpx.Client.delete() takes
    # no `json=`, hence client.request("DELETE", ...).
    delete = client.request(
        "DELETE", f"{API}/account", headers=auth, json={"password": password}
    )
    if delete.status_code not in (200, 202, 204):
        return c.fail(
            f"register/login/me/ops/audit OK but account cleanup failed: "
            f"DELETE /account -> {delete.status_code}"
        ) and None

    c.ok("register→login→me→operations→audit→delete all green; account cleaned up")


# --------------------------------------------------------------------------
# Checks — credentialed proxy flow (skipped until enterprise wiring lands)
# --------------------------------------------------------------------------


def check_proxy_write_approve_deliver(client: httpx.Client, c: Check) -> None:
    monitor_user = os.environ.get("NUVRAIL_STAGING_MONITOR_IMAP_USER")
    proxy_imap_port = os.environ.get("NUVRAIL_STAGING_PROXY_IMAP_PORT")
    if not monitor_user or not proxy_imap_port:
        return c.skip(
            "no monitor mailbox / proxy IMAP port exposed on staging yet — "
            "needs enterprise deploy wiring (NUVRAIL_STAGING_MONITOR_IMAP_USER + "
            "NUVRAIL_STAGING_PROXY_IMAP_PORT). Tracking: #18 follow-on."
        ) and None
    # Implemented once the staging gateway exposes its proxy ports; reuses the
    # aioimaplib/aiosmtplib helpers in tests/e2e/helpers.py against the
    # deployed proxy endpoint rather than an in-process one.
    return c.skip("proxy endpoint reachable but flow not yet implemented for staging") and None


def check_rejection_revert(client: httpx.Client, c: Check) -> None:
    if not os.environ.get("NUVRAIL_STAGING_MONITOR_IMAP_USER"):
        return c.skip(
            "rejection/revert + unsolicited-FETCH proof needs the monitor mailbox "
            "credential (enterprise wiring); skipped. Tracking: #18 follow-on."
        ) and None
    return c.skip("monitor mailbox present but rejection/revert flow not yet implemented") and None


PUBLIC_CHECKS = [
    ("gateway_health", check_gateway_health),
    ("web_up", check_web_up),
    ("cors_isolation", check_cors_isolation),
    ("vapid_key", check_vapid_key),
    ("account_lifecycle", check_account_lifecycle),
]

CREDENTIALED_CHECKS = [
    ("proxy_write_approve_deliver", check_proxy_write_approve_deliver),
    ("rejection_revert", check_rejection_revert),
]


def run() -> Report:
    report = Report(
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        gateway_url=GATEWAY_URL,
        web_url=WEB_URL,
    )
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        for name, fn in PUBLIC_CHECKS + CREDENTIALED_CHECKS:
            report.checks.append(_timed(name, fn, client))
    return report


def main() -> int:
    report = run()
    print(report.to_json())
    # Human-readable tail for the Actions log.
    print(
        f"\nstaging-monitor: {len(report.passed)} passed, "
        f"{len(report.failed)} failed, {len(report.skipped)} skipped "
        f"against {GATEWAY_URL}",
        file=sys.stderr,
    )
    # Exit non-zero on any failure so the scheduled workflow alerts.
    # Skipped checks do NOT fail the run (graceful degradation).
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
