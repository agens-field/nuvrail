# How to add human approval before an AI agent sends email

*Short answer: hold the send at the SMTP layer, not inside the agent. Point the agent at
an SMTP proxy that accepts the message, holds it, shows it to you, and only relays it to
your real mail server after you approve. No agent code changes, and the agent can't skip
the step. Setup below.*

---

## Why you're right to want this

An agent that sends email sends it **as you**: your address, your authenticated session,
your reputation. The recipient's server sees a perfectly normal message. There's no header
saying an agent wrote it. And once SMTP hands it off, it's gone. You can't recall it.

Put that together with a probabilistic model and you get the failures people actually hit:

- A half-finished draft (`SUBJECT TBC`, pricing blank) goes to your biggest prospect
  because the agent read the thread as "urgent, needs a reply."
- A reply-all to a thread with outside contractors on CC quotes salary numbers from
  further up the thread.
- A friendly auto-reply loop with another bot at 3am.

None of these need a jailbreak. The agent did what it was asked, a little wrong, with a
tool that acts on the first try. The long version is
[Why email is the most dangerous thing you can give an AI agent](../why-email-is-dangerous.md).

## Where to put the approval step (and where not to)

**Inside the agent: a confirm prompt or a "draft only" instruction.** This works until it
doesn't. The model decides whether to ask, and a fully autonomous run, a prompt injection
in an email it read, or a framework that auto-executes tool calls can all skip it. You're
asking the thing you're guarding against to guard itself.

**Inside the provider: OAuth scopes.** `gmail.send` is all-or-nothing. There's no scope that
means "may send, but only this message, after I look at it." Once send is granted, every
send is pre-approved.

**"Undo send."** A 30-second window assumes a person is watching. At 3am, nobody is.

**On the wire: an SMTP proxy.** The agent's mail client connects to the proxy instead of
your provider. The proxy speaks normal SMTP, takes the message, and **doesn't forward it**.
It stores it, notifies you, and relays it only on approval. The agent can't route around a
step that sits underneath its tools. This is the one that holds.

## What the flow looks like

1. The agent sends a message over SMTP, exactly as it would to Gmail or Outlook.
2. The proxy intercepts the `DATA` command and replies
   `250 OK [STAGED] Send queued for approval`. The agent moves on.
3. You get a push notification (phone or browser) showing the subject, sender, and
   recipients.
4. **Approve** and the proxy relays it to your real mail server. **Reject** and it never
   leaves. The agent is told about the rejection the next time it connects.

In the open-source build there's no auto-approve path: nothing leaves until you tap
approve. If the agent drafts a batch of replies, you can approve or reject them as a batch.

## Set it up with Nuvrail (self-host, ~15 minutes)

[Nuvrail](https://github.com/agens-field/nuvrail) is an open-source (AGPL-3.0) IMAP/SMTP
approval proxy built on this model. It also gates the IMAP side (moves, flags, deletes)
and blocks permanent deletion outright. You need Docker with the Compose plugin:

```bash
git clone https://github.com/agens-field/nuvrail.git
cd nuvrail
cp .env.example .env
docker compose up --build
```

A cold first build takes **5 to 9 minutes**. After that, starts take seconds. Create your
account (sign-up is closed by default):

```bash
docker compose exec gateway python3 scripts/manage_users.py create you@example.com --name "You"
```

Open `http://localhost:3000`, connect your mailbox, and copy the agent credentials it gives
you. Then change three things in your agent's mail settings: **SMTP host** to the proxy,
**port** to `10587` locally, and the **login** to your Nuvrail agent credentials. Your real
mailbox password never goes near the agent.

### Framework notes

- **LangChain / CrewAI / plain Python:** if your send tool uses `smtplib`, point it at the
  proxy. That's the whole change. Full walkthrough:
  [LangChain recipe](../integrations/langchain.md).
- **Claude Desktop / Cursor (MCP):** see the [Claude Desktop](../integrations/claude-desktop.md)
  and [Cursor](../integrations/cursor.md) recipes.
- **Anything else that speaks SMTP:** same idea. If it can send through Gmail, it can send
  through the proxy.

For a real deployment (TLS, an agent on another machine), follow the
[README](../../README.md#deploying-to-production).

## FAQ

### Does the agent know its email hasn't actually gone out yet?

It gets `250 OK [STAGED]` with a queue ID, so a well-behaved agent can log that it's
pending. Most agents just carry on, which is the point: the approval doesn't block its work.

### What do I see when I approve?

The subject, sender, and recipients, so you can catch the wrong recipient or the
half-finished draft. The notification is end-to-end encrypted to your device via Web Push,
so the relay in between can't read it.

### Is this a guardrail or a filter?

A guardrail. It doesn't try to guess whether an email is "bad." It makes sure a person says
yes before anything irreversible happens. Guessing is the model's job,
and the model is what you don't fully trust yet.

### Can I self-host the whole thing?

Yes. It's a self-hosted approval gateway for AI agents: Docker Compose on your own box,
credentials encrypted at rest, and every staged send, approval, and rejection in an audit
log.

---

If this is the missing piece in your email agent, a ★ on
[the repo](https://github.com/agens-field/nuvrail) helps other builders find it.
