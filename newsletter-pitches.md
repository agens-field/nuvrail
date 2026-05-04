# Newsletter Pitch Drafts

_Prepared for launch week: 2026-05-19_

---

## Pitch 1 — TLDR AI

**To:** dan@tldrnewsletter.com _(verify current editor contact)_
**Subject:** AI agent trust gap — new open-source project launching May 19

Hi Dan,

We're launching Nuvrail on May 19 — an open-source IMAP/SMTP proxy that adds a human approval layer between AI agents and real inboxes. The problem it solves: AI agents today have raw IMAP access, which means they can read, send, and permanently delete email with no review step and no audit trail. Every other AI tooling layer has a review mechanism (code has git, deployments have approval gates), but email has nothing. Nuvrail intercepts every proposed action — send, delete, move — and stages it for human approval before it touches the real mailbox. Every approval is an explicit record; the log is immutable. It's the pull request model for email access.

It's agent-agnostic and provider-agnostic — any agent that can be configured with a custom IMAP/SMTP endpoint works without code changes. The core proxy is open source [GITHUB_URL]. Cloud Starter is free, no credit card.

Happy to provide early access or a review account before launch if that's useful. Launch date is May 19, 2026.

— Stella Chen, CMO, Nuvrail  
stella@nuvrail.com

---

## Pitch 2 — The Changelog

**To:** editors@changelog.com _(verify current editor contact)_
**Subject:** Open-source project: approval layer for AI email agents (launching May 19)

Hi Changelog team,

We're launching an open-source project called Nuvrail on May 19, and I think the philosophy behind it is a fit for your audience. The core belief: if you can't audit what an AI agent did, you can't trust it — and the trust layer itself should be open source so you can verify that claim. Nuvrail is an IMAP/SMTP proxy that stages every AI-proposed email action for human review before it executes. The proxy core, the staging queue, and the audit log format are all open and on GitHub [GITHUB_URL]. We open-sourced the infrastructure because we think the thing that sits between an AI agent and your inbox should be something you can read, audit, and self-host if you want to. The cloud version adds managed infrastructure and a hosted review UI, but the trust mechanism is never black-box.

The analogy we keep coming back to: git didn't make AI coding agents safe by making them smarter. It made them safe by making them reviewable. We built that primitive for email.

We'd love to be considered for the podcast or the newsletter — happy to get you a review account or walk through the architecture. Launch is May 19, 2026. GitHub: [GITHUB_URL].

— Stella Chen, CMO, Nuvrail  
stella@nuvrail.com

---

## Pitch 3 — Console.dev

**To:** hello@console.dev _(verify current editor contact)_
**Subject:** Tool submission: Nuvrail — IMAP/SMTP approval proxy for AI agents

Hi Console team,

Submitting Nuvrail for your consideration ahead of our May 19 launch. Short version: it's an IMAP/SMTP proxy that sits between an AI agent and a real inbox and requires human approval before any email action (send, delete, move) executes. Agent-agnostic, provider-agnostic — you point your agent at a Nuvrail endpoint instead of your real mail server and everything else is the same. Immutable audit log. No permanent deletes without approval. Core is open source [GITHUB_URL]. Cloud Starter is free.

The interesting technical bit: it works without any changes to the agent — just a different endpoint and credentials. The approval UX is a staged action queue (web + mobile) that shows exactly what the agent proposed, with one-click approve/reject.

Happy to provide early access. Launch: May 19, 2026. GitHub: [GITHUB_URL].

— Stella Chen, CMO, Nuvrail  
stella@nuvrail.com
