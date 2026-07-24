#!/bin/bash
# Nuvrail gateway entrypoint — starts all three processes:
#   1. IMAP proxy       (gateway.proxy)
#   2. SMTP proxy       (gateway.smtp_proxy)
#   3. FastAPI REST API (uvicorn api.main:app)
#
# All three write to the same SQLite DB at NUVRAIL_DATA_DIR.
# Crash behaviour: if any process exits non-zero, the container exits
# immediately so Docker restart policy handles recovery.

set -e

# Signal to the app that it is running inside a container, so the proxies can
# warn if NUVRAIL_PROXY_HOST is bound to loopback (unreachable under Docker's
# published-port forwarding — see gateway.state_db.warn_if_loopback_bind_in_container).
export NUVRAIL_IN_CONTAINER=1

# Validate required env vars before starting anything
if [ -z "$NUVRAIL_MASTER_KEY" ]; then
    echo "[entrypoint] WARNING: NUVRAIL_MASTER_KEY is not set."
    echo "[entrypoint] A key will be auto-generated to ${NUVRAIL_DATA_DIR}/master.key."
    echo "[entrypoint] This is acceptable for dev but NOT for production."
    echo "[entrypoint] Set NUVRAIL_MASTER_KEY in your .env to avoid losing encrypted credentials on redeploy."
fi

echo "[entrypoint] Starting IMAP proxy..."
python -m gateway.proxy &
IMAP_PID=$!

echo "[entrypoint] Starting SMTP proxy..."
python -m gateway.smtp_proxy &
SMTP_PID=$!

echo "[entrypoint] Starting FastAPI (uvicorn)..."
uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8080 \
    --log-level "${LOG_LEVEL:-info}" &
API_PID=$!

echo "[entrypoint] All processes started (IMAP=$IMAP_PID SMTP=$SMTP_PID API=$API_PID)"

# Wait for any process to exit; exit with its code so Docker knows something died
wait -n
EXIT_CODE=$?
echo "[entrypoint] A process exited with code $EXIT_CODE — shutting down container"
kill $IMAP_PID $SMTP_PID $API_PID 2>/dev/null || true
exit $EXIT_CODE
