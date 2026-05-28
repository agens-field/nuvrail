# Nuvrail Staging Environment — Runbook

## Apps

| App | fly.io name | toml file | Purpose |
|---|---|---|---|
| Gateway | `nuvrail-staging-gateway` | `fly.staging-gateway.toml` | IMAP proxy + SMTP proxy + REST API |
| Web | `nuvrail-staging-web` | `web/fly.staging-web.toml` | React approval PWA |

## How Isolation Is Enforced

```
┌─────────────────────────────────────────────────────────┐
│ STAGING                                                   │
│                                                          │
│  nuvrail-staging-web  ──HTTPS──►  nuvrail-staging-gateway│
│  (VITE_API_URL baked                (NUVRAIL_CORS_ORIGINS │
│   at build time)                     = staging-web only)  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ PRODUCTION (separate apps, separate secrets, no overlap) │
│                                                          │
│  nuvrail-app  ──HTTPS──►  nuvrail-mail                   │
└─────────────────────────────────────────────────────────┘
```

Two hard boundaries prevent cross-environment traffic:

1. **`VITE_API_URL` is baked into the web build.** The web app cannot be redirected to production at runtime — a rebuild with different build args is required. `web/fly.staging-web.toml` hardcodes `nuvrail-staging-gateway.fly.dev`.

2. **`NUVRAIL_CORS_ORIGINS` on the gateway** is set to `https://nuvrail-staging-web.fly.dev` only. A request from any other origin (including production's web app) gets a CORS rejection.

3. **Secrets are staging-specific.** `NUVRAIL_MASTER_KEY` and VAPID keys are generated fresh for staging — never copied from production. Credentials encrypted under the staging key cannot be decrypted by production.

---

## First-Time Setup

### Prerequisites

```bash
# Install flyctl if you haven't already
brew install flyctl        # macOS
# or: curl -L https://fly.io/install.sh | sh

flyctl auth login
```

### Step 1 — Create the apps

```bash
flyctl apps create nuvrail-staging-gateway
flyctl apps create nuvrail-staging-web
```

### Step 2 — Create the persistent volume (gateway only)

SQLite needs a persistent volume. Create it once — **never recreate on redeploy** or you will lose the DB.

```bash
flyctl volumes create nuvrail_staging_data \
  --size 1 \
  --region iad \
  --app nuvrail-staging-gateway
```

Verify:

```bash
flyctl volumes list --app nuvrail-staging-gateway
# Should show: nuvrail_staging_data  iad  1GB  attached
```

### Step 3 — Generate staging-specific secrets

⚠️ Generate fresh values — do NOT copy from production.

```bash
# NUVRAIL_MASTER_KEY — 32 random bytes as hex (64 chars)
python3 -c "import secrets; print(secrets.token_hex(32))"

# VAPID keypair
# Install py-vapid if needed: pip install py-vapid
python3 -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
v.save_key('staging_vapid_private.pem')
v.save_public_key('staging_vapid_public.pem')
print('Keys written to staging_vapid_*.pem')
"
```

### Step 4 — Set secrets on the gateway app

```bash
flyctl secrets set \
  NUVRAIL_MASTER_KEY="<64 hex chars from step 3>" \
  NUVRAIL_VAPID_PRIVATE="$(cat staging_vapid_private.pem)" \
  NUVRAIL_VAPID_PUBLIC="$(cat staging_vapid_public.pem)" \
  --app nuvrail-staging-gateway

# LOOPS_API_KEY is optional — omit on staging unless you need transactional email
```

Clean up the temp key files after setting:

```bash
rm staging_vapid_private.pem staging_vapid_public.pem
```

### Step 5 — Deploy the gateway

```bash
# From the repo root:
flyctl deploy \
  --app nuvrail-staging-gateway \
  --config fly.staging-gateway.toml \
  --remote-only \
  --wait-timeout 300
```

Verify it's up:

```bash
curl https://nuvrail-staging-gateway.fly.dev/health
# Expected: {"status":"ok","timestamp":"..."}
```

### Step 6 — Deploy the web app

```bash
# From the repo root:
flyctl deploy \
  --app nuvrail-staging-web \
  --config web/fly.staging-web.toml \
  --remote-only \
  --wait-timeout 300
```

Verify:

```bash
curl -I https://nuvrail-staging-web.fly.dev
# Expected: HTTP/2 200
```

### Step 7 — Verify isolation

Confirm the web app is talking to staging gateway, not production:

```bash
# Open the web app in a browser and check DevTools → Network.
# All /api/v1/... requests should go to nuvrail-staging-gateway.fly.dev.

# Confirm CORS rejects a request from a fake origin:
curl -s -H "Origin: https://nuvrail-app.fly.dev" \
  https://nuvrail-staging-gateway.fly.dev/api/v1/auth/login \
  -I | grep -i "access-control-allow-origin"
# Expected: no allow-origin header (rejected) or missing entirely
```

---

## Routine Redeployment

After pushing changes to `main`, redeploy staging manually:

```bash
# Gateway (from repo root):
flyctl deploy \
  --app nuvrail-staging-gateway \
  --config fly.staging-gateway.toml \
  --remote-only

# Web:
flyctl deploy \
  --app nuvrail-staging-web \
  --config web/fly.staging-web.toml \
  --remote-only
```

Or trigger via GitHub Actions → Deploy → Run workflow → staging (CI gate fires automatically).

> Note: if you're using the GitHub Actions workflow, add `FLY_MAIL_APP_STAGING` / `FLY_WEB_APP_STAGING` variables pointing at the new app names and update `deploy.yml` to pass `--config fly.staging-gateway.toml` / `--config web/fly.staging-web.toml`.

---

## Logs

```bash
flyctl logs --app nuvrail-staging-gateway
flyctl logs --app nuvrail-staging-web
```

## SSH into the gateway machine

```bash
flyctl ssh console --app nuvrail-staging-gateway
```

## DB backup (before schema-touching deploys)

```bash
flyctl ssh console --app nuvrail-staging-gateway
# Inside:
cp /data/nuvrail.db /data/nuvrail.db.bak-$(date +%Y%m%d-%H%M%S)
exit
```

## Rollback

```bash
flyctl releases --app nuvrail-staging-gateway
flyctl deploy --image <image-ref> --app nuvrail-staging-gateway
```

## Secrets rotation

```bash
# Generate new value, then:
flyctl secrets set NUVRAIL_MASTER_KEY="<new 64 hex chars>" \
  --app nuvrail-staging-gateway
# A redeploy is triggered automatically by flyctl secrets set.
```

---

## Relationship to Production

| | Staging | Production |
|---|---|---|
| Gateway app | `nuvrail-staging-gateway` | `nuvrail-mail` |
| Web app | `nuvrail-staging-web` | `nuvrail-app` |
| Gateway toml | `fly.staging-gateway.toml` | `fly.gateway.toml` |
| Web toml | `web/fly.staging-web.toml` | `web/fly.toml` |
| MASTER_KEY | staging-only, never shared | production-only |
| VAPID keys | staging-only, never shared | production-only |
| CORS origin | `nuvrail-staging-web.fly.dev` | `nuvrail-app.fly.dev` |
| SQLite volume | `nuvrail_staging_data` | `nuvrail_data` |

The two environments share no secrets, no volumes, and no runtime configuration that could cause cross-talk.
