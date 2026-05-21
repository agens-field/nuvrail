#!/usr/bin/env bash
# backup_db.sh — Nuvrail SQLite online backup with optional encryption and retention pruning.
#
# Usage:
#   ./scripts/backup_db.sh
#
# Environment variables (all optional — defaults shown):
#   NUVRAIL_DB_PATH            Path to the SQLite database (default: /data/nuvrail.db)
#   NUVRAIL_BACKUP_DIR         Directory for backup files  (default: /data/backups)
#   NUVRAIL_BACKUP_RETENTION   Days to keep daily backups  (default: 30)
#   NUVRAIL_BACKUP_KEY         Encryption passphrase — if set, backup is AES-256-CBC encrypted
#                              via openssl. Leave unset to skip encryption (not recommended
#                              for off-instance storage).
#
# Backup naming:
#   nuvrail_20260522_143000.db        unencrypted
#   nuvrail_20260522_143000.db.enc    encrypted
#
# Restore: see docs/backup-strategy.md
#
# Cron example (daily at 02:00 UTC on the server):
#   0 2 * * * /opt/nuvrail/scripts/backup_db.sh >> /var/log/nuvrail-backup.log 2>&1
#
# Exit codes:
#   0  success
#   1  DB not found or backup failed
#   2  encryption step failed

set -euo pipefail

DB_PATH="${NUVRAIL_DB_PATH:-/data/nuvrail.db}"
BACKUP_DIR="${NUVRAIL_BACKUP_DIR:-/data/backups}"
RETENTION_DAYS="${NUVRAIL_BACKUP_RETENTION:-30}"
ENCRYPTION_KEY="${NUVRAIL_BACKUP_KEY:-}"

DATE=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/nuvrail_${DATE}.db"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting Nuvrail DB backup"

# --- Preflight -----------------------------------------------------------

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH" >&2
    exit 1
fi

if ! command -v sqlite3 &>/dev/null; then
    echo "ERROR: sqlite3 CLI not found — install it with: apt-get install sqlite3" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# --- Online backup -------------------------------------------------------
# sqlite3 .backup uses the SQLite Online Backup API, which is safe to run
# against a live database under write load. It produces a consistent snapshot
# without requiring the application to stop.

echo "Backing up $DB_PATH → $BACKUP_FILE"
sqlite3 "$DB_PATH" ".backup '${BACKUP_FILE}'"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file was not created" >&2
    exit 1
fi

BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "Backup created: $BACKUP_FILE ($BACKUP_SIZE)"

# Sanity check: confirm the backup is a valid SQLite file
if ! sqlite3 "$BACKUP_FILE" "SELECT COUNT(*) FROM sqlite_master;" &>/dev/null; then
    echo "ERROR: Backup integrity check failed — not a valid SQLite DB" >&2
    rm -f "$BACKUP_FILE"
    exit 1
fi

# --- Encryption ----------------------------------------------------------
# If NUVRAIL_BACKUP_KEY is set, encrypt the backup before storing.
# Encryption uses AES-256-CBC with PBKDF2 key derivation (10000 iterations).
# The unencrypted file is removed immediately after encryption succeeds.

if [ -n "$ENCRYPTION_KEY" ]; then
    ENCRYPTED_FILE="${BACKUP_FILE}.enc"
    echo "Encrypting backup → $ENCRYPTED_FILE"
    if ! openssl enc -aes-256-cbc -pbkdf2 -iter 10000 \
        -pass "pass:${ENCRYPTION_KEY}" \
        -in "$BACKUP_FILE" \
        -out "$ENCRYPTED_FILE"; then
        echo "ERROR: Encryption failed" >&2
        rm -f "$BACKUP_FILE" "$ENCRYPTED_FILE"
        exit 2
    fi
    rm -f "$BACKUP_FILE"
    BACKUP_FILE="$ENCRYPTED_FILE"
    echo "Encrypted backup: $BACKUP_FILE"
else
    echo "WARNING: NUVRAIL_BACKUP_KEY not set — backup is unencrypted." \
         "Set this variable before using off-instance storage." >&2
fi

# --- Retention pruning ---------------------------------------------------
# Delete backups older than RETENTION_DAYS days.
# Both encrypted and unencrypted filenames match the glob below.

PRUNED=$(find "$BACKUP_DIR" -maxdepth 1 -name "nuvrail_*.db*" \
    -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
if [ "$PRUNED" -gt 0 ]; then
    echo "Pruned $PRUNED backup(s) older than ${RETENTION_DAYS} days"
fi

# --- Summary -------------------------------------------------------------

BACKUP_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -name "nuvrail_*.db*" | wc -l)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup complete." \
     "Retained backups: $BACKUP_COUNT. Latest: $BACKUP_FILE"
