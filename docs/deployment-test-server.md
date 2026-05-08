# Nuvrail — Test Server Deployment Guide

> ⚠️ **Superseded.** This document describes the old bare-metal / systemd deployment.
> The current approach uses Docker Compose. See **[README.md](../README.md)** for the
> up-to-date guide (nginx TLS stream for proxy ports, Docker Compose for services).

**Audience:** Developer setting up a test instance on a Linux server with nginx already running.  
**Assumes:** Ubuntu 22.04 LTS, Python 3.11+, nginx installed, a domain or subdomain pointed at the server (e.g. `test.nuvrail.com`), a working MXrouting account in `.env`.

---

## Overview

```
Internet
    │
    ▼
nginx (:443 TLS)
    │
    ├──► /api/v1/*  ──►  Nuvrail API  (uvicorn :8080, internal only)
    │
    ▼
[Direct browser access to approval UI — later milestone]

AI Agent (on same or remote machine)
    │
    ├──► IMAP  ──►  nuvrail-imap-proxy  (:10143, localhost)
    └──► SMTP  ──►  nuvrail-smtp-proxy  (:10587, localhost)
         │
         └──► MXrouting upstream (blizzard.mxrouting.net)
```

The proxies listen on localhost only. The REST API is exposed via nginx with TLS. The AI agent connects to the proxies directly (port-forwarded if remote, or from localhost if co-located).

---

## Step 1 — Clone and install

```bash
# As a non-root user (e.g. 'nuvrail')
git clone https://github.com/AnimalHorde/nuvrail.git
cd nuvrail

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools
pip install -e ".[dev]"
```

Verify:
```bash
python -c "import gateway.proxy, gateway.smtp_proxy, api.main; print('OK')"
```

---

## Step 2 — Create `.env`

```bash
cp .env.example .env
nano .env
```

Fill in:

```dotenv
# Where Nuvrail stores its SQLite DB
NUVRAIL_DATA_DIR=/var/lib/nuvrail

# Upstream IMAP (the real mail server behind the proxy)
NUVRAIL_TEST_IMAP_HOST=blizzard.mxrouting.net
NUVRAIL_TEST_IMAP_PORT=993
NUVRAIL_TEST_IMAP_USER=testing@nuvrail.com
NUVRAIL_TEST_IMAP_PASS=<your-password>

# Upstream SMTP
NUVRAIL_TEST_SMTP_HOST=blizzard.mxrouting.net
NUVRAIL_TEST_SMTP_PORT=587
NUVRAIL_TEST_SMTP_USER=testing@nuvrail.com
NUVRAIL_TEST_SMTP_PASS=<your-password>

# Proxy listen addresses (keep on localhost — nginx handles external TLS)
NUVRAIL_PROXY_HOST=127.0.0.1
NUVRAIL_PROXY_IMAP_PORT=10143
NUVRAIL_PROXY_SMTP_PORT=10587
NUVRAIL_PROXY_API_URL=http://127.0.0.1:8080

LOG_LEVEL=info
```

Optional auth abuse controls (defaults are conservative if omitted):

```dotenv
NUVRAIL_AUTH_ATTEMPT_WINDOW_SECONDS=60
NUVRAIL_AUTH_MAX_ATTEMPTS_PER_IP=20
NUVRAIL_AUTH_MAX_ATTEMPTS_PER_ACCOUNT=10
NUVRAIL_AUTH_FAILURE_WINDOW_SECONDS=300
NUVRAIL_AUTH_MAX_FAILURES_BEFORE_LOCKOUT=5
NUVRAIL_AUTH_BASE_LOCKOUT_SECONDS=120
NUVRAIL_AUTH_MAX_LOCKOUT_SECONDS=3600
```

Create the data directory:
```bash
sudo mkdir -p /var/lib/nuvrail
sudo chown $USER:$USER /var/lib/nuvrail
```

---

## Step 3 — Smoke-test the proxies manually

Before setting up services, confirm everything works:

```bash
# Terminal 1 — start IMAP proxy
source .venv/bin/activate
PYTHONPATH=. python -m gateway.proxy
# → Nuvrail IMAP proxy listening on 127.0.0.1:10143 → blizzard.mxrouting.net:993

# Terminal 2 — test it
python3 -c "
import imaplib
c = imaplib.IMAP4('127.0.0.1', 10143)
print(c.login('testing@nuvrail.com', '<your-password>'))
print(c.select('INBOX'))
c.logout()
"
# → ('OK', [b'Logged in'])  and  ('OK', [...])
```

```bash
# Terminal 1 — start SMTP proxy
PYTHONPATH=. python -m gateway.smtp_proxy
# → Nuvrail SMTP proxy listening on 127.0.0.1:10587 → blizzard.mxrouting.net:587

# Terminal 2 — test it (sends to itself, returns STAGED — no actual send yet)
python3 -c "
import smtplib, base64
c = smtplib.SMTP('127.0.0.1', 10587)
c.ehlo('test')
creds = base64.b64encode(b'\x00testing@nuvrail.com\x00<your-password>').decode()
print(c.docmd('AUTH PLAIN', creds))
print(c.docmd('MAIL FROM', '<testing@nuvrail.com>'))
print(c.docmd('RCPT TO', '<testing@nuvrail.com>'))
print(c.docmd('DATA'))
c.send(b'Subject: test\r\n\r\nHello\r\n.\r\n')
print(c.getreply())
c.quit()
"
# → Last DATA response: (250, b'OK [STAGED] Send queued for approval — ID: op_XXXXXX')
```

```bash
# Terminal 1 — start API
PYTHONPATH=. uvicorn api.main:app --host 127.0.0.1 --port 8080
# → Application startup complete.

# Terminal 2 — check it
curl -s http://127.0.0.1:8080/api/v1/operations?status=pending | python3 -m json.tool
```

---

## Step 4 — systemd service units

Create three service files. Replace `/home/nuvrail/nuvrail` with your actual repo path.

### `/etc/systemd/system/nuvrail-imap.service`

```ini
[Unit]
Description=Nuvrail IMAP Proxy
After=network.target
Wants=network.target

[Service]
Type=simple
User=nuvrail
WorkingDirectory=/home/nuvrail/nuvrail
EnvironmentFile=/home/nuvrail/nuvrail/.env
ExecStart=/home/nuvrail/nuvrail/.venv/bin/python -m gateway.proxy
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/nuvrail-smtp.service`

```ini
[Unit]
Description=Nuvrail SMTP Proxy
After=network.target
Wants=network.target

[Service]
Type=simple
User=nuvrail
WorkingDirectory=/home/nuvrail/nuvrail
EnvironmentFile=/home/nuvrail/nuvrail/.env
ExecStart=/home/nuvrail/nuvrail/.venv/bin/python -m gateway.smtp_proxy
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/nuvrail-api.service`

```ini
[Unit]
Description=Nuvrail Approval API
After=network.target
Wants=network.target

[Service]
Type=simple
User=nuvrail
WorkingDirectory=/home/nuvrail/nuvrail
EnvironmentFile=/home/nuvrail/nuvrail/.env
ExecStart=/home/nuvrail/nuvrail/.venv/bin/uvicorn api.main:app \
    --host 127.0.0.1 \
    --port 8080 \
    --workers 1 \
    --log-level info
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable nuvrail-imap nuvrail-smtp nuvrail-api
sudo systemctl start nuvrail-imap nuvrail-smtp nuvrail-api

# Verify all three are running
sudo systemctl status nuvrail-imap nuvrail-smtp nuvrail-api
```

View live logs:
```bash
sudo journalctl -u nuvrail-api -f
sudo journalctl -u nuvrail-imap -f
sudo journalctl -u nuvrail-smtp -f
```

---

## Step 5 — nginx configuration

Add a new site config. Assumes you already have certbot/Let's Encrypt set up for other sites.

### `/etc/nginx/sites-available/nuvrail`

```nginx
server {
    listen 80;
    server_name test.nuvrail.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name test.nuvrail.com;

    ssl_certificate     /etc/letsencrypt/live/test.nuvrail.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/test.nuvrail.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    # Security headers
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header Referrer-Policy no-referrer;

    # Approval REST API
    location /api/ {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }

    # Health check (public, no auth)
    location /health {
        proxy_pass http://127.0.0.1:8080/api/v1/health;
    }

    # Block everything else for now
    location / {
        return 404;
    }
}
```

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/nuvrail /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Get TLS certificate (if not already done for this subdomain):

```bash
sudo certbot --nginx -d test.nuvrail.com
```

Verify API is reachable over HTTPS:

```bash
curl -s https://test.nuvrail.com/api/v1/operations?status=pending | python3 -m json.tool
```

---

## Step 6 — Expose IMAP/SMTP proxy ports (for remote AI agents)

The proxies listen on `127.0.0.1` by default. If your AI agent runs on the same machine, no change needed. If the agent runs remotely, you have two options:

### Option A — SSH tunnel (recommended for testing)

On the machine running the AI agent:
```bash
ssh -N -L 10143:127.0.0.1:10143 -L 10587:127.0.0.1:10587 user@test.nuvrail.com
```

The agent then connects to `localhost:10143` and `localhost:10587` as if it were co-located.

### Option B — Bind proxies to a network interface

Change `.env`:
```dotenv
NUVRAIL_PROXY_HOST=0.0.0.0
```

Then add a firewall rule to restrict access to trusted IPs only:
```bash
sudo ufw allow from <agent-ip> to any port 10143
sudo ufw allow from <agent-ip> to any port 10587
```

⚠️ **Do not expose these ports publicly.** They accept plain-text connections and carry credentials. SSH tunnel is strongly preferred for testing.

---

## Step 7 — Run the integration test suite against the live server

From your development machine (or the server itself):

```bash
cd nuvrail
source .venv/bin/activate

# Point tests at the live server's API
export NUVRAIL_PROXY_API_URL=https://test.nuvrail.com

# Run only integration tests (skips unit tests that use localhost fixtures)
PYTHONPATH=. pytest tests/integration/ -v
```

---

## Operational runbook

### Check service health

```bash
# All three services
sudo systemctl status nuvrail-imap nuvrail-smtp nuvrail-api

# API health endpoint
curl -s https://test.nuvrail.com/health

# Check pending operations
curl -s https://test.nuvrail.com/api/v1/operations?status=pending | python3 -m json.tool
```

### Restart after code update

```bash
cd /home/nuvrail/nuvrail
git pull
source .venv/bin/activate
pip install -e ".[dev]"  # picks up any new deps

sudo systemctl restart nuvrail-imap nuvrail-smtp nuvrail-api
```

### View pending operations and approve manually

```bash
# List pending
curl -s https://test.nuvrail.com/api/v1/operations?status=pending | \
  python3 -m json.tool

# Approve one (replace op_XXXXXX with real ID)
curl -s -X POST https://test.nuvrail.com/api/v1/operations/op_XXXXXX/approve | \
  python3 -m json.tool

# Reject one
curl -s -X POST https://test.nuvrail.com/api/v1/operations/op_XXXXXX/reject | \
  python3 -m json.tool
```

### Inspect the SQLite database directly

```bash
sqlite3 /var/lib/nuvrail/nuvrail.db

# All pending operations
SELECT id, op_type, protocol, description, created_at FROM staged_operations WHERE status='pending';

# Audit log
SELECT timestamp, operation_id, event, actor FROM audit_log ORDER BY id DESC LIMIT 20;

# All operations
SELECT id, status, op_type, protocol, created_at FROM staged_operations ORDER BY created_at DESC;
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| IMAP proxy won't start | `journalctl -u nuvrail-imap -n 50` — usually missing env var or upstream unreachable |
| SMTP DATA returns 421 | Upstream connection failed — check `NUVRAIL_TEST_SMTP_HOST` and credentials in `.env` |
| API returns 502 via nginx | uvicorn not running — `systemctl status nuvrail-api` |
| Operations not appearing in DB | Check `NUVRAIL_DATA_DIR` path exists and is writable by the service user |
| AUTH fails through proxy | Credentials in `.env` may differ from what the client is sending — check proxy logs |
| `RuntimeError: Event loop is closed` in test output | Benign asyncio teardown artefact on Python 3.9 — safe to ignore, does not affect test results |

---

## What's not here yet

- **TLS on the proxy ports** — the proxies speak plain TCP on their listen side. This is intentional for Phase 0 (the AI agent and proxy are co-located or connected via SSH tunnel). TLS termination on the proxy ports arrives in a later milestone.
- **Authentication on the REST API** — Lane 3 JWT auth is not yet implemented. Do not expose the API publicly without adding auth or restricting access at the nginx level (`allow`/`deny` directives or HTTP basic auth as a temporary measure).
- **Web approval UI** — the React PWA is a later milestone. Use the curl commands above or write a small script.
- **SMTP approval actually relaying** — SMTP approve currently sends the email via the upstream relay. IMAP approve (executing the actual STORE/MOVE against upstream) arrives in milestone 1.1 with the state DB.
