# How to stop an AI agent from deleting your emails

*Short answer: don't try to make the agent more careful. Put something between the
agent and the mailbox that won't forward a permanent delete, and that holds every
other change until you say yes. This guide shows why that's the only fix that
actually holds, and how to set it up with an open-source IMAP proxy in about 15
minutes.*

---

## The fear, stated plainly

You gave an agent access to your inbox so it could triage, file, and clean up. Somewhere
in the back of your mind is a question you haven't answered: **what stops it from deleting
something I need?**

For most setups the honest answer is: nothing. If the agent holds a normal IMAP login or
an OAuth token with mail scope, it can do anything your mail client can do, including the
one thing a mail client almost never does on its own: **permanently delete**.

## How an agent actually loses your mail

It doesn't take a rogue model or a prompt injection. It takes a normal instruction with a
slightly wrong reading:

1. You say "clean up resolved threads."
2. The agent decides the onboarding thread with your biggest customer is "resolved."
3. It flags the messages `\Deleted`, then sends IMAP `EXPUNGE`.

`EXPUNGE` is not "move to Trash." It removes flagged messages from the server. There is no
Trash to recover from and no undo. The agent did exactly what it was told. It just read
"resolved" differently than you would have.

The longer version, with sends, reply-all, and data leaking out, is in
[Why email is the most dangerous thing you can give an AI agent](../why-email-is-dangerous.md).

## Why the usual fixes don't stop deletes

- **Telling the agent "never delete anything."** That's a request, not a control. The
  thing you're guarding against is the thing you're asking to guard itself.
- **Scoped OAuth.** Mail scopes are coarse. A scope that lets an agent move and label mail
  usually lets it delete too. No provider scope means "may delete, but only after I approve
  this specific message."
- **Read-only access.** It stops deletes, and it also stops the agent from doing anything
  useful. An inbox agent that can't file or archive is a search box.
- **Backups.** Worth having, but restoring from a backup is recovery after the damage, not
  prevention. And most people find out a message is gone weeks later.

What's missing is a checkpoint **at the moment of the write**, outside the agent.

## The fix: an IMAP proxy that requires approval to delete

Put a proxy between the agent and your real mail server. To the agent it looks like an
ordinary IMAP/SMTP server, so no SDK and no code changes are needed. The proxy sorts every
command by consequence:

- **Reads pass straight through.** `FETCH`, `SEARCH`, `LIST`. The agent stays fast.
- **Changes are staged.** A move, a flag change, or an outbound send returns
  `OK [STAGED]` right away, so the agent keeps working. Nothing touches the real mailbox yet.
- **You decide.** You get a notification showing what the agent wants to do (subject,
  sender, destination) and approve or reject it. In the open-source build, nothing runs
  until you approve it.
- **Permanent delete doesn't exist.** `EXPUNGE` is never forwarded to the real server.
  `\Deleted` is rewritten into a staged move-to-Trash. The worst an agent can do is ask to
  move a message to Trash, and that waits for your yes too.

That's what [Nuvrail](https://github.com/agens-field/nuvrail) is: an open-source (AGPL-3.0),
self-hosted IMAP/SMTP proxy built on exactly this model. The `EXPUNGE` block lives in
[`gateway/`](../../gateway). Read the code rather than trusting this page.

## Set it up (self-host, ~15 minutes)

You need Docker with the Compose plugin. No domain or TLS needed to try it locally.

```bash
git clone https://github.com/agens-field/nuvrail.git
cd nuvrail
cp .env.example .env
docker compose up --build
```

The first build compiles everything, so a cold clone typically takes **5 to 9 minutes**.
Later starts take seconds. Sign-up is closed by default, so create your account from the
CLI:

```bash
docker compose exec gateway python3 scripts/manage_users.py create you@example.com --name "You"
```

Open `http://localhost:3000`, log in, connect your mailbox, and point your agent's IMAP/SMTP
settings at the proxy (`localhost:10143` / `localhost:10587`) instead of your provider. The
next time the agent tries to delete something, it shows up in the approval screen instead
of vanishing.

Wiring a specific agent: [Claude Desktop](../integrations/claude-desktop.md) ·
[Cursor](../integrations/cursor.md) · [LangChain](../integrations/langchain.md). Running it
for real (TLS, a mailbox reachable from another machine) is covered in the
[README](../../README.md#deploying-to-production).

## FAQ

### How do I block EXPUNGE / prevent permanent email deletion over IMAP?

Most mail servers let any authenticated client expunge, and you usually can't turn that off per
client. The reliable way is to make sure the agent never talks to the server directly. A
proxy in the middle can refuse to forward `EXPUNGE` and turn `\Deleted` into a reversible
move-to-Trash. That's the Nuvrail model.

### Will the agent break if its deletes are blocked?

No. The proxy answers `OK [STAGED]`, so from the agent's side the command worked. If you
reject the change, the proxy rolls back its local view and tells the agent on its next
command.

### Can I see what the agent tried to do?

Yes. Every staged action, approval, and rejection is written to an audit log. That's also
how you answer "how do I audit what an AI agent does to my email?"

### Isn't approving every change tedious?

Less than you'd think. Reads never wait, so the agent reads and searches freely; only
changes queue up. When a cleanup run stages a pile of moves at once, you can approve or
reject them as a batch instead of one by one.

---

If this saved you from finding out the hard way, a ★ on
[the repo](https://github.com/agens-field/nuvrail) helps the next person building an email
agent find it first.
