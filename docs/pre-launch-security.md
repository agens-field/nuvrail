# Pre-Launch Security Review
_Completed: 2026-05-20 · Reviewer: KC (CTO) · Due: 2026-06-12_

---

## Summary

| Item | Status | Action |
|---|---|---|
| 1. Secrets scan | ✅ PASS | No credentials in tracked files |
| 2. Dependency audit | ✅ PASS | Fixed — 0 known vulns after remediation |
| 3. Rate limiting (proxy) | ✅ PASS | Auth abuse protectors in place |
| 4. Rate limiting (API) | ⚠️ GAP | No rate limiting on FastAPI auth endpoints — see below |
| 5. Input validation | ✅ PASS | No injection risks found |
| 6. CORS | ⚠️ LAUNCH BLOCKER | Defaults to `*` — must set env var before launch (GitHub #19) |
| 7. Auth token rotation | ⚠️ ACTION REQUIRED | `NUVRAIL_MASTER_KEY` must differ between staging and production |
| 8. Network exposure | ✅ PASS | All ports bound to 127.0.0.1; nginx handles TLS termination |

---

## 1. Secrets Scan

**Method:** Manual scan of tracked files + `.gitignore` review.

**Findings:**
- `.gitignore` covers `.env`, `*.pem`, `*.key`, `client_secret.json` ✅
- No hardcoded credentials, API keys, or tokens found in tracked files ✅
- The May 2026 incident (`master.key` + `vapid_private.pem` accidentally pushed to public repo) was fully remediated: `git-filter-repo` purged the files from history, keys were rotated on the server, and `.gitignore` was updated to block future accidents ✅

**Recommendation:**  
Run `gitleaks` or `trufflehog` as a pre-push git hook before public GitHub exposure of the agens-field repos. Neither tool is installed in the dev environment yet. GitHub #23 tracks this work.

---

## 2. Dependency Audit

**Method:** `pip-audit`, `npm audit`

### Python (pip-audit) — after remediation: 0 known vulnerabilities

Vulnerabilities found and resolved:

| Package | CVE / ID | Fix Applied |
|---|---|---|
| `python-jose 3.5.0` | PYSEC-2025-185 | **Removed from pyproject.toml** — was listed as a dependency but never imported anywhere in the codebase. Orphaned dep. |
| `urllib3 2.6.3` | PYSEC-2026-141, PYSEC-2026-142 | **Upgraded to 2.7.0** (transitive via httpx) |
| `idna 3.13` | CVE-2026-45409 | **Upgraded to 3.15** (transitive dep) |

**Status:** `pip-audit` now reports 0 known vulnerabilities. ✅

### npm (build deps) — 8 high, 2 moderate

All 8 high-severity findings are in **build-time tools only** (Vite, Babel, rollup, workbox-build, serialize-javascript). None of these are shipped in the production JS bundle served to users — they are dev dependencies used only during `npm run build`.

| Risk assessment | Low — build tools run in a controlled CI environment, not exposed to untrusted input at runtime |
|---|---|

**Action:** Accept for Phase 0 launch. Create a scheduled issue to run `npm update` and pin build dep versions before Phase 2 public beta.

---

## 3. Rate Limiting — Proxy Layer

**Method:** Code review of `gateway/security_controls.py`

**Findings:**

Both the IMAP and SMTP proxies use `AuthAbuseProtector` instances:
- `IMAP_AUTH_ABUSE_PROTECTOR` — exponential backoff on failed auth attempts per IP + per account
- `SMTP_AUTH_ABUSE_PROTECTOR` — same

Behaviour:
- 5 failed attempts within window → lockout + BYE
- Lockout returns `NO [LIMIT]` with retry-after seconds
- Successful auth resets the counter

**Status:** ✅ PASS — proxy layer has brute-force protection.

---

## 4. Rate Limiting — API Layer (GAP)

**Method:** Code review of `api/main.py`, `api/routes/auth.py`

**Findings:**

The FastAPI API has **no rate limiting middleware**. The following endpoints are unauthenticated or weakly authenticated and have no request throttling:

| Endpoint | Risk |
|---|---|
| `POST /api/v1/auth/login` | Password brute-force |
| `POST /api/v1/auth/register` | Account creation spam |
| `POST /api/v1/auth/password-reset/request` | Email enumeration / spam |
| `POST /api/v1/auth/password-reset/confirm` | Token brute-force |

**Recommendation (pre-launch):**  
Add `slowapi` rate limiting to the FastAPI app:
```python
# api/main.py
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# On auth routes:
@limiter.limit("10/minute")
async def login(...): ...
```

This is Phase 0 with a single known user — immediate exploitation risk is low. However, the API will be publicly reachable at `test.nuvrail.com` and should have at minimum IP-based throttling on auth endpoints before wider exposure.

**Filed as a launch-gate item** — must be addressed before public launch.

---

## 5. Input Validation

**Method:** Code review of `api/routes/` — injection risk scan.

**Findings:**

- All parameterized SQL uses `aiosqlite` with `?` placeholders — no f-string SQL construction using user input ✅
- The `PATCH /rules/{id}` and `PATCH /agents/{id}` endpoints build dynamic `SET col = ?` clauses using Pydantic `model_dump(exclude_unset=True)` — the column names come from Pydantic field definitions, not from user input ✅
- Agent-submitted operation payloads (IMAP command, SMTP envelope) are stored as TEXT/JSON blobs and never executed as queries ✅
- `json_extract` in SQLite queries uses validated integer rule IDs, not user strings ✅

`noqa: S608` suppressions present in `api/routes/operations.py` and `api/routes/audit.py` are for dynamic `WHERE` clause construction — reviewed individually, all safe (column names are string literals, not user input).

**Status:** ✅ PASS — No injection risks found.

---

## 6. CORS (Launch Blocker)

**Method:** Code review of `api/main.py`

**Findings:**

```python
# api/main.py (current)
_CORS_ORIGINS_RAW = os.environ.get("NUVRAIL_CORS_ORIGINS", "")
if _CORS_ORIGINS_RAW.strip():
    _CORS_ORIGINS = [...]
else:
    # WARNING logged but...
    _CORS_ORIGINS = ["*"]   # ← wildcard default
```

Additionally: `allow_methods=["*"]` and `allow_headers=["*"]` are more permissive than necessary.

**Required before launch:**

1. Set `NUVRAIL_CORS_ORIGINS=https://nuvrail.com` in fly.io production secrets
2. Consider tightening to specific methods and headers:
   ```python
   allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
   allow_headers=["Authorization", "Content-Type"]
   ```

**GitHub:** #19 (opened 2026-05-20)  
**Status:** ⚠️ LAUNCH BLOCKER — must set env var before production goes live.

---

## 7. Auth Token Rotation

**Pre-launch checklist:**

| Secret | Action Required |
|---|---|
| `NUVRAIL_MASTER_KEY` | Generate a fresh key for production (never reuse the staging key). Staging key was rotated 2026-05-07 after the incident. Production key must be independently generated. |
| `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY` | Rotated 2026-05-07. Production should use a separate VAPID keypair from staging. |
| GitHub PAT (KC, `kc@nuvrail.com`) | Scoped to agens-field org. Review expiry before launch; create a machine-user token for CI if GitHub Actions are wired. |
| Agent tokens | All production agent credentials will be fresh (no carryover from staging). ✅ |
| Human API bearer tokens | SHA-256 hashed at rest since commit `cf07b3d`. Plaintext shown to user once only. ✅ |

**Status:** ⚠️ ACTION REQUIRED — `NUVRAIL_MASTER_KEY` rotation for production is a human task (Martin). Add to launch checklist.

---

## 8. Network Exposure

**Method:** Review of `docker-compose.yml`, nginx config

**Findings:**

All Docker Compose port bindings are `127.0.0.1`-scoped (localhost only):

```yaml
"127.0.0.1:10143:10143"  # IMAP proxy — not public
"127.0.0.1:10587:10587"  # SMTP proxy — not public
"127.0.0.1:8080:8080"    # REST API — not public
"127.0.0.1:3000:80"      # Web app — not public
```

External access routes through nginx:
- Port 993 → 10143 (IMAP SSL via nginx stream)
- Port 465 → 10587 (SMTPS via nginx stream)
- Port 443 → web + API (HTTPS via nginx)
- Port 80 → redirects to 443

The IMAP and SMTP proxies are **not** directly reachable from the public internet — they sit behind nginx TLS termination. ✅

**fly.io (production):** Confirm that only ports 993, 465, and 443 are exposed in `fly.toml` when the agens-field server deployment is configured.

**Status:** ✅ PASS on current staging. Confirm on production deployment.

---

## Outstanding Actions (Pre-Launch Gate)

| Priority | Item | Owner | GitHub | Due |
|---|---|---|---|---|
| 🔴 BLOCKER | Set `NUVRAIL_CORS_ORIGINS` in production secrets | Martin | #19 | Before launch |
| 🟠 HIGH | Add `slowapi` rate limiting to API auth endpoints | KC | #20 | June 12 |
| 🟠 HIGH | Rotate `NUVRAIL_MASTER_KEY` and VAPID keys for production | Martin | #21 | Before launch |
| 🟡 MEDIUM | Add `gitleaks` pre-push hook to both repos | KC | #23 | Before public repo exposure |
| 🟢 LOW | Run `npm update` on build deps to clear audit findings | KC | #22 | Before Phase 2 beta |
