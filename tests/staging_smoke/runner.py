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
checks are FULLY IMPLEMENTED here (see check_proxy_write_approve_deliver /
check_rejection_revert). They require the staging gateway to expose its
proxy ports AND a dedicated monitor mailbox credential, which land with
the enterprise deploy/ops wiring (proxy port exposure + monitor secret
live there). Until those env vars are present the two checks report
status="skipped" with the exact missing-variable list, so the gap is
visible in every report rather than silently absent; the moment the
wiring exists the real flow runs with no further code change.

Required env for the credentialed flows (GitHub Actions secrets):
  NUVRAIL_STAGING_MONITOR_IMAP_USER       monitor mailbox address
  NUVRAIL_STAGING_MONITOR_IMAP_PASSWORD   monitor mailbox password
  NUVRAIL_STAGING_MONITOR_API_TOKEN       bearer token owning the monitor agent
  NUVRAIL_STAGING_PROXY_SMTP_PORT         deployed SMTP proxy port
  NUVRAIL_STAGING_PROXY_IMAP_PORT         deployed IMAP proxy port (reject test)
  NUVRAIL_STAGING_PROXY_HOST (optional)   defaults to the gateway host
  NUVRAIL_STAGING_UPSTREAM_IMAP_HOST/PORT ground-truth delivery verification
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

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

# --------------------------------------------------------------------------
# Credentialed proxy-flow configuration (all optional; absent => skip).
#
# These point the monitor at the DEPLOYED gateway's proxy listeners and a
# dedicated throwaway monitor mailbox. The values live as GitHub Actions
# secrets, injected at run time — never in the repo (KC: NO secrets in repo).
# The proxy host defaults to the gateway host (proxy + REST are the same fly
# app) but is overridable for split deployments.
# --------------------------------------------------------------------------


def _proxy_host_default() -> str:
    # Strip scheme from the gateway URL to get a bare host for raw TCP.
    return re.sub(r"^https?://", "", GATEWAY_URL).split("/")[0]


PROXY_HOST = os.environ.get("NUVRAIL_STAGING_PROXY_HOST", _proxy_host_default())
MONITOR_IMAP_USER = os.environ.get("NUVRAIL_STAGING_MONITOR_IMAP_USER")
MONITOR_IMAP_PASSWORD = os.environ.get("NUVRAIL_STAGING_MONITOR_IMAP_PASSWORD")
PROXY_IMAP_PORT = os.environ.get("NUVRAIL_STAGING_PROXY_IMAP_PORT")
PROXY_SMTP_PORT = os.environ.get("NUVRAIL_STAGING_PROXY_SMTP_PORT")
# Bearer token for the approval REST API account that owns the monitor agent
# (so the monitor can list + approve/reject its own staged operations).
MONITOR_API_TOKEN = os.environ.get("NUVRAIL_STAGING_MONITOR_API_TOKEN")
# Direct (non-proxy) upstream IMAP for ground-truth delivery verification.
UPSTREAM_IMAP_HOST = os.environ.get("NUVRAIL_STAGING_UPSTREAM_IMAP_HOST")
UPSTREAM_IMAP_PORT = int(os.environ.get("NUVRAIL_STAGING_UPSTREAM_IMAP_PORT", "993"))

# How long to wait for an approved message to land / a rejection to revert.
FLOW_TIMEOUT = float(os.environ.get("NUVRAIL_STAGING_FLOW_TIMEOUT", "25"))


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
# Low-level proxy clients for the credentialed flows
#
# The deployed proxy speaks plain TCP (TLS terminates upstream), exactly like
# the in-process e2e proxy, so we reuse the same SMTP dialogue + op_id parse
# as tests/e2e/test_send_and_verify.py rather than depending on its fixtures
# (those patch an in-process server and are unusable against a live host).
# --------------------------------------------------------------------------

_OP_ID_RE = re.compile(r"(op_[A-Za-z0-9]+)")


def _auth_plain_b64(user: str, password: str) -> str:
    return base64.b64encode(f"\x00{user}\x00{password}".encode()).decode()


async def _read_smtp_response(reader: asyncio.StreamReader, timeout: float) -> list[str]:
    """Read a complete (possibly multi-line) SMTP response."""
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("timed out reading SMTP response")
        raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
        if not raw:
            break
        decoded = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(decoded)
        if len(decoded) < 4 or decoded[3] != "-":
            break
    return lines


async def _smtp_stage_via_proxy(subject: str) -> str:
    """Run a full SMTP session against the deployed proxy and return the
    op_id from the 250 [STAGED] response. Raises on any protocol failure."""
    user, password = MONITOR_IMAP_USER, MONITOR_IMAP_PASSWORD
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(PROXY_HOST, int(PROXY_SMTP_PORT)), timeout=FLOW_TIMEOUT
    )
    try:
        await asyncio.wait_for(reader.readline(), timeout=FLOW_TIMEOUT)  # greeting

        async def cmd(text: str) -> list[str]:
            writer.write(text.encode())
            await writer.drain()
            return await _read_smtp_response(reader, FLOW_TIMEOUT)

        await cmd("EHLO staging-monitor\r\n")
        auth = await cmd(f"AUTH PLAIN {_auth_plain_b64(user, password)}\r\n")
        if not auth[-1].startswith("235"):
            raise AssertionError(f"AUTH rejected by proxy: {auth!r}")
        await cmd(f"MAIL FROM:<{user}>\r\n")
        await cmd(f"RCPT TO:<{user}>\r\n")
        go = await cmd("DATA\r\n")
        if not go[-1].startswith("354"):
            raise AssertionError(f"expected 354 after DATA, got {go!r}")
        body = (
            f"From: {user}\r\nTo: {user}\r\nSubject: {subject}\r\n\r\n"
            f"staging monitor probe {subject}\r\n.\r\n"
        )
        staged = await cmd(body)
        line = staged[-1]
        if not line.startswith("250") or "STAGED" not in line.upper():
            raise AssertionError(f"expected 250 [STAGED], got {staged!r}")
        m = _OP_ID_RE.search(" ".join(staged))
        if not m:
            raise AssertionError(f"no op_id in STAGED response: {staged!r}")
        await cmd("QUIT\r\n")
        return m.group(1)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass


async def _imap_search_subject(subject: str) -> Optional[int]:
    """Direct (non-proxy) IMAP poll of the monitor mailbox for a subject.
    Returns the UID if found within FLOW_TIMEOUT, else None."""
    import aioimaplib  # local import: only needed on credentialed runs

    host = UPSTREAM_IMAP_HOST or PROXY_HOST
    deadline = time.monotonic() + FLOW_TIMEOUT
    while time.monotonic() < deadline:
        client = aioimaplib.IMAP4_SSL(host=host, port=UPSTREAM_IMAP_PORT)
        try:
            await client.wait_hello_from_server()
            await client.login(MONITOR_IMAP_USER, MONITOR_IMAP_PASSWORD)
            await client.select("INBOX")
            status, data = await client.uid_search(f'HEADER SUBJECT "{subject}"')
            if status == "OK" and data and data[0]:
                uids = data[0].decode().split()
                if uids:
                    return int(uids[-1])
        except Exception:  # noqa: BLE001 — keep polling, monitor must not crash
            pass
        finally:
            try:
                await client.logout()
            except Exception:
                pass
        await asyncio.sleep(2.0)
    return None


async def _imap_delete_uid(uid: int) -> None:
    """Best-effort cleanup of a delivered probe message (direct IMAP)."""
    import aioimaplib

    host = UPSTREAM_IMAP_HOST or PROXY_HOST
    client = aioimaplib.IMAP4_SSL(host=host, port=UPSTREAM_IMAP_PORT)
    try:
        await client.wait_hello_from_server()
        await client.login(MONITOR_IMAP_USER, MONITOR_IMAP_PASSWORD)
        await client.select("INBOX")
        await client.uid("store", str(uid), "+FLAGS", r"(\Deleted)")
        await client.expunge()
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            await client.logout()
        except Exception:
            pass


async def _imap_stage_flag_via_proxy(uid_target_subject: str) -> tuple[str, int]:
    """Connect to the deployed IMAP proxy, select INBOX, and stage a write
    (\\Flagged on the newest message) so we can exercise reject/revert.
    Returns (op_id, uid). Raises on protocol failure.

    The proxy returns OK [STAGED op_...] on the tagged write response; we
    parse the op_id out of the server's tagged/untagged lines.
    """
    import aioimaplib

    client = aioimaplib.IMAP4(host=PROXY_HOST, port=int(PROXY_IMAP_PORT))
    try:
        await asyncio.wait_for(client.wait_hello_from_server(), timeout=FLOW_TIMEOUT)
        await client.login(MONITOR_IMAP_USER, MONITOR_IMAP_PASSWORD)
        await client.select("INBOX")
        status, data = await client.uid_search(f'HEADER SUBJECT "{uid_target_subject}"')
        if status != "OK" or not data or not data[0]:
            raise AssertionError("probe message not visible via proxy for reject test")
        uid = int(data[0].decode().split()[-1])
        resp = await client.uid("store", str(uid), "+FLAGS", r"(\Flagged)")
        text = " ".join(
            bytes(x).decode("utf-8", "replace") if isinstance(x, (bytes, bytearray)) else str(x)
            for x in ([resp.result] + list(resp.lines))
        )
        m = _OP_ID_RE.search(text)
        if not m:
            raise AssertionError(f"no op_id in STAGE response for flag write: {text!r}")
        return m.group(1), uid
    finally:
        try:
            await client.logout()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Checks — credentialed proxy flow
#
# Fully implemented. They self-skip (graceful degradation) until the
# enterprise deploy/ops wiring exposes the proxy ports + provisions the
# monitor mailbox secret; the instant those env vars are present, the real
# flow runs with no further code change. Tracking: #18 follow-on.
# --------------------------------------------------------------------------


def _proxy_smtp_ready() -> Optional[str]:
    """Return None if the SMTP write->approve->deliver flow can run, else a
    human-readable reason it must be skipped."""
    missing = [
        n
        for n, v in (
            ("NUVRAIL_STAGING_MONITOR_IMAP_USER", MONITOR_IMAP_USER),
            ("NUVRAIL_STAGING_MONITOR_IMAP_PASSWORD", MONITOR_IMAP_PASSWORD),
            ("NUVRAIL_STAGING_PROXY_SMTP_PORT", PROXY_SMTP_PORT),
            ("NUVRAIL_STAGING_MONITOR_API_TOKEN", MONITOR_API_TOKEN),
        )
        if not v
    ]
    if missing:
        return (
            "credentialed SMTP flow not wired yet — missing "
            + ", ".join(missing)
            + ". Needs enterprise deploy/ops wiring (proxy port exposure + "
            "monitor mailbox secret). Tracking: #18 follow-on."
        )
    return None


def check_proxy_write_approve_deliver(client: httpx.Client, c: Check) -> None:
    """End-to-end proof the DEPLOYED SMTP proxy stages a send, the approval
    API executes it against upstream, and the message actually lands:
        SMTP-via-proxy -> 250 [STAGED op_x] -> GET /operations shows pending
        -> POST /operations/{op}/approve -> direct IMAP confirms delivery
        -> cleanup (delete probe + reject any stragglers).
    """
    reason = _proxy_smtp_ready()
    if reason:
        return c.skip(reason) and None

    subject = f"nuvrail-staging-monitor-{uuid.uuid4().hex[:12]}"
    auth = {"Authorization": f"Bearer {MONITOR_API_TOKEN}"}

    op_id = asyncio.run(_smtp_stage_via_proxy(subject))

    # The op must surface as pending on the approval API.
    detail = client.get(f"{API}/operations/{op_id}", headers=auth)
    if detail.status_code != 200:
        return c.fail(f"staged op {op_id} not visible: GET /operations -> {detail.status_code}") and None
    if detail.json().get("status") not in ("pending", "staged"):
        return c.fail(f"op {op_id} unexpected status: {detail.json().get('status')}") and None

    # Approve -> proxy relays to upstream SMTP.
    appr = client.post(f"{API}/operations/{op_id}/approve", headers=auth)
    if appr.status_code not in (200, 202):
        return c.fail(f"approve {op_id} -> {appr.status_code}: {appr.text[:200]}") and None

    uid = asyncio.run(_imap_search_subject(subject))
    if uid is None:
        return c.fail(
            f"approved op {op_id} but message '{subject}' did not arrive within {FLOW_TIMEOUT:.0f}s"
        ) and None

    asyncio.run(_imap_delete_uid(uid))
    c.ok(f"SMTP stage->approve->deliver green (op={op_id}, uid={uid}); probe cleaned up")


def check_rejection_revert(client: httpx.Client, c: Check) -> None:
    """Proof the DEPLOYED IMAP proxy stages a write, a rejection reverts
    local state, and the proxy surfaces the revert (the core Nuvrail trick:
    rejection => unsolicited FETCH / reverted flags on the next command).
        deliver probe -> IMAP-proxy STORE +FLAGS \\Flagged -> [STAGED op_x]
        -> POST /operations/{op}/reject -> op status == rejected
        -> direct IMAP confirms the flag was NOT applied upstream (reverted).
    """
    reason = _proxy_smtp_ready()
    if reason:
        return c.skip(reason.replace("SMTP flow", "rejection/revert flow")) and None
    if not PROXY_IMAP_PORT:
        return c.skip(
            "rejection/revert needs NUVRAIL_STAGING_PROXY_IMAP_PORT exposed "
            "(enterprise wiring). Tracking: #18 follow-on."
        ) and None

    auth = {"Authorization": f"Bearer {MONITOR_API_TOKEN}"}
    subject = f"nuvrail-staging-reject-{uuid.uuid4().hex[:12]}"

    # Seed a message to operate on: stage a send + approve it so it exists.
    seed_op = asyncio.run(_smtp_stage_via_proxy(subject))
    seed_appr = client.post(f"{API}/operations/{seed_op}/approve", headers=auth)
    if seed_appr.status_code not in (200, 202):
        return c.fail(f"seed approve -> {seed_appr.status_code}") and None
    if asyncio.run(_imap_search_subject(subject)) is None:
        return c.fail("seed message for reject test never delivered") and None

    # Stage a flag write via the proxy, then reject it.
    op_id, uid = asyncio.run(_imap_stage_flag_via_proxy(subject))
    rej = client.post(f"{API}/operations/{op_id}/reject", headers=auth)
    if rej.status_code not in (200, 202):
        return c.fail(f"reject {op_id} -> {rej.status_code}: {rej.text[:200]}") and None

    detail = client.get(f"{API}/operations/{op_id}", headers=auth)
    if detail.status_code != 200 or detail.json().get("status") != "rejected":
        return c.fail(
            f"op {op_id} not 'rejected' after reject: "
            f"{detail.status_code} {detail.json().get('status') if detail.status_code==200 else ''}"
        ) and None

    # Ground truth: the \\Flagged flag must NOT be present upstream (reverted).
    flagged = asyncio.run(_imap_uid_has_flag(uid, "\\Flagged"))
    asyncio.run(_imap_delete_uid(uid))
    if flagged:
        return c.fail(
            f"rejected op {op_id} but \\Flagged was applied upstream on uid {uid} — revert failed"
        ) and None
    c.ok(f"IMAP stage->reject->revert green (op={op_id}, uid={uid}); flag correctly not applied")


async def _imap_uid_has_flag(uid: int, flag: str) -> bool:
    """Direct (non-proxy) IMAP: does the message carry the given flag?"""
    import aioimaplib

    host = UPSTREAM_IMAP_HOST or PROXY_HOST
    client = aioimaplib.IMAP4_SSL(host=host, port=UPSTREAM_IMAP_PORT)
    try:
        await client.wait_hello_from_server()
        await client.login(MONITOR_IMAP_USER, MONITOR_IMAP_PASSWORD)
        await client.select("INBOX")
        status, data = await client.uid("fetch", str(uid), "(FLAGS)")
        if status != "OK":
            return False
        text = b"".join(
            bytes(x) if isinstance(x, (bytes, bytearray)) else b"" for x in data
        ).decode("utf-8", "replace")
        m = re.search(r"FLAGS\s*\(([^)]*)\)", text, re.IGNORECASE)
        return bool(m and flag in m.group(1))
    finally:
        try:
            await client.logout()
        except Exception:
            pass


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
