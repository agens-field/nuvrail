# Nuvrail

**The approval layer between AI agents and your inbox.**

AI agents that can send email are useful. AI agents that can send email *without any human check* are a liability. Nuvrail sits between your AI agent and your real mail server. The agent sees a normal IMAP/SMTP server. Every write — every move, delete, flag change, and outbound send — is staged for your approval before it touches the real mailbox. Reads pass through instantly; nothing blocks the agent from working. Only destructive or irreversible actions require a human to say yes.

---

## How it works

```
  AI agent                  Nuvrail                   Real mail server
  ────────     IMAP/SMTP     ───────     IMAP/SMTP     ────────────────
  (Claude,  ──────────────► gateway ──────────────►  (Gmail, iCloud,
   Cursor,   reads: pass       │       on approval    Outlook, or any
   custom)   through           │       only           IMAP/SMTP server)
             instantly         │
                         writes: staged
                         → human reviews
                           & approves
```

1. **Agent writes a command** — e.g. `UID MOVE 42 Trash` or sends an email via SMTP.
2. **Gateway intercepts it** — returns `OK [STAGED]` immediately so the agent can keep working.
3. **You get a notification** — a push notification on your phone or browser shows you exactly what the agent wants to do, with the subject, sender, and destination.
4. **You approve or reject** — one tap. On approval the operation executes against the real server. On rejection the proxy reverts its local state and silently surfaces the rejection to the agent on its next command.

Nothing is deleted. Ever. `EXPUNGE` is permanently blocked at the gateway layer — the worst the agent can do is move a message to Trash, and even that requires your sign-off.

---

## Architecture

```
Internet
    │
    ├──► :993  IMAPS  ──► nginx (TLS) ──► IMAP proxy (10143)  ──► upstream IMAP
    ├──► :465  SMTPS  ──► nginx (TLS) ──► SMTP proxy (10587)  ──► upstream SMTP
    └──► :443  HTTPS  ──► nginx (TLS) ──► Web PWA + REST API (3000/8080)

All external TLS terminates at nginx. Docker containers are bound to 127.0.0.1 only.
No plain-text port is reachable from outside the host.
```

**Components:**
- **IMAP proxy** (`gateway/proxy.py`) — asyncio IMAP4rev1 state machine. Intercepts writes, passes reads, maintains a local SQLite mirror for optimistic consistency.
- **SMTP proxy** (`gateway/smtp_proxy.py`) — asyncio. Intercepts `DATA` (outbound sends), stages them. Relays on approval.
- **Staging engine** (`gateway/staging.py`) — assigns op IDs, stores snapshots for revert, tracks 48-hour expiry.
- **REST API** (`api/`) — FastAPI. Approve/reject operations, manage agents, audit log, push subscriptions.
- **Web PWA** (`web/`) — React 18 + Vite + TypeScript + Tailwind. Login, pending operations, audit log, agent management. Installable as a PWA on iOS/Android.

**Key design constraints:**
- Standard IMAP/SMTP on both ends — no agent modifications required
- EXPUNGE permanently blocked; `\Deleted` rewrites to a staged move-to-Trash
- AES-256-GCM for all credentials at rest; TLS everywhere in transit
- No plaintext email metadata in push payloads
- Immutable audit log — every action is recorded forever

---

## Deploying

### Prerequisites

- Ubuntu 22.04 LTS (or equivalent Debian-based distro)
- DNS `A` records pointing at your server:
  - `test.nuvrail.com` → server IP (approval UI + proxy)
  - `nuvrail.com` → same IP (landing page, optional)
- Git, Docker Engine + Compose plugin, nginx with stream module, certbot

```bash
# Docker Engine
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in

# nginx with stream module + certbot
sudo apt install -y nginx libnginx-mod-stream certbot python3-certbot-nginx
```

---

### Step 1 — Clone and configure

```bash
sudo mkdir -p /opt/nuvrail && sudo chown $USER:$USER /opt/nuvrail
git clone https://github.com/agens-field/nuvrail.git /opt/nuvrail
cd /opt/nuvrail
cp .env.example .env
nano .env
```

Required environment variables:

| Variable | Description |
|---|---|
| `NUVRAIL_MASTER_KEY` | 64 hex chars — `python3 -c "import secrets; print(secrets.token_bytes(32).hex())"` |
| `NUVRAIL_CORS_ORIGINS` | `https://test.nuvrail.com` |
| `NUVRAIL_VAPID_EMAIL` | Admin contact email, e.g. `mailto:you@yourdomain.com` |
| `GOOGLE_CLIENT_ID` | GCP OAuth2 client ID (required for Gmail agent setup) |
| `GOOGLE_CLIENT_SECRET` | GCP OAuth2 client secret (required for Gmail agent setup) |
| `GOOGLE_REDIRECT_URI` | `https://test.nuvrail.com/api/v1/oauth2/google/callback` |

> ⚠️ **Back up `NUVRAIL_MASTER_KEY`.** It encrypts every upstream credential in the
> database. Losing it makes all stored agent credentials unrecoverable.

---

### Step 2 — TLS certificates

```bash
sudo ufw allow 80  # temporarily for certbot
sudo certbot certonly --standalone \
  -d test.nuvrail.com -d nuvrail.com \
  --non-interactive --agree-tos -m you@yourdomain.com
```

---

### Step 3 — nginx

#### Enable the stream module

Add at the **top level** of `/etc/nginx/nginx.conf` (outside `http {}`):

```nginx
stream {
    include /etc/nginx/stream.d/*.conf;
}
```

```bash
sudo mkdir -p /etc/nginx/stream.d
```

#### `/etc/nginx/stream.d/nuvrail-proxy.conf`

```nginx
# IMAP SSL (:993) → gateway container (:10143)
server {
    listen 993 ssl;
    ssl_certificate     /etc/letsencrypt/live/test.nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.nuvrail.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    proxy_pass          127.0.0.1:10143;
    proxy_timeout       3600s;
    proxy_connect_timeout 5s;
}

# SMTPS (:465) → gateway container (:10587)
server {
    listen 465 ssl;
    ssl_certificate     /etc/letsencrypt/live/test.nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.nuvrail.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    proxy_pass          127.0.0.1:10587;
    proxy_timeout       300s;
    proxy_connect_timeout 5s;
}
```

#### `/etc/nginx/sites-available/nuvrail`

```nginx
server {
    listen 80;
    server_name test.nuvrail.com nuvrail.com www.nuvrail.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name test.nuvrail.com;
    ssl_certificate     /etc/letsencrypt/live/test.nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.nuvrail.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header Referrer-Policy no-referrer;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 443 ssl;
    server_name nuvrail.com www.nuvrail.com;
    ssl_certificate     /etc/letsencrypt/live/nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/nuvrail.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;
    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/nuvrail /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

### Step 4 — Build and start

```bash
cd /opt/nuvrail
docker compose build
docker compose up -d
docker compose ps
```

Check the gateway logs to confirm all three services started:

```bash
docker compose logs gateway --tail=30
# Look for:
#   Nuvrail IMAP proxy listening on 0.0.0.0:10143
#   Nuvrail SMTP proxy listening on 0.0.0.0:10587
#   Application startup complete.
```

---

### Step 5 — Verify the installation

```bash
# Approval UI
curl -s -o /dev/null -w "%{http_code}" https://test.nuvrail.com/
# → 200

# API health
curl -s https://test.nuvrail.com/api/v1/health | python3 -m json.tool
# → {"status": "ok", ...}

# IMAP proxy (should see: * OK Nuvrail IMAP proxy ready)
openssl s_client -connect test.nuvrail.com:993 -quiet 2>/dev/null

# SMTP proxy (should see: 220 Nuvrail SMTP proxy ready)
openssl s_client -connect test.nuvrail.com:465 -quiet 2>/dev/null
```

---

### Step 6 — Create your account

Open `https://test.nuvrail.com` in your browser and complete the first-run setup, or use the API directly:

```bash
# Register your account
curl -s -X POST https://test.nuvrail.com/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@yourdomain.com", "password": "your-strong-password", "display_name": "Your Name"}' \
  | python3 -m json.tool

# Log in to get a bearer token
TOKEN=$(curl -s -X POST https://test.nuvrail.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@yourdomain.com", "password": "your-strong-password"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
```

---

## Connecting an email account

Nuvrail supports Gmail (OAuth2), standard IMAP/SMTP, and more providers coming. Each connected account gets a unique agent credential — the username and token you give to your AI agent.

### Gmail via OAuth2 (recommended)

1. Go to `https://test.nuvrail.com` → **Agents** → **Connect Gmail**
2. Complete the Google OAuth2 consent flow
3. Copy the one-time agent token that appears on success — you won't see it again

### Standard IMAP/SMTP

```bash
curl -s -X POST https://test.nuvrail.com/api/v1/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "label": "my-work-email",
    "upstream_host": "imap.yourdomain.com",
    "upstream_smtp_host": "smtp.yourdomain.com",
    "upstream_imap_port": 993,
    "upstream_smtp_port": 587,
    "upstream_user": "you@yourdomain.com",
    "upstream_password": "your-email-password"
  }' | python3 -m json.tool
# Returns agent_username + agent_token (one-time — copy it now)
```

---

## Configuring your AI agent

Point any IMAP/SMTP client at the Nuvrail proxy using the agent credentials. The agent doesn't need to know Nuvrail is there.

| Setting | Value |
|---|---|
| **IMAP host** | `test.nuvrail.com` |
| **IMAP port** | `993` (SSL) |
| **SMTP host** | `test.nuvrail.com` |
| **SMTP port** | `465` (SSL) |
| **Username** | agent username from setup (e.g. `nuvrail_abc123`) |
| **Password** | agent token from setup (one-time, copy it at creation) |

### Example: Claude / MCP email tool

```json
{
  "imap": {
    "host": "test.nuvrail.com",
    "port": 993,
    "ssl": true,
    "username": "nuvrail_abc123",
    "password": "your-agent-token"
  },
  "smtp": {
    "host": "test.nuvrail.com",
    "port": 465,
    "ssl": true,
    "username": "nuvrail_abc123",
    "password": "your-agent-token"
  }
}
```

**What the agent can do without approval:**
- Read any message (`FETCH`)
- Search (`SEARCH`, `UID SEARCH`)
- List folders (`LIST`, `LSUB`)
- Check status (`STATUS`, `NOOP`)

**What requires your approval:**
- Move or copy messages (`UID MOVE`, `UID COPY`)
- Flag changes (`UID STORE`)
- Send outbound email (SMTP `DATA`)
- Create or rename folders

**Permanently blocked:**
- `EXPUNGE` — the gateway never allows permanent deletion

---

## Approving operations

When an agent stages an operation:

1. **Push notification** arrives on your phone/browser (requires enabling notifications on the PWA)
2. Open the approval UI at `https://test.nuvrail.com`
3. Each pending operation shows: type, subject, sender, destination folder or recipient
4. Tap **Approve** to execute against the real mail server, or **Reject** to revert

Operations expire after 48 hours if not acted on — the agent receives a rejection and the state reverts.

You can also approve/reject via the API:

```bash
# List pending operations
curl -s https://test.nuvrail.com/api/v1/operations?status=pending \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Approve
curl -s -X POST https://test.nuvrail.com/api/v1/operations/op_XXXXXX/approve \
  -H "Authorization: Bearer $TOKEN"

# Reject
curl -s -X POST https://test.nuvrail.com/api/v1/operations/op_XXXXXX/reject \
  -H "Authorization: Bearer $TOKEN"
```

---

## Development setup

```bash
git clone https://github.com/agens-field/nuvrail.git
cd nuvrail

# Python dependencies (requires Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Run the gateway locally (needs a .env with NUVRAIL_MASTER_KEY set)
cp .env.example .env
python3 -m gateway.proxy    # IMAP proxy on :10143
python3 -m gateway.smtp_proxy   # SMTP proxy on :10587
uvicorn api.main:app --port 8080    # REST API

# Web UI
cd web
npm install
npm run dev     # Vite dev server on :5173
```

---

## Operations

### Updating the deployment

```bash
cd /opt/nuvrail
git pull
docker compose build gateway
docker compose up -d gateway
```

The `nuvrail_data` Docker volume persists across rebuilds — your database, VAPID keys, and (if stored there) the master key all survive redeployment.

### Useful commands

```bash
# Container status
docker compose ps

# Follow gateway logs
docker compose logs gateway -f --tail=50

# Inspect pending operations in the DB directly
docker compose exec gateway python3 -c "
import asyncio
from pathlib import Path
from gateway.state_db import get_db
async def main():
    async with get_db(Path('/data/nuvrail.db')) as db:
        async with db.execute(
            'SELECT id, op_type, status, folder_from, created_at '
            'FROM staged_operations ORDER BY created_at DESC LIMIT 10'
        ) as c:
            for row in await c.fetchall():
                print(dict(row))
asyncio.run(main())
"

# Check OAuth2 token health for an agent
docker compose exec gateway python3 scripts/check_oauth2_token.py nuvrail_XXXXXXXX

# Test IMAP connectivity directly (bypasses proxy)
docker compose exec gateway python3 scripts/test_imap_direct.py nuvrail_XXXXXXXX

# Renew TLS certificates
sudo certbot renew && sudo systemctl reload nginx
```

### Firewall

```bash
sudo ufw allow 22    # SSH
sudo ufw allow 80    # HTTP → HTTPS redirect
sudo ufw allow 443   # HTTPS: approval UI + API + landing
sudo ufw allow 993   # IMAP SSL: AI agent connects here
sudo ufw allow 465   # SMTPS: AI agent connects here
sudo ufw enable

# Do NOT expose ports 10143, 10587, or 8080 to the internet —
# these are internal Docker ports, accessed only via nginx.
```

---

## Security model

- **Credentials at rest:** all upstream passwords and OAuth2 tokens are encrypted with AES-256-GCM using `NUVRAIL_MASTER_KEY`. The key never leaves the host.
- **TLS everywhere:** all external connections use TLS. The internal Docker network uses plain TCP between nginx and the proxy containers — nginx terminates TLS before handing off.
- **No EXPUNGE:** permanent deletion is blocked at the protocol level, unconditionally. The gateway cannot be configured to allow it.
- **Per-agent isolation:** each AI agent has its own credentials and can only access the upstream account it was created for. There is no path for one agent to affect another agent's mailbox.
- **Audit log:** every staged operation, approval, rejection, and execution is recorded in the database with a timestamp. The log is append-only.
- **Rate limiting:** repeated authentication failures from a single IP trigger a lockout.
- **Push payload privacy:** push notifications contain only the operation ID and urgency flag — no email subject, sender, or body in the push payload itself. The full detail is fetched over authenticated HTTPS when you open the app.

---

## License

MIT. See [LICENSE](LICENSE).
