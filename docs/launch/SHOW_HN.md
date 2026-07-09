# Show HN post — INPUT to the CRO outreach pack (do not post as a second Show HN)

> **Handoff note.** Per the 2026-07-08 launch-content reconciliation, the CRO
> outreach pack (`revenue-and-marketing/2026-07/oss-launch-outreach-pack.md`,
> nuvrail-internal) is the authoritative source for the *actual* Show HN post
> Martin publishes. This draft is provided as **input/raw material** for that
> pack — a positioning-consistent version to fold in, not a competing post.
> Reconcile into the pack; do not open two Show HN threads.

**Title:** `Show HN: Nuvrail – an approval layer between AI agents and your inbox`

**Body (~230 words):**

I kept wanting to let an AI agent handle my email, and kept not doing it. Reading
my inbox, fine. But an agent that can send, move, and delete mail with no human
check is a liability — one hallucinated "reply all" or confident-but-wrong
archive and you can't take it back.

So I built Nuvrail: an IMAP/SMTP proxy that sits between the agent and your real
mail server. To the agent it looks like a normal mail server. In reality, reads
pass through instantly (the agent is never blocked), but every write — move,
delete, flag, outbound send — is staged and waits for you to approve with one
tap. You see the subject, sender, and destination; you say yes or no. On yes it
executes; on no the proxy reverts and tells the agent. `EXPUNGE` is blocked at
the gateway, so nothing is ever permanently deleted — worst case is Trash, and
even that needs your sign-off.

It's AGPL-3.0 and free — no hosting, no pricing. The point of open source here is
that a trust layer you can't inspect isn't a trust layer, so the interception and
approval logic is all there to read.

`docker compose up` and it runs on localhost in about 60 seconds — no domain, no
TLS. Repo + quickstart: https://github.com/agens-field/nuvrail

Honest scope: early project, core proxy works, rough edges are documented. Would
love feedback from anyone wiring agents into real workflows.

**First comment (author, to seed the thread):**

Technical detail for the HN crowd: the gateway returns `OK [STAGED]` synchronously
so the agent's IMAP/SMTP session never stalls waiting on a human — it keeps working
against staged local state, and the approve/reject reconciles against the real
server out of band. Happy to go deep on how rejection-reversion and the EXPUNGE
block work; it's all in the gateway source.
