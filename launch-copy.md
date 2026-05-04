# Launch Copy — Show HN + CEO Outreach

_Prepared for launch day: 2026-05-19_

---

## Section 1: Show HN Post

**Title:** `Show HN: Nuvrail – IMAP/SMTP approval proxy for AI agents (open source)`

**Body:**

AI agents with email access can read, send, and permanently delete messages with no review step — there's no equivalent of a pull request for email actions. Nuvrail is an IMAP/SMTP proxy that intercepts every proposed action (send, delete, move) and stages it for human approval before it touches the real mailbox, with an immutable audit log of every decision. The core proxy is open source and you can self-host the entire stack; we also offer a hosted cloud version with a managed review UI. Try it at nuvrail.com or read the code at [GITHUB_URL].

---

### Anticipated HN Comments — Prepared Responses

---

**Q: "Why not just use OAuth scopes or a restricted Gmail API? You don't need a proxy for this."**

Scoped OAuth on Gmail gets you closer, but it still doesn't give you a staged approval flow or an audit log. And we built for IMAP/SMTP specifically because the vast majority of AI agent email integrations — LangChain tools, custom agents, anything that uses an IMAP library — use raw credentials, not the Gmail API. We wanted something that works across providers and agents without requiring them to adopt a new SDK. If you're all-in on Gmail + Google OAuth, you could approximate some of this; for everyone else, there's no existing primitive.

---

**Q: "Isn't this just Zapier with an approval step?"**

Zapier is a workflow automation platform. Nuvrail is infrastructure. Zapier requires you to build your email automation inside Zapier's model. Nuvrail is transparent to your agent — your agent thinks it's talking to a regular IMAP/SMTP server. You also don't get an immutable audit log from Zapier, and Zapier has no concept of staging a destructive action (delete) for review. Different layer entirely.

---

**Q: "What's the threat model? What exactly are you protecting against?"**

Primarily mistakes, not adversaries. Most AI agent email incidents aren't attacks — they're an agent misinterpreting an instruction and sending a draft early, or interpreting "clean up" as delete. The approval layer adds a human checkpoint that catches those. The audit log covers the post-hoc question: "what did the agent do and when?" The secondary threat model is a compromised agent — if something upstream of Nuvrail is misbehaving, it still can't send or delete without a human approving the action.

---

**Q: "How does this scale if every action needs human approval?"**

For most teams building AI email agents, the workflow is already human-in-the-loop — the agent proposes, a person reviews, it executes. Nuvrail just makes that explicit and auditable rather than an informal practice. For genuinely autonomous, high-volume pipelines, Nuvrail isn't the right fit and we're honest about that. We do support trusted read-only actions without approval (configurable), and we're looking at auto-approval rules for deterministic low-risk actions (e.g., "auto-approve folder moves if sender is in approved list"). That's on the roadmap.

---

**Q: "Who's the team and why are you the ones to build this?"**

We're a small team that came out of developer infrastructure and security. We ran into this problem directly while building an AI assistant that had email access, got burned by it, and didn't find anything in the ecosystem that addressed it at the infrastructure level. The core insight — that the git model of "diff before merge" should exist for email — seemed obvious once we articulated it, and we couldn't find anyone who'd built it. So we did. We're pre-revenue, seed-stage, and this is our first public launch. Happy to talk to anyone who has strong opinions about how the proxy layer should work.

---

---

## Section 2: CEO Cold Outreach — Launch Day Email

_From: jack@nuvrail.com_
_Sent: 2026-05-19_

---

### Variant A — Warm (CTO has publicly discussed AI agent deployment)

**Subject:** the approval layer you don't have for email agents

Hi [Name],

You've written/spoken about [specific thing they said — e.g., "deploying AI agents across your support workflow"] — which means you've probably already thought about what happens when one of them does something wrong in email. We built Nuvrail to solve exactly that: it's an IMAP/SMTP proxy that stages every AI-proposed email action for human approval before it executes, with an immutable audit log. The core is open source, and it's agent-agnostic — you point your existing agent at a new endpoint, no code changes. Would you be willing to try it and tell us what we got wrong?

Jack  
jack@nuvrail.com | nuvrail.com | [GITHUB_URL]

---

### Variant B — Cold-ish (CTO at AI-forward company, no public AI email commentary)

**Subject:** what git did for code, for email

Hi [Name],

[Company] is clearly investing in AI-assisted workflows — we've been following what your team's building. One gap we haven't seen anyone solve well: AI agents with email access have no equivalent of a pull request. They can send, delete, and read with full IMAP permissions and no review step, which is fine until it isn't. Nuvrail is an open-source IMAP/SMTP proxy that stages every proposed email action for human approval before it touches a real inbox — immutable audit log, no permanent deletes without approval, works with any agent or provider. We launched today. Would you be willing to take a look and tell us if it solves a real problem for your team?

Jack  
jack@nuvrail.com | nuvrail.com | [GITHUB_URL]

---

_Notes for Jack: Keep the [specific thing they said] reference genuinely specific — one sentence that shows you read something they wrote or said, not a generic compliment. The ask is "try it and tell us what we got wrong" not "hop on a call" — engineers respond better to the former. Personalize the company name in Variant B and remove the bracketed note._
