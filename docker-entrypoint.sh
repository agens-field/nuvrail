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

# Behind the web container's /api/ proxy, uvicorn's peer is nginx for EVERY
# request, so request.client.host would collapse the per-IP login lockout and
# rate limits (api/limiter.py, api/routes/auth.py) into ONE global bucket --
# a single attacker could then throttle or lock out every user. Honouring
# X-Forwarded-For fixes that, but a spoofable XFF is worse than none, so it is
# opt-in: NUVRAIL_FORWARDED_ALLOW_IPS names the peers allowed to set it.
# docker-compose sets "*", safe there because the API port is published on
# loopback only and the sole other route in is the compose network. Left unset
# (direct exposure, e.g. fly.io) uvicorn keeps using the real socket peer.
PROXY_ARGS=()
if [ -n "$NUVRAIL_FORWARDED_ALLOW_IPS" ]; then
    echo "[entrypoint] Trusting X-Forwarded-For from: $NUVRAIL_FORWARDED_ALLOW_IPS"
    PROXY_ARGS=(--proxy-headers --forwarded-allow-ips "$NUVRAIL_FORWARDED_ALLOW_IPS")
fi

echo "[entrypoint] Starting FastAPI (uvicorn)..."
uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8080 \
    "${PROXY_ARGS[@]}" \
    --log-level "${LOG_LEVEL:-info}" &
API_PID=$!

echo "[entrypoint] All processes started (IMAP=$IMAP_PID SMTP=$SMTP_PID API=$API_PID)"

# Wait for any process to exit; exit with its code so Docker knows something died
wait -n
EXIT_CODE=$?
echo "[entrypoint] A process exited with code $EXIT_CODE — shutting down container"
kill $IMAP_PID $SMTP_PID $API_PID 2>/dev/null || true
exit $EXIT_CODE
