# Changelog

All notable changes to Nuvrail are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Nuvrail has not yet cut a tagged release. Everything below `0.1.0` is a
backfilled history of notable milestones reconstructed from the commit log, so
the release-notes habit is in place before the first version tag. Dates are the
dates the work landed on `main`; version numbers below `0.1.0` are logical
groupings, not published tags.

## [Unreleased]

### Added
- Unauthenticated `GET /api/v1/config` returning the deployment's `signup_mode`
  (`closed` | `invite` | `open`), so the web app can hide — not merely reject —
  the account-creation UI on closed/invite deployments. Closed shows an
  "unavailable" notice; invite surfaces a required invite-code field; open is
  unchanged. The server-side 403 on `/auth/register` remains the sole source of
  truth; the client mode is UX only and fails closed. (GH #139)
- Community & contributor docs for the open-source launch: `CONTRIBUTING.md`
  (build/test/PR workflow, AGPL-3.0/DCO stance) and `SECURITY.md` (coordinated
  disclosure policy).

### Fixed
- Web client now distinguishes a network/wiring failure from an HTTP error
  response. A `fetch()` that never reaches the API (server down, wrong
  `VITE_API_URL`/baked-in localhost, mixed content, CORS) previously surfaced
  the browser's bare "Load failed" / "Failed to fetch", indistinguishable from a
  server rejection. It now throws a typed `NetworkError` naming the target URL
  and the likely cause; HTTP 4xx/5xx errors are left exactly as-is. (GH #140)
- Removed a stray bottom "MIT License" block in the README that contradicted the
  project's AGPL-3.0 license declaration.

## [0.1.0] - 2026-06-15

First self-hostable, feature-complete build of the approval gateway: an
IMAP/SMTP proxy that stages every write (move, delete, flag, outbound send) for
human approval while letting reads pass through instantly. `EXPUNGE` is blocked
at the gateway — nothing is ever permanently deleted without sign-off.

### Added
- **Multi-provider mail support** — Gmail/Google via OAuth2 (XOAUTH2 SASL) with
  a web connect flow from the Agents UI, plus separate IMAP/SMTP host support;
  generic IMAP/SMTP via credential passthrough.
- **Auto-approval rules engine** — CRUD API and web UI to let low-risk
  operations through automatically while still gating destructive actions.
- **Operation intent labels** — human-readable summaries of what an agent wants
  to do (move, reply/forward, special-use folder actions), including batch
  intent summaries for review at a glance.
- **Web PWA approval app** — React + Vite progressive web app with Web Push
  notifications so approvals can happen from a phone or browser.
- **Audit chain verification** — tamper-evident audit log with chain
  verification, plus export completeness checks.
- **Staging-loop health monitoring** — end-to-end staging monitor and
  credentialed proxy health checks; external uptime monitoring config and an
  on-call monitoring runbook.
- **Send-rate controls** — per-account and daily send caps, send rate limiting,
  and agent suspension enforcement.

### Security
- **Encryption at rest** — AES-256-GCM encryption of upstream mail credentials,
  wired end-to-end, with a key-rotation script.
- **Web hardening** — Content-Security-Policy headers, removal of
  `unsafe-inline`, and CORS that fails closed in production.
- **Secret hygiene** — upstream secrets deleted on account disconnect/deletion;
  hardened `.gitignore`; fixed a `~`-expansion bug in path handling.
- **Tenancy isolation** — per-user mailbox mirror scoping and tenant-scoped
  operation previews so users cannot see each other's data.

### Legal / Compliance
- **GDPR erasure path** — right-to-erasure support that anonymizes and purges
  user data.
- **AGPL-3.0 source-availability footer** — in-app link to source, satisfying
  the AGPL network-use obligation.

## [0.5.0] - 2026-03-22

Pre-MVP: the proxy became a real staged-write system with a database, an API,
and a web app, and credentials were encrypted at rest.

### Added
- Staging engine backed by SQLite with a minimal REST API.
- Optimistic MOVE — messages leave the source folder in local state at staging
  time for a responsive review experience.
- Nuvrail Approval Web App (React + Vite PWA) and Web Push notifications.
- End-to-end test suite covering the staged approve/reject flow.

### Security
- AES-256-GCM credential encryption at rest.

## [0.1.0-proxy] - 2026-03-18

Earliest working proxy: raw asyncio TCP with LOGIN passthrough, then SMTP with
STARTTLS upstream and DATA staging.

### Added
- Raw asyncio IMAP TCP proxy with LOGIN passthrough.
- SMTP proxy with STARTTLS to the upstream server and outbound DATA staging.

[Unreleased]: https://github.com/agens-field/nuvrail/compare/main...HEAD
