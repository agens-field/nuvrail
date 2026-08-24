# Claude Desktop → Nuvrail integration recipe

_Wire **Claude Desktop** to a mailbox **through Nuvrail's approval proxy**, end to
end: reads pass through instantly, and every write (send, move, delete, flag)
waits for your one-tap approval before it touches the real mail server._

> **TL;DR** — Claude Desktop talks to tools over **MCP**, not raw IMAP/SMTP. So
> you run a small **IMAP/SMTP MCP server** and point *it* at Nuvrail's proxy
> ports (not at Gmail/iCloud directly). Claude Desktop → MCP email server →
> Nuvrail → your real mail server. Nuvrail is invisible to Claude; it just sees
> a normal IMAP/SMTP server that happens to stage writes.

---

## Why this shape

Claude Desktop does not speak IMAP or SMTP. It speaks the **Model Context
Protocol (MCP)** — it launches MCP servers you configure and calls the tools
they expose. Nuvrail, on the other hand, *is* an IMAP/SMTP server (that's the
whole point — an agent's mail client can't tell it's there). The bridge between
the two is an **MCP server that offers email tools over IMAP/SMTP**. You give
that MCP server your **Nuvrail agent credentials** and the **Nuvrail proxy
host/port** — never your real mailbox credentials — and the approval layer does
the rest.

```
  Claude Desktop            MCP email server            Nuvrail proxy              Real mail server
  ──────────────    MCP     ────────────────  IMAP/SMTP  ─────────────  IMAP/SMTP  ────────────────
   "archive that   ───────► email tool over  ──────────► gateway       ──────────► (Gmail, iCloud,
    newsletter"    stdio    IMAP/SMTP         :993/:465   reads: pass    on approval  Outlook, any
                                                          through          only        IMAP/SMTP)
                                                          writes: STAGED
                                                              │
                                                     you approve/reject
                                                       (phone / browser)
```

Everything Claude can do without asking you (read, search, list) flows straight
through. Everything that changes the mailbox (send, move, delete, flag, create
folder) comes back to you as a one-tap approval. `EXPUNGE` is permanently
blocked at the gateway — Claude can never permanently delete a message.

---

## Before you start

You need:

1. **A running Nuvrail instance** with a mailbox connected. Follow the
   [60-second quickstart](../../README.md) to run Nuvrail locally, then connect
   the upstream mailbox with one of the provider guides:
   [Gmail](../providers/gmail.md) · [iCloud](../providers/icloud.md) ·
   [Outlook](../providers/outlook.md) · [generic IMAP/SMTP](../provider-imap-guide.md).
2. **Your Nuvrail agent credentials** — the one-time `agent_username`
   (e.g. `nuvrail_abc123`) and `agent_token` printed when you connected the
   mailbox. These are what Claude's MCP server logs in with. If you lost the
   token, reconnect the mailbox to mint a fresh one.
3. **Claude Desktop** installed (macOS or Windows), and **Node.js 18+** (the MCP
   server below runs via `npx`; a Python server would need Python 3.10+
   instead).

> **Never give the MCP server your real mailbox password.** The whole point of
> Nuvrail is that the agent side only ever holds the scoped, revocable Nuvrail
> agent token. If you ever need to cut Claude off, revoke that token in Nuvrail;
> your real mailbox credentials are untouched.

---

## Nuvrail proxy connection settings

These are the values the MCP server connects to — **the Nuvrail proxy, not your
mail provider**. Use whichever row matches your deployment:

| Setting        | Production Nuvrail            | Local trial (`docker compose up`) |
| -------------- | ---------------------------- | --------------------------------- |
| IMAP host      | `nuvrail.example.com`        | `localhost`                       |
| IMAP port      | `993` (implicit TLS)         | `10143` (**plaintext**, dev only) |
| SMTP host      | `nuvrail.example.com`        | `localhost`                       |
| SMTP port      | `465` (implicit TLS)         | `10587` (**plaintext**, dev only) |
| Username       | your Nuvrail `agent_username` | your Nuvrail `agent_username`     |
| Password       | your Nuvrail `agent_token`    | your Nuvrail `agent_token`        |

> **TLS caveat for the local trial.** The `docker compose` trial exposes the
> proxy on `10143`/`10587` **without TLS** for evaluation on `localhost` only.
> Most MCP email servers assume TLS by default, so for the local trial set the
> server's TLS/SSL option to `false` (shown below). For any real deployment use
> the production ports (`993`/`465`) with TLS on — see
> [Deploying to production](../../README.md#deploying-to-production).

---

## Step 1 — Locate your Claude Desktop config file

Claude Desktop reads MCP servers from `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

The fastest way to open it: in Claude Desktop go to **Settings → Developer →
Edit Config**. That creates the file if it doesn't exist and opens it in your
editor. If you edit it by hand, create the file (and any missing parent folders)
if it isn't there yet.

---

## Step 2 — Add an IMAP/SMTP MCP server pointed at Nuvrail

Claude Desktop launches MCP servers as local subprocesses and passes config via
`env`. Add an entry to `mcpServers` that runs an email MCP server and points it
at the **Nuvrail proxy** with your **Nuvrail agent credentials**.

The example below uses a community IMAP/SMTP MCP server run via `npx` (no global
install). Any MCP email server works — what matters is that its host/port/creds
point at Nuvrail, not at your provider. Adjust the package name and env-var
names to match the server you choose; check its README for the exact keys.

**Production Nuvrail (TLS on):**

```json
{
  "mcpServers": {
    "nuvrail-email": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-imap"],
      "env": {
        "IMAP_HOST": "nuvrail.example.com",
        "IMAP_PORT": "993",
        "IMAP_TLS": "true",
        "SMTP_HOST": "nuvrail.example.com",
        "SMTP_PORT": "465",
        "SMTP_TLS": "true",
        "EMAIL_USER": "nuvrail_abc123",
        "EMAIL_PASSWORD": "your-nuvrail-agent-token"
      }
    }
  }
}
```

**Local trial (plaintext dev ports — `localhost` only):**

```json
{
  "mcpServers": {
    "nuvrail-email": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-imap"],
      "env": {
        "IMAP_HOST": "localhost",
        "IMAP_PORT": "10143",
        "IMAP_TLS": "false",
        "SMTP_HOST": "localhost",
        "SMTP_PORT": "10587",
        "SMTP_TLS": "false",
        "EMAIL_USER": "nuvrail_abc123",
        "EMAIL_PASSWORD": "your-nuvrail-agent-token"
      }
    }
  }
}
```

Notes:

- **`command`/`args`** are just "how to launch the MCP server." If you use a
  Python-based email MCP server instead, this might be
  `"command": "uvx"` / `"command": "python"` with different `args` — follow that
  server's README.
- **The env-var names above (`IMAP_HOST`, `EMAIL_USER`, …) are illustrative.**
  Different MCP email servers use different names (`IMAP_USERNAME`, `MAIL_PASS`,
  etc.). The **values** are what matter: Nuvrail host, Nuvrail proxy port, your
  Nuvrail agent username, your Nuvrail agent token. Map them onto whatever keys
  your chosen server documents.
- **Don't commit this file to a repo.** It contains your Nuvrail agent token in
  plaintext. Treat it like any other credential file.

Save the file and **fully quit and reopen Claude Desktop** (a window reload is
not enough — it re-reads MCP config only on a cold start).

---

## Step 3 — Confirm Claude sees the tools

After restart, open a new chat in Claude Desktop and look for the tools /
plug icon (**Search and tools**) in the input area. Your `nuvrail-email` server
should be listed with its email tools. If it isn't:

- Open **Settings → Developer** and check the server's status. A red/errored
  state usually means the `command` couldn't launch (Node not installed, wrong
  package name) or the server crashed on bad config.
- Check the MCP logs:
  - **macOS:** `~/Library/Logs/Claude/mcp*.log`
  - **Windows:** `%APPDATA%\Claude\Logs\mcp*.log`

A connection refused / auth error in those logs points at the Nuvrail side —
see [Troubleshooting](#troubleshooting).

---

## Step 4 — End-to-end verification

Run these two checks in order. Together they prove reads pass through and writes
are staged — the whole reason Nuvrail is in the path.

### 4a — A read (should just work, no approval)

Ask Claude:

> "List the 5 most recent emails in my inbox — just subjects and senders."

Claude calls the MCP server's search/fetch tools, which run `SELECT INBOX` +
`UID SEARCH` + `FETCH` through Nuvrail. Reads pass through instantly, so you
should get the list back with **no approval prompt**. If this works, the wire is
good end to end.

### 4b — A write (should stage and wait for you)

Ask Claude to do something that changes the mailbox — e.g.:

> "Archive the newsletter from news@example.com."

or

> "Send a one-line test email to me@example.com saying 'Nuvrail test'."

What should happen:

1. Claude reports the action was taken (the MCP server received `OK [STAGED]`
   from Nuvrail and returns success to Claude — this is by design; the agent
   keeps working).
2. **You get a Nuvrail approval notification** (phone/browser PWA) showing the
   exact operation — the message, destination folder, or recipient.
3. Open the Nuvrail approval UI, review, and **Approve** — only then does it
   execute against the real mail server. **Reject** reverts and Claude is told
   on its next command.

If the write shows up as a pending operation in Nuvrail waiting for your tap,
the integration is working correctly: Claude can act, but nothing lands on your
real mailbox without you.

> You can also approve/reject from the API instead of the UI — see
> [Approving operations](../../README.md#approving-operations).

---

## What Claude can and can't do through Nuvrail

| Action                                          | Through Nuvrail                    |
| ----------------------------------------------- | --------------------------------- |
| Read / fetch a message                          | ✅ passes through instantly       |
| Search (`SEARCH`, `UID SEARCH`)                 | ✅ passes through instantly       |
| List folders (`LIST`, `LSUB`), `STATUS`, `NOOP` | ✅ passes through instantly       |
| Move / copy (`UID MOVE`, `UID COPY`)            | ⏸ staged — needs your approval    |
| Flag changes (`UID STORE`)                      | ⏸ staged — needs your approval    |
| Send outbound email (SMTP `DATA`)               | ⏸ staged — needs your approval    |
| Create / rename a folder                        | ⏸ staged — needs your approval    |
| Permanent delete (`EXPUNGE`)                    | 🚫 permanently blocked at gateway |

This mapping is enforced by Nuvrail regardless of what the MCP server or Claude
tries — the approval boundary is in the proxy, not in the client.

---

## Troubleshooting

| Symptom                                                        | Likely cause / fix                                                                                                                                                        |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nuvrail-email` server not listed in Claude after restart      | JSON syntax error in `claude_desktop_config.json`, or you reloaded instead of a full quit+reopen. Validate the JSON and cold-restart Claude.                              |
| Server shows errored in **Settings → Developer**               | `command` couldn't launch — Node/`npx` not on PATH, or wrong package name. Check the `mcp*.log` files (paths in Step 3).                                                  |
| Connection refused / timeout in MCP logs                       | Wrong host/port, or Nuvrail isn't running. Confirm Nuvrail is up (`curl http://localhost:8080/health` for the local trial) and the port matches the table in this guide. |
| TLS / certificate error on the local trial                     | The local dev ports `10143`/`10587` are **plaintext** — set the server's TLS option to `false` for the trial. Use `993`/`465` with TLS only against a production deploy. |
| Auth fails (login rejected)                                    | You're using your real mailbox password or a stale token. Use the **Nuvrail agent username + agent token**; reconnect the mailbox in Nuvrail to mint a fresh token.      |
| Reads work but writes never prompt you                         | Notifications not enabled on the Nuvrail PWA. Open the approval UI directly (`https://nuvrail.example.com`) — the pending op will be there; enable PWA notifications.     |
| A staged op vanished / Claude got a rejection it didn't expect | Pending operations **expire after 48h** if not acted on — the proxy reverts and reports a rejection to the agent. Approve sooner, or ask Claude to retry.                 |

---

## Security notes

- **Claude only ever holds the Nuvrail agent token**, which is scoped to one
  mailbox and revocable in Nuvrail without touching your real credentials. That
  is the point of the proxy — the blast radius of a leaked token is "someone can
  *stage* operations you still have to approve," not "someone owns your inbox."
- **Nothing is deleted, ever.** `EXPUNGE` is blocked at the gateway; the worst a
  compromised or confused agent can do is *stage* a move-to-Trash that you then
  reject.
- **Keep `claude_desktop_config.json` out of version control** — it holds the
  token in plaintext. If it leaks, revoke the agent token in Nuvrail and mint a
  new one.

---

_See also: [Cursor integration recipe](cursor.md) ·
[Configuring your AI agent](../../README.md#configuring-your-ai-agent) ·
[Approving operations](../../README.md#approving-operations) ·
provider guides — [Gmail](../providers/gmail.md) · [iCloud](../providers/icloud.md) · [Outlook](../providers/outlook.md) · [generic IMAP/SMTP](../provider-imap-guide.md)._
