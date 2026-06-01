# Nuvrail Database Backup Strategy
_Owner: KC (CTO) · Last updated: 2026-05-21 · Due: 2026-06-12_

---

## Overview

Nuvrail's data lives in a single SQLite database (`nuvrail.db`). This document covers:

- What's backed up and why it matters
- The backup mechanism
- Retention policy
- Encryption at rest
- Restore procedure (tested before launch — required)
- Audit log integrity note
- Remaining human actions (GitHub #24)

---

## What's in the Database

| Table | Contents | Loss impact |
|---|---|---|
| `users` | Human user accounts and hashed passwords | High — users can't log in |
| `agent_credentials` | Encrypted upstream email credentials (AES-256-GCM) | High — agents stop working |
| `staged_operations` | Pending / approved / rejected operations | Medium — operations in-flight at backup time are lost |
| `audit_log` | Append-only record of all operations | High — audit integrity broken for post-backup window |
| `auto_approval_rules` | User-defined auto-approval rules | Low — can be recreated |
| `push_subscriptions` | Web Push subscription records | Low — re-registers on next load |

---

## Backup Mechanism

### Script: `scripts/backup_db.py`

Uses the **SQLite Online Backup API** (`sqlite3.backup()` in Python), which:
- Produces a consistent snapshot safe to run against a live database under write load
- Does not require application downtime
- Copies pages in chunks, yielding the GIL between chunks — no blocking

```
┌─────────────┐   sqlite3.backup()   ┌────────────────────────────┐
│  nuvrail.db │ ──────────────────►  │  nuvrail_YYYYMMDD_HHMMSS.db │
│  (live)     │                      │  (snapshot, consistent)      │
└─────────────┘                      └────────────────────────────┘
```

### Encryption

If `NUVRAIL_BACKUP_KEY` is set, the backup is encrypted with AES-256-CBC (PBKDF2, 10000 iterations) via OpenSSL before writing. The unencrypted file is removed immediately after encryption succeeds.

**Always set `NUVRAIL_BACKUP_KEY` before uploading to off-instance storage.**

### Schedule

Intended: daily at 02:00 UTC via cron (GitHub #24 — wiring is a human action).

```bash
# /etc/cron.d/nuvrail-backup or via crontab -e
0 2 * * * NUVRAIL_DB_PATH=/data/nuvrail.db \
           NUVRAIL_BACKUP_DIR=/data/backups \
           NUVRAIL_BACKUP_KEY=<passphrase> \
           /opt/nuvrail/scripts/backup_db.py >> /var/log/nuvrail-backup.log 2>&1
```

---

## Retention Policy

- **30 daily backups** retained locally (configurable via `NUVRAIL_BACKUP_RETENTION`)
- Backups older than 30 days are pruned automatically by the script
- Off-instance storage (S3/R2/B2): set bucket lifecycle rule to 90 days or keep indefinitely

---

## Audit Log Integrity Note

The `audit_log` table is **append-only** — rows are never updated or deleted by the application. The backup script produces a consistent point-in-time snapshot:

```
Timeline:
  [backup at T]──────────────────────►[restore from T]
                                         │
                                         ├─ All rows up to T: ✅ present
                                         └─ Rows after T:     ❌ lost (expected)
```

On restore from a backup, the audit log is complete and internally consistent up to the backup timestamp. Any operations staged, approved, or rejected **after the backup timestamp** will not appear in the restored database — this is expected and must be documented for any compliance requirements.

**Recovery window:** with daily backups, maximum data loss is ~24 hours of audit entries. For shorter RPO, increase backup frequency.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NUVRAIL_DB_PATH` | `/data/nuvrail.db` | Path to live SQLite database |
| `NUVRAIL_BACKUP_DIR` | `/data/backups` | Local backup destination directory |
| `NUVRAIL_BACKUP_RETENTION` | `30` | Days to retain daily backups locally |
| `NUVRAIL_BACKUP_KEY` | *(unset)* | Encryption passphrase — **set before prod** |

---

## Restore Procedure

> **Run this procedure at least once in a test environment before the June 22 launch.**

### Step 1 — Identify the backup to restore

```bash
ls -lh /data/backups/
# nuvrail_20260522_020001.db.enc   (encrypted)
# nuvrail_20260521_020001.db.enc
```

Choose the most recent backup before the failure event.

### Step 2 — Stop the gateway

```bash
cd /opt/nuvrail
docker compose stop gateway
```

### Step 3 — Decrypt (if encrypted)

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 10000 \
    -pass "pass:<NUVRAIL_BACKUP_KEY>" \
    -in /data/backups/nuvrail_20260522_020001.db.enc \
    -out /tmp/nuvrail_restored.db
```

If unencrypted, skip this step (file already has `.db` extension).

### Step 4 — Verify the decrypted backup

```bash
python3 -c "
import sqlite3
with sqlite3.connect('/tmp/nuvrail_restored.db') as db:
    result = db.execute('PRAGMA integrity_check').fetchone()
    print('Integrity:', result[0])
    users = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    ops = db.execute('SELECT COUNT(*) FROM staged_operations').fetchone()[0]
    audit = db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
    print(f'Users: {users}, Operations: {ops}, Audit rows: {audit}')
"
```

Expected output:
```
Integrity: ok
Users: <N>, Operations: <N>, Audit rows: <N>
```

If integrity check fails: try the next most recent backup.

### Step 5 — Replace the live database

```bash
# Snapshot the broken DB first (keep for forensics)
cp /data/nuvrail.db /data/nuvrail.db.pre-restore

# Replace
cp /tmp/nuvrail_restored.db /data/nuvrail.db
chmod 640 /data/nuvrail.db
```

### Step 6 — Restart and verify

```bash
cd /opt/nuvrail
docker compose up -d gateway

# Check logs for errors
docker compose logs -f gateway --tail 50

# Quick API health check
curl -s https://nuvrail.example.com/api/v1/health | python3 -m json.tool
```

### Step 7 — Post-restore audit entry

After a successful restore, manually log a note in the audit log or in the team's incident tracker:
- Which backup was restored
- What timestamp it represents
- What data window is missing
- Who performed the restore

---

## Off-Instance Storage (Required — GitHub #24)

Local backups on the same server as the DB are not sufficient — a server failure would lose both. Before launch:

1. Choose a storage backend: Cloudflare R2 (recommended, free tier), AWS S3, Backblaze B2
2. Install `rclone` and configure a remote
3. Add an upload step after the backup cron:

```bash
# Example rclone upload after backup
rclone copy /data/backups/ r2:nuvrail-backups/ --include "nuvrail_*.db*"
```

See GitHub #24 for the full setup checklist.

---

## Checklist (Pre-Launch)

- [ ] `NUVRAIL_BACKUP_KEY` set in production environment
- [ ] Cron job scheduled on production server (GitHub #24)
- [ ] Off-instance storage configured (GitHub #24)
- [ ] Failure alerts wired (GitHub #24)
- [ ] **Restore procedure tested once in test environment** (required before launch)
- [ ] Post-restore doc added to team runbooks
