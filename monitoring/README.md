# External uptime monitoring (#34)

Off-host (Layer 2) uptime checks for every fly.io endpoint, so we detect
outages independently of the host — including fly.io itself being down, DNS/TLS
failures, and cert expiry that internal checks are blind to. See
[`docs/oncall.md`](../docs/oncall.md) for the full three-layer monitoring model
and the triage decision tree.

## What's here

| File | Purpose |
|---|---|
| `uptime-checks.yaml` | **Source of truth** — every check, per environment, with the cold-start tolerances baked in. |
| `check_uptime.py` | Validate the manifest, and dry-run the probes from any box with **zero monitor credentials**. |

## The endpoints

Per environment (host swaps; staging is live now, production at launch):

```
gateway /health   HTTPS GET → 200, body "ok"     (API may auto-stop when idle)
web /             HTTPS GET → 200 (serves HTML)
gateway :993      TCP connect (IMAP)              (never auto-stops → real signal)
gateway :465      TCP connect (SMTPS)             (never auto-stops → real signal)
```

**Host names** come from the enterprise `deploy.yml` "Resolve deploy config"
step, the source of truth for what actually deploys — **not** from the older
GitHub issue body. Staging: `nuvrail-staging-gateway.fly.dev` /
`nuvrail-staging-web.fly.dev`. Production app names resolve from the
`FLY_MAIL_APP_PROD` / `FLY_WEB_APP_PROD` repo vars at deploy time; fill the
commented `production:` block in the manifest at go-live.

## Use it now (no account needed)

```bash
# 1. shape-check the manifest
python monitoring/check_uptime.py --validate

# 2. probe staging endpoints from wherever you are (off-host)
python monitoring/check_uptime.py --env staging
```

`check_uptime.py` needs PyYAML (`pip install pyyaml`) — a dev/ops dependency
only; the gateway runtime does not import it. Exit code is non-zero on a failed
validation or any DOWN probe, so it drops into CI or a cron line unchanged.

## Wire the paid monitor (operator action — needs an account)

The manifest is import-shaped for **Better Uptime** (free tier: HTTP + TCP
checks at 3-min intervals, on-call routing, public status page). Steps:

1. Create the Better Uptime account.
2. Add the four checks for `staging` from `uptime-checks.yaml` (and `production`
   at launch). Use the cold-start tolerances in `defaults`:
   - `/health`: timeout **≥ 10s**, alert on **2 consecutive failures** (the API
     cold-starts after idle auto-stop — a single slow first request is expected,
     not an incident).
   - IMAP/SMTP TCP checks: these never auto-stop, so a failure is unambiguous —
     no special tolerance needed.
3. Route alerts to `kc@nuvrail.com` (+ Slack webhook if/when one exists).
4. (Optional) embed the status-page badge on nuvrail.com.

Alternative: **UptimeRobot** (simpler, less routing flexibility).

> Why this can't be fully automated on a cron run: creating the monitor account
> and its API token needs a human. Everything *up to* that — the exact checks,
> hosts, thresholds, and a working off-host probe — is in this directory so the
> operator's manual step is "import + paste credentials," not "design the
> monitoring."

## Keeping it honest

`uptime-checks.yaml`'s `alerting` block mirrors `docs/oncall.md` "Alert routing
& contacts." If either changes, update both.
