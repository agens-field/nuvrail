# Nuvrail — Launch Narrative (positioning source of truth)

> **Purpose of this file.** This is the *canonical positioning and framing* for
> Nuvrail's open-source launch. It is the story, the voice, and the claims —
> everything downstream (README voice, the Show HN body, newsletter pitches,
> the maintainer note) draws from here. It is **not** posted directly. The
> channel-ready posts live in the CRO outreach pack; this file is what keeps
> them all telling one story.

---

## The one sentence

**Email is the most dangerous thing you can hand an AI agent. Nuvrail is the
approval layer that makes it safe to try.**

## The stakes (lead with the human, not the mechanism)

An AI agent that can *read* your email is useful. An AI agent that can *send,
move, and delete* your email with no human in the loop is a liability you feel
in your stomach the first time you wire it up. One hallucinated "reply all." One
confidently-wrong archive of the thread you needed. One phishing email the agent
was tricked into forwarding. The blast radius of an autonomous agent on your
real inbox is your reputation, your relationships, and things that don't come
back.

That fear is why most people never connect an agent to their real mailbox at
all. They give it a throwaway account, or they don't give it email. The
capability is real; the trust isn't there. **That trust gap is the problem
Nuvrail exists to close.**

## The mechanism (concrete, checkable)

Nuvrail is an IMAP/SMTP proxy that sits between the agent and your real mail
server. To the agent it looks like an ordinary mail server. In reality:

- **Reads pass through instantly.** The agent is never blocked from working.
- **Every write is staged.** Every move, delete, flag change, and outbound send
  returns `OK [STAGED]` immediately, then waits.
- **A human approves with one tap.** You see the subject, sender, and
  destination on your phone or browser and say yes or no. On yes it executes
  against the real server; on no the proxy reverts and quietly tells the agent.
- **Nothing is ever destroyed.** `EXPUNGE` is permanently blocked at the
  gateway — the worst an agent can do is move a message to Trash, and even that
  needs your sign-off.

The shape is: **reads are free, writes wait, deletion is impossible.** A
developer can hold that in their head in ten seconds, and every word of it is
backed by code in the repo today.

## Why open source is the point, not a footnote

Trust you can't inspect isn't trust. A security layer you have to take on faith
is a contradiction. So Nuvrail is AGPL-3.0 and the whole thing is right there to
read: the interception logic, the fact that `EXPUNGE` is blocked, the approval
flow, the reversion on reject. You don't trust our marketing — you read the
gateway. That is the credibility engine, and it is why "star it / self-host it"
is the ask, not "buy hosting." There is no hosting. There is no pricing. There
is no waitlist. It's free, and the code is the argument.

## What it is not (honesty is part of the pitch)

- **Not a spam filter, not a security scanner.** It gates *the agent*, not the
  world's mail.
- **Not a hosted product.** Free and self-hosted. No SaaS, no tiers.
- **Not finished.** It's an early open-source project. The core proxy works and
  the 60-second local quickstart runs; the rough edges are documented and the
  roadmap is public.

## Who this is for

Agent builders — the people wiring Claude, Cursor, or a custom agent into real
workflows — the moment they hit "...and now it needs to touch my email." That's
the moment of hesitation Nuvrail is built for.

## The proof points (all live in the repo today)

- Working `docker compose up` quickstart — running locally in ~60 seconds, no
  domain/TLS/nginx required.
- `EXPUNGE` blocked at the gateway layer (the "nothing is deleted, ever"
  guarantee is code, not a promise).
- AGPL-3.0 license; CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.md, issue/PR
  templates all in place — a real front door for contributors.

## Voice and guardrails for every downstream post

- Lead with the human stakes, then the mechanism, then open-source-as-trust.
- Every claim maps to something in the repo. No vaporware.
- **No revenue / pricing / hosting claims** — Nuvrail is free OSS; the
  north-star is GitHub stars.
- Understate rather than hype. This audience punishes overclaiming and rewards
  "here's exactly what it does and doesn't do."
- One consistent tagline everywhere: *the approval layer between AI agents and
  your inbox.*
