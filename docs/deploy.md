# Nuvrail — fly.io Deployment Runbook

## Overview

Nuvrail runs two fly.io apps:

| App variable | Default name | Purpose |
|---|---|---|
| `FLY_MAIL_APP_STAGING` | `nuvrail-mail-staging` | Gateway (IMAP + SMTP + API), staging |
| `FLY_WEB_APP_STAGING` | `nuvrail-app-staging` | Web PWA, staging |
| `FLY_MAIL_APP_PROD` | `nuvrail-mail` | Gateway, production |
| `FLY_WEB_APP_PROD` | `nuvrail-app` | Web PWA, production |

Apps are deployed via the **manual** `workflow_dispatch` trigger on `.github/workflows/deploy.yml`. CI (tests + lint) runs automatically on every push to `main`.

---

## First-Time Setup (per app)

### 1. Create the fly.io apps

```bash
flyctl apps create nuvrail-mail-staging
flyctl apps create nuvrail-app-staging
# Repeat for prod apps when ready
```

### 2. Create persistent volume (gateway app only)

SQLite requires a persistent volume. Create once per app — **do not recreate** on deploy.

```bash
flyctl volumes create nuvrail_data \
  --size 1 \
  --region iad \
  --app nuvrail-mail-staging
```

### 3. Set secrets

Gateway app secrets:

```bash
flyctl secrets set \
  NUVRAIL_MASTER_KEY="<64 hex chars — run: python3 -c 'import secrets; print(secrets.token_hex(32))'>" \
  NUVRAIL_VAPID_PRIVATE="<vapid_private.pem contents>" \
  NUVRAIL_VAPID_PUBLIC="<vapid_public.pem contents>" \
  --app nuvrail-mail-staging
```

### 4. Set GitHub secrets and variables

In GitHub → Settings → Secrets and variables → Actions:

**Secrets:**
- `FLY_API_TOKEN` — from `flyctl auth token`

**Variables:**
- `FLY_MAIL_APP_STAGING` = `nuvrail-mail-staging`
- `FLY_WEB_APP_STAGING` = `nuvrail-app-staging`
- `FLY_MAIL_APP_PROD` = `nuvrail-mail`
- `FLY_WEB_APP_PROD` = `nuvrail-app`

Also configure GitHub Environments:
- Create `staging` environment (no protection rules)
- Create `production` environment (add required reviewers)

---

## Routine Deploy

All deploys are triggered manually via GitHub Actions:

1. Go to **Actions → Deploy → Run workflow**
2. Select `staging` or `production`
3. Click **Run workflow**

The workflow will:
1. Run full CI (lint + tests) — fails here if tests are broken
2. Deploy gateway and web in parallel via `flyctl deploy --remote-only`
3. Run E2E smoke tests automatically (staging only)

**Never** run `flyctl deploy` directly from a local machine against production — always go through the workflow so the CI gate fires.

---

## Rollback

```bash
# List recent releases
flyctl releases --app nuvrail-mail-staging

# Roll back to a previous release
flyctl deploy --image <image-ref-from-release-list> --app nuvrail-mail-staging
```

---

## Volume Backup

SQLite volume backup — run before any deploy that touches the DB schema:

```bash
# SSH into the machine
flyctl ssh console --app nuvrail-mail-staging

# Inside the machine:
cp /data/nuvrail.db /data/nuvrail.db.bak-$(date +%Y%m%d-%H%M%S)
exit
```

For off-instance backup, use `flyctl sftp` or the backup cron script in `scripts/backup_db.sh`.

---

## Health Check

The gateway exposes `GET /health` on port 8080. fly.io polls this every 30s. If it fails 3 times consecutively, the machine is replaced.

Manual check:
```bash
curl https://nuvrail-mail-staging.fly.dev/health
```

---

## Secrets Rotation

To rotate `NUVRAIL_MASTER_KEY` (required after any potential exposure):

1. Export current data: `GET /api/account/export` for all users
2. Re-encrypt all credentials with the new key (script in `scripts/rotate_master_key.py`)
3. Set the new key: `flyctl secrets set NUVRAIL_MASTER_KEY="<new key>" --app <app>`
4. Redeploy

⚠️ Do not set the new key before re-encrypting — the proxy will fail to decrypt existing credentials.

---

## Logs

```bash
flyctl logs --app nuvrail-mail-staging
flyctl logs --app nuvrail-mail-staging --instance <instance-id>
```

Log retention: 90 days (configured via logrotate on self-hosted; fly.io retains ~30 days of live logs — set up external log shipping for the full 90-day retention required by the privacy policy).
