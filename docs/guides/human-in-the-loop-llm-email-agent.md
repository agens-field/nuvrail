# Human in the loop for LLM email agents: the pattern, and a drop-in implementation

*Short answer: split every mail operation by consequence. Let reads run; stage writes;
have a person approve them; make permanent deletion impossible. Put that split in a
proxy that speaks IMAP and SMTP, so it works with any agent and the agent can't opt out.
Below: why that placement matters, the state machine, and an open-source implementation
you can self-host today.*

---

## The problem human-in-the-loop has to solve for email

Most "human in the loop" advice for LLM agents is written for tool calls in general: ask
before running a tool. Email is harder, for three reasons that stack:

1. **It's your most sensitive data.** Password resets, 2FA codes, contracts, payroll.
2. **It's your identity.** Every send goes out as you, and recipients can't tell an agent
   wrote it.
3. **Writes are irreversible.** A sent message can't be recalled. An IMAP `EXPUNGE` can't
   be undone.

So the question isn't "should a person be involved?" It's **which operations need a
person, and where does the check live so it can't be skipped?** Background on why email
earns this treatment:
[Why email is the most dangerous thing you can give an AI agent](../why-email-is-dangerous.md).

## The pattern

### 1. Split by consequence, not by tool

Asking before *every* action makes the agent useless; asking before *none* is how inboxes
get wiped. The line that works is reversibility:

| Operation | Examples | Treatment |
|---|---|---|
| Read | `FETCH`, `SEARCH`, `LIST`, `SELECT` | Pass through immediately |
| Change | `MOVE`, `STORE` flags, create folder | Stage, wait for approval |
| Send | SMTP `DATA` | Stage, wait for approval |
| Destroy | `EXPUNGE`, `CLOSE` | Never forwarded; a `\Deleted` flag is staged like any other change |

### 2. Don't block the agent while it waits

If the agent has to sit and wait for a person, it times out or gives up. Instead, answer
immediately with a staged success (`OK [STAGED]` on IMAP, `250 OK [STAGED]` on SMTP) and
keep a local view that reflects the pending change. The agent carries on with its run;
the person reviews when they can.

### 3. Show the person the actual operation

An approval screen that says "Agent wants to send an email" is useless. Show the subject,
sender, recipients, and destination folder, so a human can catch the wrong recipient or
the half-finished draft in a glance.

### 4. Roll back cleanly on rejection

If the person says no, revert the local view and tell the agent the next time it reads, so
its picture of the mailbox matches reality again.

### 5. Put it underneath the agent, not inside it

This is the part most implementations get wrong. A confirm step implemented *in the
agent* (a system-prompt rule, a framework "ask human" tool, a callback) runs at the
model's discretion. Autonomous runs, auto-executing tool loops, and prompt injection
from a message the agent just read can all skip it.

Put the check **on the wire**, as an IMAP/SMTP proxy. The agent connects to it like any
mail server. It can't call around it, because it never holds credentials for the real
mailbox. The check no longer depends on the model, the prompt, or the framework behaving.

### 6. Log everything

Every staged operation, approval, and rejection should be an audit event. That also
answers the next question people ask: "how do I audit what an AI agent does to my email?"

## A drop-in implementation: Nuvrail

[Nuvrail](https://github.com/agens-field/nuvrail) is an open-source (AGPL-3.0),
self-hosted implementation of exactly this pattern:

- An asyncio **IMAP proxy** that passes reads and stages writes, with a local mirror for
  the agent's view.
- An **SMTP proxy** that intercepts `DATA`, stages the message, and relays only on
  approval.
- A **staging engine** that stores snapshots so rejections revert cleanly.
- A **web app** (installable on your phone) with push notifications and one-tap
  approve/reject, plus batch approve/reject.
- A tamper-evident **audit log**.
- `EXPUNGE` **blocked at the gateway**.

The agent needs no SDK and no code changes: swap the host, port, and credentials in its
mail settings. It works with any agent that speaks IMAP/SMTP, including LangChain,
CrewAI, and plain Python `imaplib`/`smtplib` tools, and there are recipes for
[Claude Desktop](../integrations/claude-desktop.md), [Cursor](../integrations/cursor.md),
and [LangChain](../integrations/langchain.md).

### Run it

```bash
git clone https://github.com/agens-field/nuvrail.git
cd nuvrail
cp .env.example .env
docker compose up --build
# first build: ~5-9 minutes. Then:
docker compose exec gateway python3 scripts/manage_users.py create you@example.com --name "You"
```

Open `http://localhost:3000`, connect a mailbox, and point your agent at
`localhost:10143` (IMAP) and `localhost:10587` (SMTP). Production setup with TLS is in the
[README](../../README.md#deploying-to-production).

## FAQ

### Is it safe to let ChatGPT or Claude manage my email?

With direct access to your mailbox, you're trusting the model never to misread an
instruction that leads to an irreversible send or delete. With an approval layer, the
model can only *propose* those actions; you make the final call. That's the version we'd
recommend.

### What doesn't this cover?

Be clear-eyed: reads pass through by design, so an agent can still *see* everything in the
mailbox you connect. The approval layer stops that data from leaving **by email** without
your sign-off, but if your agent also has other outbound tools (web requests, chat
integrations), those need their own controls. Connect the narrowest mailbox that does the
job.

### How is this different from "AI agent email guardrails" that filter content?

Content filters try to guess whether an action is bad. This pattern doesn't guess; it makes
irreversible actions require a yes from a person. The two combine fine, but only one of them
is a hard stop.

### Why a proxy instead of an SDK or middleware?

Because protocol-level placement is framework-agnostic and can't be bypassed from the agent
side. Anything that can talk to Gmail over IMAP/SMTP can talk to the proxy, and nothing on
the agent side can turn it off.

---

If you're building an email agent and this pattern is useful, a ★ on
[the repo](https://github.com/agens-field/nuvrail) helps other builders find it.
