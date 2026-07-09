# Reddit + Lobsters variants (input to the launch post set)

> Same positioning as NARRATIVE.md, tuned per audience. These have no Show HN
> collision — keep them and add them to the post set Martin works from. Repo
> must be public before any of these go out.

---

## r/selfhosted

**Title:** `Nuvrail: self-hosted approval layer that sits between an AI agent and your real inbox (AGPL, docker compose)`

**Body:**

If you've wanted to let an AI agent touch your email but didn't trust it not to
send/delete something irreversible, this is for you.

Nuvrail is an IMAP/SMTP proxy you self-host. The agent connects to Nuvrail
instead of your real mail server. Reads pass straight through; every write
(send, move, delete, flag) is held and waits for you to approve with one tap.
`EXPUNGE` is blocked at the gateway, so nothing is ever permanently deleted.

- Runs with `docker compose up` — localhost in ~60s, no domain/TLS needed to try
- AGPL-3.0, no hosted version, no pricing — you run it, you own it
- Works in front of Gmail, iCloud, Outlook, or any IMAP/SMTP server

Repo + 60-second quickstart: https://github.com/agens-field/nuvrail

Early project — core proxy works, rough edges documented. Feedback welcome,
especially on provider setup.

## r/LocalLLaMA

**Title:** `Gave my local agent email access without the pucker factor — Nuvrail stages every write for human approval (open source)`

**Body:**

For anyone running a local agent (Claude Desktop, Cursor, custom loops) that you
want to give email: the scary part is the writes. Reading is harmless; an agent
that can autonomously send/delete mail is a different risk class.

Nuvrail is an IMAP/SMTP proxy between the agent and your mail server. The agent
sees a normal mail server and is never blocked — reads pass through instantly.
But every write returns `OK [STAGED]` and waits for you to approve/reject with
one tap. Reject reverts the local state and surfaces it to the agent on its next
command. `EXPUNGE` is blocked entirely.

AGPL-3.0, self-hosted, free. The point of it being open is that a trust layer you
can't read isn't one — the interception + approval logic is all in the gateway
source. `docker compose up` and you're testing on localhost in about a minute.

https://github.com/agens-field/nuvrail

## Lobsters

**Tags:** `security`, `ai`, `show`

**Title:** `Nuvrail – an IMAP/SMTP approval proxy that puts a human in the loop for AI-agent email writes`

**Body:**

Nuvrail sits between an AI agent and a real mail server as an IMAP/SMTP proxy.
Design goal: make it *safe* to give an agent email by making destructive/
irreversible actions require explicit human approval, without blocking the agent
on reads.

Mechanism: reads pass through synchronously; writes (SMTP send, IMAP move/store/
delete) return `OK [STAGED]` immediately and are queued for human approval. On
approve, the op executes against the upstream server; on reject, the proxy
reverts local state and the agent learns on its next command. `EXPUNGE` is
refused at the gateway — permanent deletion is not expressible through Nuvrail.

It's AGPL-3.0 and self-hosted; there's no hosted product. The open-source
posture is deliberate — a trust boundary you can't audit is not a trust
boundary. Quickstart is `docker compose up` (localhost, no TLS/domain to
evaluate). Interested in feedback on the staging/reconciliation model and the
IMAP semantics of deferring writes.

https://github.com/agens-field/nuvrail
