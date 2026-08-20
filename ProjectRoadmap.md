# Nuvrail Roadmap

Nuvrail is an **open-source, self-hostable** approval gateway between AI agents and
email. This roadmap reflects the open-source-first direction: the free core is the
product, and the priority is making it trivial to self-host and safe to trust. It
supersedes an earlier draft framed around a paid hosted/enterprise offering.

> Roadmaps move. Nothing here is a commitment or a dated promise — it's the current
> thinking, and it changes as the project and its contributors learn. Issues and PRs
> are the source of truth for what's actually being worked on.

## Shipped (core, today)

- IMAP + SMTP proxying with **propose-before-execute**: reads pass through instantly,
  every write (move / delete / flag / outbound send) is staged for human approval.
- `EXPUNGE` (permanent delete) **blocked unconditionally** at the gateway.
- Staging engine with readable diffs, reverts, undo, batch approve/reject, and 48h
  expiry of un-actioned operations.
- Tamper-evident, hash-chained **audit log**, verified on a schedule and on demand.
- REST API (FastAPI) + installable React PWA with web-push approvals.
- **Gmail** via OAuth2 (XOAUTH2); generic IMAP/SMTP for any provider.
- Credentials encrypted at rest (AES-256-GCM) for self-host; secret-manager backend
  available. GDPR-style two-stage account deletion (secret teardown + OAuth grant
  revocation + audit anonymization → hard purge).
- Private-instance controls: closed / invite / open signup modes.

## Near-term (what we're focused on next)

- **Lower time-to-first-successful-setup.** Provider setup guides (Gmail, Apple/iCloud,
  generic IMAP) and integration recipes (Claude Desktop, Cursor) so a newcomer is
  safely proxying an agent's email in under ~15 minutes.
- **More providers, proven end-to-end.** Apple/iCloud (app-specific passwords) and
  Microsoft/Outlook (XOAUTH2) verified against live accounts, with automated coverage.
- **Setup UX hardening.** Clearer failure messages (distinguish "can't reach the API"
  from a server rejection), and signup screens that reflect a deployment's actual
  signup mode instead of dead-ending.

## Later / exploring

- Postgres as an alternative to SQLite for larger self-host deployments.
- iOS app for push approvals (APNs).
- Additional upstream providers as contributors need them.

## Not on the open-source roadmap

Group administration, enterprise buyer controls, and compliance reporting are **not**
part of the open-source core. Any such capabilities live outside this repository and
are not required to run, self-host, or contribute to Nuvrail.

---

*Want something on this list sooner, or something that isn't here at all? Open an issue —
contributor input drives priority.*
