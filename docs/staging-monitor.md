# Staging Monitor — Continuous E2E Against Deployed Staging

**What it is:** a scheduled end-to-end monitor that exercises the *deployed*
fly.io staging environment over its real network endpoints, every 45 minutes,
and alerts when something is wrong. It is the answer to #18's follow-on ask:
"a set of end-to-end tests that validate every part of the staging
environment, runnable automatically and continuously throughout the day."

## How this differs from `tests/e2e/`

```
tests/e2e/              tests/staging_smoke/
─────────────           ─────────────────────
in-process              over the network
proxies + FastAPI       hits nuvrail-staging-gateway.fly.dev
spun up locally         and nuvrail-staging-web.fly.dev
runs on every PR        runs on a 45-min schedule
proves the CODE works   proves the DEPLOYMENT is up + wired right
```

Both matter. `tests/e2e/` catches a regression before it merges. The staging
monitor catches a deploy that merged clean but broke in the deployed
environment (bad secret, CORS misconfig, volume not mounted, app crash-looping)
— the class of failure a manual smoke test only finds when someone happens to
run it.

## What it checks

| Check | Needs creds? | What a failure means |
|---|---|---|
| `gateway_health` | no | gateway down or `/health` not `ok` |
| `web_up` | no | PWA not serving HTML at the web app |
| `cors_isolation` | no | staging granting CORS to the production origin (isolation breach) |
| `vapid_key` | no | push not configured on the deployed gateway |
| `account_lifecycle` | no | self-serve register→login→/me→operations→audit→delete broken end-to-end |
| `proxy_write_approve_deliver` | yes | IMAP write→stage→approve→deliver broken (skipped until enterprise wiring) |
| `rejection_revert` | yes | rejection→revert→unsolicited-FETCH broken (skipped until enterprise wiring) |

**Graceful degradation:** checks needing credentials report `skipped` (not
`failed`) when the relevant env vars are absent, so the monitor stays green and
useful on the public surface even before the credentialed wiring lands.
Skipped checks never fail the run; only a real `failed` check does.

## Running it locally

```bash
# Public + self-serve checks only (no secrets needed):
python -m tests.staging_smoke.runner
# Prints a JSON report; exits 0 if all green, 1 if any check failed.

# Point at a different staging deployment:
NUVRAIL_STAGING_GATEWAY_URL=https://my-gateway.fly.dev \
NUVRAIL_STAGING_WEB_URL=https://my-web.fly.dev \
  python -m tests.staging_smoke.runner
```

Unit tests for the aggregation/exit logic (no network):

```bash
python -m pytest tests/staging_smoke/test_runner_logic.py -q
```

## The schedule + alerting

`.github/workflows/staging-monitor.yml` runs the monitor every 45 minutes
(`cron: "*/45 * * * *"`, UTC) and on manual dispatch.

- **Green run:** no-op. The JSON report is uploaded as a build artifact
  (`staging-monitor-report`, retained 14 days) either way.
- **Failed run:** the workflow opens — or refreshes, via a comment — a single
  GitHub issue labelled `staging-monitor`, embedding the JSON report and a link
  to the run, then fails the job. One open issue per ongoing incident, not one
  per failing run.

### Configuration

Repo **variables** (optional; defaults point at the known staging apps):

- `NUVRAIL_STAGING_GATEWAY_URL` (default `https://nuvrail-staging-gateway.fly.dev`)
- `NUVRAIL_STAGING_WEB_URL` (default `https://nuvrail-staging-web.fly.dev`)
- `NUVRAIL_PROD_WEB_ORIGIN` (default `https://nuvrail-app.fly.dev`) — the origin
  CORS must reject.

Repo **secrets** (optional; activate the credentialed proxy checks):

- `NUVRAIL_STAGING_MONITOR_IMAP_USER`
- `NUVRAIL_STAGING_PROXY_IMAP_PORT`

## Interpreting a report

```json
{
  "summary": { "passed": 5, "failed": 0, "skipped": 2 },
  "checks": [ { "name": "gateway_health", "status": "ok", "detail": "...", "duration_ms": 85 }, ... ]
}
```

- `failed > 0` → staging has a real problem; read each failed check's `detail`,
  which names the exact endpoint and status code (e.g. `DELETE /account -> 422`).
- `skipped > 0` with `failed == 0` → monitor is healthy; the skipped checks are
  waiting on credentials, not broken.
- Rising `duration_ms` on `gateway_health` across runs is an early warning of
  fly cold-starts or a degrading instance.

## Outstanding (enterprise deploy wiring)

The two credentialed checks (`proxy_write_approve_deliver`, `rejection_revert`)
are scaffolded and `skipped` until staging exposes its IMAP/SMTP proxy ports
and a dedicated monitor mailbox credential is provisioned. Both belong with the
deploy/ops tooling in `nuvrail-enterprise` (port exposure + the monitor secret),
not in this public core repo. The check functions already gate on the env vars
and will activate automatically once those secrets are present. Tracking: #18
follow-on.
