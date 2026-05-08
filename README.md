# nuvrail
The approval layer between AI agents and your inbox.

---

## Deploying a test server

### What you're building

```
Internet
    │
    ├──► :80   HTTP  ──► nginx ──────────────────► redirect to HTTPS
    │
    ├──► :443  HTTPS ──► nginx ──► web :3000  ──► React approval PWA
    │                               (approval UI)    /api/ → gateway :8080
    │
    ├──► :443  HTTPS ──► nginx ──► landing :3001 ── Next.js landing page
    │                               (nuvrail.com)
    │
    ├──► :993  IMAP SSL ──► nginx stream ──► gateway :10143  IMAP proxy
    │                        (TLS termination)  (plain TCP, container)
    │
    └──► :465  SMTPS    ──► nginx stream ──► gateway :10587  SMTP proxy
                             (TLS termination)  (plain TCP, container)

Docker Compose (on the host)
    gateway   IMAP :10143  SMTP :10587  REST API :8080
    web       Approval PWA + nginx (/api/ → gateway)  :3000
    landing   Next.js landing page                    :3001
```

AI agents connect to `:993` (IMAP) and `:465` (SMTP). nginx terminates TLS and
forwards plain TCP to the proxy containers. The approval UI and API are served
over HTTPS at `test.nuvrail.com`.

---

### Prerequisites

- Ubuntu 22.04 LTS (or equivalent Debian-based distro)
- DNS `A` records pointing at the server:
  - `test.nuvrail.com` → server IP (approval UI + proxy)
  - `nuvrail.com` → same server IP (landing page)
- Packages installed:

```bash
# Docker Engine + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in after this

# nginx with stream module
sudo apt install -y nginx libnginx-mod-stream

# certbot
sudo apt install -y certbot python3-certbot-nginx
```

---

### Step 1 — Clone and configure

```bash
sudo mkdir -p /opt/nuvrail
sudo chown $USER:$USER /opt/nuvrail
git clone https://github.com/agens-field/nuvrail.git /opt/nuvrail
cd /opt/nuvrail
```

Create `.env` from the example and fill in every required value:

```bash
cp .env.example .env
nano .env
```

Critical fields:

| Variable | What to set |
|---|---|
| `NUVRAIL_MASTER_KEY` | 64 hex chars — `python3 -c "import secrets; print(secrets.token_bytes(32).hex())"` |
| `NUVRAIL_CORS_ORIGINS` | `https://test.nuvrail.com` |
| `NUVRAIL_VAPID_EMAIL` | your admin contact email |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | required for Gmail OAuth2 agent setup |
| `LOOPS_API_KEY` | waitlist form on landing page |

⚠️ `NUVRAIL_MASTER_KEY` is used to encrypt all upstream credentials stored in the
database. Back it up. Losing it makes stored agent credentials unrecoverable.

---

### Step 2 — TLS certificates

Get certificates for both domains before configuring nginx (certbot needs port 80 free):

```bash
# Temporarily allow port 80 through any firewall
sudo ufw allow 80

sudo certbot certonly --standalone \
  -d test.nuvrail.com \
  -d nuvrail.com \
  --non-interactive --agree-tos -m admin@nuvrail.com
```

Certs land at `/etc/letsencrypt/live/<domain>/`.

---

### Step 3 — nginx configuration

nginx needs two pieces: an `http` block for HTTPS termination, and a `stream`
block for TLS passthrough on the proxy ports.

#### 3a. Enable the stream module

Verify `/etc/nginx/nginx.conf` loads the stream module (Ubuntu packages do this
automatically via `/etc/nginx/modules-enabled/`). Check:

```bash
nginx -V 2>&1 | grep -- --with-stream
# should print: --with-stream --with-stream_ssl_module ...
```

If it doesn't appear, install `libnginx-mod-stream` (see Prerequisites).

Add this line at the **top level** of `/etc/nginx/nginx.conf` (outside the `http {}`
block, or in a separate include):

```nginx
# /etc/nginx/nginx.conf  — add at top level, outside http {}
stream {
    include /etc/nginx/stream.d/*.conf;
}
```

Create the include directory:

```bash
sudo mkdir -p /etc/nginx/stream.d
```

#### 3b. Stream config — IMAP and SMTP proxy ports

Create `/etc/nginx/stream.d/nuvrail-proxy.conf`:

```nginx
# /etc/nginx/stream.d/nuvrail-proxy.conf
# TLS termination for the Nuvrail IMAP and SMTP proxies.
# nginx unwraps SSL here and forwards plain TCP to the containers.

# IMAP SSL (:993) → gateway container (:10143)
server {
    listen     993 ssl;

    ssl_certificate     /etc/letsencrypt/live/test.nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.nuvrail.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL_IMAP:10m;
    ssl_session_timeout 10m;

    proxy_pass          127.0.0.1:10143;
    proxy_timeout       3600s;   # keep-alive IMAP connections can be long
    proxy_connect_timeout 5s;
}

# SMTPS (:465) → gateway container (:10587)
server {
    listen     465 ssl;

    ssl_certificate     /etc/letsencrypt/live/test.nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.nuvrail.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL_SMTP:10m;
    ssl_session_timeout 10m;

    proxy_pass          127.0.0.1:10587;
    proxy_timeout       300s;
    proxy_connect_timeout 5s;
}
```

#### 3c. HTTP block — approval UI and landing page

Create `/etc/nginx/sites-available/nuvrail`:

```nginx
# /etc/nginx/sites-available/nuvrail

# Redirect all HTTP to HTTPS
server {
    listen 80;
    server_name test.nuvrail.com nuvrail.com www.nuvrail.com;
    return 301 https://$host$request_uri;
}

# test.nuvrail.com — approval PWA
# The web container (port 3000) runs its own nginx that proxies /api/ → gateway.
server {
    listen 443 ssl;
    server_name test.nuvrail.com;

    ssl_certificate     /etc/letsencrypt/live/test.nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.nuvrail.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options    nosniff;
    add_header X-Frame-Options           DENY;
    add_header Referrer-Policy           no-referrer;

    location / {
        proxy_pass         http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }
}

# nuvrail.com — landing page
server {
    listen 443 ssl;
    server_name nuvrail.com www.nuvrail.com;

    ssl_certificate     /etc/letsencrypt/live/nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/nuvrail.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options    nosniff;
    add_header X-Frame-Options           DENY;
    add_header Referrer-Policy           no-referrer;

    location / {
        proxy_pass         http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }
}
```

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/nuvrail /etc/nginx/sites-enabled/
sudo nginx -t   # must say "syntax is ok" and "test is successful"
sudo systemctl reload nginx
```

---

### Step 4 — Docker Compose build and start

```bash
cd /opt/nuvrail

# Build all images
docker compose build

# Start everything detached
docker compose up -d

# Verify all three containers are running
docker compose ps
```

Expected output:

```
NAME                 STATUS    PORTS
nuvrail-gateway-1    Up        0.0.0.0:8080->8080/tcp  0.0.0.0:10143->10143/tcp  0.0.0.0:10587->10587/tcp
nuvrail-web-1        Up        0.0.0.0:3000->80/tcp
nuvrail-landing-1    Up        0.0.0.0:3001->3001/tcp
```

Check the gateway logs to confirm both proxies and the API started cleanly:

```bash
docker compose logs gateway --tail=30
# Look for:
#   [entrypoint] All processes started (IMAP=... SMTP=... API=...)
#   Nuvrail IMAP proxy listening on 0.0.0.0:10143
#   Nuvrail SMTP proxy listening on 0.0.0.0:10587
#   Application startup complete.
```

---

### Step 5 — First-run verification

```bash
# HTTPS — approval UI should return HTML
curl -s -o /dev/null -w "%{http_code}" https://test.nuvrail.com/
# → 200

# API health (routed through the web container's internal nginx)
curl -s https://test.nuvrail.com/api/v1/health | python3 -m json.tool
# → {"status": "ok", ...}

# IMAP SSL on :993 — expect Nuvrail greeting
openssl s_client -connect test.nuvrail.com:993 -quiet 2>/dev/null
# → * OK Nuvrail IMAP proxy ready

# SMTPS on :465 — expect Nuvrail greeting
openssl s_client -connect test.nuvrail.com:465 -quiet 2>/dev/null
# → 220 Nuvrail SMTP proxy ready

# Landing page
curl -s -o /dev/null -w "%{http_code}" https://nuvrail.com/
# → 200
```

---

### Step 6 — First-run setup (create admin account)

Use the API to create your first human user and generate your first AI agent token.
See the API reference in `docs/` or `SPEC.md` for full auth flow. Quick version:

```bash
# Register the first human admin account
curl -s -X POST https://test.nuvrail.com/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username": "martin", "password": "<strong-password>"}' | python3 -m json.tool

# Log in to get a bearer token
TOKEN=$(curl -s -X POST https://test.nuvrail.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "martin", "password": "<strong-password>"}' | python3 -m json.tool | grep '"token"' | awk -F'"' '{print $4}')

# Create an AI agent (returns a one-time agent token — copy it)
curl -s -X POST https://test.nuvrail.com/api/v1/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "test-agent"}' | python3 -m json.tool
```

---

### Redeployment (code update)

```bash
cd /opt/nuvrail
git pull

# Rebuild only what changed (gateway is the usual target)
docker compose build gateway
docker compose up -d gateway

# Or rebuild everything
docker compose build && docker compose up -d
```

The `nuvrail_data` Docker volume persists across rebuilds — your DB, master key
(if stored there), and VAPID keys survive redeployment.

---

### Operational runbook

```bash
# Service health
docker compose ps
docker compose logs gateway --tail=50 -f

# API quick checks
curl -s https://test.nuvrail.com/api/v1/health
curl -s https://test.nuvrail.com/api/v1/operations?status=pending | python3 -m json.tool

# Approve / reject an operation
curl -s -X POST https://test.nuvrail.com/api/v1/operations/op_XXXXXX/approve \
  -H "Authorization: Bearer $TOKEN"

# Inspect the database directly
docker compose exec gateway sqlite3 /data/nuvrail.db \
  "SELECT id, op_type, status, created_at FROM staged_operations ORDER BY created_at DESC LIMIT 20;"

# Renew TLS certs (runs automatically via certbot timer; manual if needed)
sudo certbot renew --dry-run   # test
sudo certbot renew             # real run, then:
sudo systemctl reload nginx
```

---

### Firewall rules

```bash
sudo ufw allow 22    # SSH
sudo ufw allow 80    # HTTP (redirect only)
sudo ufw allow 443   # HTTPS (approval UI + landing)
sudo ufw allow 993   # IMAP SSL (AI agent)
sudo ufw allow 465   # SMTPS  (AI agent)
sudo ufw enable

# Do NOT expose 10143, 10587, or 8080 publicly —
# those are internal between nginx and Docker.
```

---

> **Legacy doc:** `docs/deployment-test-server.md` describes the old bare-metal /
> systemd deployment approach. Superseded by this Docker Compose guide.
