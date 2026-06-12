# On-Call & Monitoring Runbook

**What it is:** the operational reference for how we detect, route, and triage
production incidents for Nuvrail's fly.io deployment. It is the documentation
deliverable for the two pre-launch monitoring tracks:

- **#34** — external (off-host) uptime monitoring for every fly.io endpoint.
- **#35** — fly.io machine-level alerts (CPU, memory, restart/crash).

> **Scope note.** This doc covers *monitoring policy, alert routing, and
> false-positive vs real-incident triage*. The fly deploy artifacts it
> references (`fly.gateway.toml`, `fly.staging-gateway.toml`) live in the
> private `nuvrail-enterprise` repo per the deploy-ownership rule; only their
> *verified state* is summarised here. The end-to-end deployment checker it
> complements is documented in [`staging-monitor.md`](./staging-monitor.md).

---

## The three layers of "is it up?"

We deliberately run three independent detection layers. Each catches a class of
failure the others miss. None alone is sufficient.

```
                         WHAT IT CATCHES
┌──────────────────────┬──────────────────────────────────────────────┐
│ 1. fly-native checks │ machine crash / unhealthy /health / OOM kill  │
│    + machine alerts  │ restart loop, CPU/memory pressure             │
│    (#35)             │ → INSIDE the fly network; blind to fly itself │
├──────────────────────┼──────────────────────────────────────────────┤
│ 2. external uptime   │ whole-region outage, DNS/TLS break, fly.io    │
│    monitoring (#34)  │ control-plane down, cert expiry, edge routing │
│                      │ → OUTSIDE fly; blind to app-internal logic    │
├──────────────────────┼──────────────────────────────────────────────┤
│ 3. staging E2E       │ deploy that came up but is wired wrong (bad   │
│    monitor (#18)     │ secret, CORS breach, volume unmounted, push   │
│                      │ misconfig) — functional, not just reachable   │
└──────────────────────┴──────────────────────────────────────────────┘
```

A real incident usually trips more than one layer. A single-layer alert is a
cue to check the others before paging a human — see triage below.

---

## Layer 1 — fly.io machine alerts (#35)

### Verified config state (as of this doc)

fly machine behaviour is set per-service in the gateway toml. The IMAP and SMTP
proxies must never auto-stop (they hold long-lived client connections); the REST
API may auto-stop to save cost because it is stateless and cold-starts fast.

| Service        | internal port | `min_machines_running` | `auto_stop_machines` | Rationale                              |
|----------------|---------------|------------------------|----------------------|----------------------------------------|
| REST API       | 8080          | `0`                    | `true`               | stateless; cold-starts on first request |
| IMAP proxy     | 10143 (→993)  | `1`                    | `false`              | long-lived sessions; never auto-stop    |
| SMTP proxy     | 10587 (→465)  | `1`                    | `false`              | long-lived sessions; never auto-stop    |

This matches #35 step 2 ("`min_machines_running = 1` for IMAP/SMTP — already
done, verify it deploys correctly"): **verified present in both
`fly.gateway.toml` (prod) and `fly.staging-gateway.toml` (staging).** The
verification of `auto_stop_machines = false` on those two services is the part
that actually guarantees they stay up — `min_machines_running = 1` alone is
necessary but not sufficient.

A fly-native health check already runs against the API:

```toml
[checks.api_health]
  port = 8080
  type = "http"
  interval = "30s"
  timeout = "5s"
  grace_period = "10s"
  method = "GET"
  path = "/health"
```

fly restarts the machine if `/health` stays unhealthy. Restarts surface as
brief TCP/HTTP blips on the external checks (Layer 2).

### What to enable (operator action — needs `flyctl` + fly account)

These steps require fly.io credentials and cannot be done unattended; an
operator runs them once per app, per environment:

1. `flyctl metrics enable --app <app>` — turns on CPU/memory/restart metrics for
   both `nuvrail-mail*` (gateway) and `nuvrail-app*` (web) apps.
2. Confirm the deployed config matches the table above:
   `flyctl config show --app <app>` and check the IMAP/SMTP services report
   `min_machines_running = 1` and `auto_stop_machines = false`.
3. For launch scale we deliberately **do not** stand up Prometheus/Grafana.
   Restart detection rides on the external TCP checks (Layer 2); fly's own
   metrics dashboard covers CPU/memory inspection. Prometheus is deferred to
   Phase 3 (see `docs/phase2-priority-stack-rank.md`).

### Alert thresholds — false positive vs real incident

| Signal                              | False positive when…                                   | Real incident when…                                        | Action                                            |
|-------------------------------------|--------------------------------------------------------|-----------------------------------------------------------|---------------------------------------------------|
| API machine "stopped"               | idle auto-stop (expected; `min=0`, `auto_stop=true`)   | stays stopped *and* `/health` fails to cold-start          | only page if Layer 2 `/health` check is also red  |
| API `/health` slow / one timeout    | cold-start after idle auto-stop (~1 first request)     | repeated timeouts across ≥2 consecutive external probes     | page after 2nd consecutive failure, not the 1st   |
| Single machine restart              | fly platform migration / one-off OOM that recovered    | restart loop (≥3 restarts in 10 min)                        | page on loop; one isolated restart → log only     |
| IMAP/SMTP machine stopped           | **never expected** (`auto_stop=false`, `min=1`)        | any time it is reported stopped                             | page immediately — this should not happen          |
| CPU sustained > 80%                 | brief spike during deploy / bulk approval burst        | sustained > 80% for > 5 min with rising latency             | investigate; scale check                          |
| Memory > 85%                        | transient during build/restart                         | climbing trend (possible leak) or OOM-kill in logs          | investigate; capture heap if it recurs            |

**Golden rule:** the API service is *designed* to stop when idle. Do not wire a
pager to "API machine is stopped" in isolation — that will page you every quiet
night. Page on **user-visible failure** (`/health` unreachable from outside),
which is Layer 2's job.

---

## Layer 2 — external uptime monitoring (#34)

External checks run from **outside** the fly.io network so we detect outages
independently of the host — including fly.io itself being down, DNS/TLS
failures, and cert expiry that internal checks are blind to.

### Endpoints to monitor

Per environment (staging now, production at launch — swap the host):

| Endpoint                                            | Check | Expected           |
|-----------------------------------------------------|-------|--------------------|
| `https://<gateway-host>/health`                     | HTTPS GET | `200`, body `ok` |
| `https://<web-host>/`                               | HTTPS GET | `200` (serves HTML)|
| `<gateway-host>:993`                                | TCP   | connection accepted |
| `<gateway-host>:465`                                | TCP   | connection accepted |

Staging hosts: `nuvrail-staging-gateway.fly.dev`, `nuvrail-staging-web.fly.dev`.
Production hosts (at launch): `nuvrail-mail.fly.dev`, `nuvrail-app.fly.dev`
(confirm final prod app names against `deploy.yml` before go-live).

### Tooling

**Better Uptime** (free tier) is the recommendation: HTTP + TCP checks at 3-min
intervals, on-call routing via email/Slack, public status page included.
Alternative: UptimeRobot (simpler, less routing flexibility).

> **Operator action (needs a third-party account — cannot be done unattended):**
> 1. Create the Better Uptime account; add the 4 checks per environment.
> 2. Set alert routing: email `kc@nuvrail.com` + Slack webhook if available.
> 3. (Optional) embed the status-page badge on nuvrail.com.

### The auto-stop interaction (read this before tuning the `/health` check)

The API machine auto-stops when idle (`min_machines_running = 0`). An external
`/health` probe against an idle machine triggers a **cold start**: the first
request may be slow or time out, then succeed. To avoid a stream of false pages:

- Set the `/health` check **timeout generously** (≥ 10s) to absorb cold-start.
- Require **2 consecutive failures** before alerting (not a single miss).
- Treat the IMAP/SMTP TCP checks (993/465) as the more reliable "is the box up"
  signal — those services never auto-stop, so a TCP failure there is unambiguous.

---

## Layer 3 — staging E2E monitor (#18)

The scheduled functional checker (every 45 min) that proves the *deployment is
wired right*, not merely reachable. Full detail in
[`staging-monitor.md`](./staging-monitor.md). On-call relevance: a Layer 3
`failed` check (e.g. `cors_isolation`, `vapid_key`) is a real incident even when
Layers 1 and 2 are green — the box is up and serving, but a security or
functional invariant is broken.

---

## Triage decision tree

```
            Alert fired
                │
                ▼
   ┌── Which layers are red? ──────────────────────────────┐
   │                                                        │
   │  Only Layer 1 "API stopped", Layers 2/3 green          │
   │     → idle auto-stop. NO ACTION. (expected)            │
   │                                                        │
   │  Layer 2 /health red + Layer 1 API unhealthy           │
   │     → API genuinely down. Check fly logs for crash/    │
   │       OOM/restart-loop. Page on-call.                  │
   │                                                        │
   │  Layer 2 IMAP/SMTP (993/465) TCP red                   │
   │     → proxy down (should never auto-stop). Page now.   │
   │                                                        │
   │  Only Layer 3 failed (Layers 1/2 green)                │
   │     → up but mis-wired (CORS/secret/push). Real        │
   │       incident — inspect the named check, do not       │
   │       assume "it's just monitoring".                   │
   │                                                        │
   │  All external checks red across all endpoints          │
   │     → suspect fly.io region / control-plane / DNS-TLS. │
   │       Check fly status page before deep-diving the app.│
   └────────────────────────────────────────────────────────┘
```

---

## Alert routing & contacts

| Channel            | Target                | Layer(s)        |
|--------------------|-----------------------|-----------------|
| Email              | `kc@nuvrail.com`      | 1, 2, 3         |
| Slack webhook      | *(if/when available)* | 2 (Better Uptime)|
| Staging monitor    | per `staging-monitor.md` | 3            |

Single on-call (KC) for launch (Phase 0 single-user scale). Revisit rotation
when the team and user count grow.

---

## Open items before this is "done"

The code/config and policy in this doc are reviewable now; the following
require credentials/accounts an operator must provision (they cannot be done on
an unattended run):

- [ ] Better Uptime account created + 4 checks per env configured (#34).
- [ ] `flyctl metrics enable` run for both apps, both envs (#35).
- [ ] Deployed config spot-checked with `flyctl config show` to confirm the
      IMAP/SMTP `min_machines_running = 1` / `auto_stop_machines = false` state
      actually shipped (#35 step 2).
- [ ] `/health` external check tuned for the cold-start auto-stop interaction
      (≥10s timeout, 2-consecutive-failure threshold).
