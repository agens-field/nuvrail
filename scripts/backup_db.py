#!/usr/bin/env python3
"""
backup_db.py — Nuvrail SQLite online backup with optional encryption and retention pruning.

Usage:
    python3 scripts/backup_db.py

    Or via the shell wrapper (scripts/backup_db.sh) which calls this script
    when the sqlite3 CLI is unavailable.

Environment variables (all optional — defaults shown):
    NUVRAIL_DB_PATH            Path to the SQLite database   (default: /data/nuvrail.db)
    NUVRAIL_BACKUP_DIR         Directory for backup files    (default: /data/backups)
    NUVRAIL_BACKUP_RETENTION   Days of daily backups to keep (default: 30)
    NUVRAIL_BACKUP_KEY         Encryption passphrase. If set, backup is AES-256-CBC
                               encrypted via openssl before writing. Leave unset to
                               skip encryption (not recommended for off-instance storage).

Restore:
    A backup file is a complete SQLite database (a point-in-time snapshot taken via
    the SQLite Online Backup API). To restore, stop the service and put the backup
    file in place of NUVRAIL_DB_PATH. If NUVRAIL_BACKUP_KEY was set, decrypt first:
    `openssl enc -d -aes-256-cbc -pbkdf2 -in <backup> -out nuvrail.db`.

Audit log integrity note:
    The audit log in nuvrail.db is append-only. The backup produced by this script
    is a consistent point-in-time snapshot (SQLite Online Backup API). On restore,
    all rows present at backup time will be present — the chain is intact for that
    window. Any operations staged after the backup timestamp will be missing.

Exit codes:
    0   success
    1   pre-flight failure (DB not found, permissions)
    2   backup or integrity check failure
    3   encryption failure
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

DB_PATH = Path(os.environ.get("NUVRAIL_DB_PATH", "/data/nuvrail.db"))
BACKUP_DIR = Path(os.environ.get("NUVRAIL_BACKUP_DIR", "/data/backups"))
RETENTION_DAYS = int(os.environ.get("NUVRAIL_BACKUP_RETENTION", "30"))
ENCRYPTION_KEY = os.environ.get("NUVRAIL_BACKUP_KEY", "")


def _log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def _abort(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def take_backup(db_path: Path, backup_path: Path) -> None:
    """Use the SQLite Online Backup API for a consistent live snapshot."""
    with sqlite3.connect(str(db_path)) as src, sqlite3.connect(str(backup_path)) as dst:
        src.backup(dst, pages=100)  # 100 pages at a time; yields GIL between chunks


def verify_backup(backup_path: Path) -> None:
    """Quick integrity check: open the backup and run PRAGMA integrity_check."""
    with sqlite3.connect(str(backup_path)) as db:
        result = db.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            _abort(f"Backup integrity check failed: {result}", code=2)


# ---------------------------------------------------------------------------
# Encryption (optional)
# ---------------------------------------------------------------------------


def encrypt_backup(backup_path: Path, key: str) -> Path:
    """Encrypt backup with AES-256-CBC via openssl. Returns path to .enc file."""
    encrypted_path = backup_path.with_suffix(backup_path.suffix + ".enc")
    result = subprocess.run(  # noqa: S603
        [
            "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "10000",
            "-pass", f"pass:{key}",
            "-in", str(backup_path),
            "-out", str(encrypted_path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        encrypted_path.unlink(missing_ok=True)
        _abort(f"Encryption failed: {result.stderr.decode()}", code=3)
    backup_path.unlink()
    return encrypted_path


# ---------------------------------------------------------------------------
# Retention pruning
# ---------------------------------------------------------------------------


def prune_old_backups(backup_dir: Path, retention_days: int) -> int:
    """Delete backups older than retention_days. Returns count of pruned files."""
    import time
    cutoff = time.time() - retention_days * 86400
    pruned = 0
    for f in backup_dir.glob("nuvrail_*.db*"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            pruned += 1
    return pruned


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _log("Starting Nuvrail DB backup")

    # Pre-flight
    if not DB_PATH.exists():
        _abort(f"Database not found at {DB_PATH}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Create backup filename
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"nuvrail_{stamp}.db"

    # Take online backup
    _log(f"Backing up {DB_PATH} → {backup_path}")
    take_backup(DB_PATH, backup_path)
    size_kb = backup_path.stat().st_size // 1024
    _log(f"Backup created: {backup_path} ({size_kb} KB)")

    # Integrity check
    verify_backup(backup_path)
    _log("Integrity check passed")

    # Encrypt if key provided
    if ENCRYPTION_KEY:
        backup_path = encrypt_backup(backup_path, ENCRYPTION_KEY)
        _log(f"Encrypted backup: {backup_path}")
    else:
        print(
            "WARNING: NUVRAIL_BACKUP_KEY not set — backup is unencrypted. "
            "Set this variable before using off-instance storage.",
            file=sys.stderr,
        )

    # Prune old backups
    pruned = prune_old_backups(BACKUP_DIR, RETENTION_DAYS)
    if pruned:
        _log(f"Pruned {pruned} backup(s) older than {RETENTION_DAYS} days")

    # Summary
    retained = len(list(BACKUP_DIR.glob("nuvrail_*.db*")))
    _log(f"Backup complete. Retained backups: {retained}. Latest: {backup_path}")


if __name__ == "__main__":
    main()
