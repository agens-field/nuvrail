# Cursor → Nuvrail integration recipe

_Wire **Cursor** to a mailbox **through Nuvrail's approval proxy**, end to end:
reads pass through instantly, and every write (send, move, delete, flag) waits
for your one-tap approval before it touches the real mail server._

> **TL;DR** — Cursor's agent talks to tools over **MCP**, not raw IMAP/SMTP. So
> you run a small **IMAP/SMTP MCP server** and point *it* at Nuvrail's proxy
> ports (not at Gmail/iCloud directly). Cursor → MCP email server → Nuvrail →
> your real mail server. Nuvrail is invisible to Cursor; it just sees a normal
> IMAP/SMTP server that happens to stage writes.

---

## Why this shape

Cursor's agent does not speak IMAP or SMTP. It speaks the **Model Context
Protocol (MCP)** — it launches the MCP servers you configure and calls the tools
they expose. Nuvrail, on the other hand, *is* an IMAP/SMTP server (that's the
whole point — an agent's mail client can't tell it's there). The bridge between
the two is an **MCP server that offers email tools over IMAP/SMTP**. You give
that MCP server your **Nuvrail agent credentials** and the **Nuvrail proxy
host/port** — never your real mailbox credentials — and the approval layer does
the rest.

```
      Cursor                MCP email server            Nuvrail proxy              Real mail server
  ──────────────    MCP     ────────────────  IMAP/SMTP  ─────────────  IMAP/SMTP  ────────────────
   "archive that   ───────► email tool over  ──────────► gateway       ──────────► (Gmail, iCloud,
    newsletter"    stdio    IMAP/SMTP         :993/:465   reads: pass    on approval  Outlook, any
                                                          through          only        IMAP/SMTP)
                                                          writes: STAGED
                                                              │
                                                     you approve/reject
                                                       (phone / browser)
```

Everything Cursor can do without asking you (read, search, list) flows straight
through. Everything that changes the mailbox (send, move, delete, flag, create
folder) comes back to you as a one-tap approval. `EXPUNGE` is permanently
blocked at the gateway — Cursor can never permanently delete a message.

> **This is a second, independent approval boundary from Cursor's own.** Cursor
> already asks you before it runs a tool (unless you've turned on auto-run /
> YOLO mode). Nuvrail's staging is *underneath* that: even if Cursor is set to
> auto-run tools, a write still stages at the proxy and waits for **your**
> approval. Turning on Cursor auto-run does not bypass Nuvrail.

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
   mailbox. These are what Cursor's MCP server logs in with. If you lost the
   token, reconnect the mailbox to mint a fresh one.
3. **Cursor** installed, and **Node.js 18+** (the MCP server below runs via
   `npx`; a Python server would need Python 3.10+ instead).

> **Never give the MCP server your real mailbox password.** The whole point of
> Nuvrail is that the agent side only ever holds the scoped, revocable Nuvrail
> agent token. If you ever need to cut Cursor off, revoke that token in Nuvrail;
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

## Step 1 — Locate your Cursor MCP config file

Cursor reads MCP servers from an `mcp.json` file. You choose the scope:

- **Global (every project):** `~/.cursor/mcp.json`
  (Windows: `%USERPROFILE%\.cursor\mcp.json`)
- **Project-only (this repo/folder):** `<project-root>/.cursor/mcp.json`

The fastest way to create it: in Cursor open **Settings → Tools & MCP → New MCP
Server**. That drops you straight into `mcp.json`, creating the file (and any
missing parent folders) if it doesn't exist. You can also create the file by
hand.

> **Global vs project.** Global config makes the mailbox tools available in
> every Cursor window. Project config keeps them scoped to one folder — handy if
> only one project should touch mail. Both use the identical JSON below; they
> differ only in file location.

---

## Step 2 — Add an IMAP/SMTP MCP server pointed at Nuvrail

Add an entry to `mcpServers` that runs an email MCP server and points it at the
**Nuvrail proxy** with your **Nuvrail agent credentials**. Cursor uses the same
`mcpServers` object shape as other MCP clients.

The example below uses a community IMAP/SMTP MCP server run via `npx` (no global
install). Any MCP email server works — what matters is that its host/port/creds
point at Nuvrail, not at your provider. Adjust the package name and env-var names
to match the server you choose; check its README for the exact keys.

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
- **Don't commit a project `.cursor/mcp.json` that contains the token.** It
  holds your Nuvrail agent token in plaintext. If you use project scope, add
  `.cursor/mcp.json` to `.gitignore`, or keep the credentialed config global
  (`~/.cursor/mcp.json`).

Save the file. Cursor picks up new MCP servers on save, but if the server
doesn't appear, toggle it off/on in **Settings → Tools & MCP** or reload the
window.

---

## Step 3 — Confirm Cursor sees the tools

Open **Settings → Tools & MCP**. Your `nuvrail-email` server should be listed
with a green/enabled status and its email tools expanded underneath. If it
isn't:

- A red/errored state usually means the `command` couldn't launch (Node not
  installed, wrong package name) or the server crashed on bad config. Expand the
  server row for its error output.
- Make sure the server toggle is **enabled** — a newly added server can land
  disabled.
- Open a fresh **Agent** chat (MCP tools are called from Agent mode, not from
  inline edits) and confirm the tools are offered.

A connection refused / auth error points at the Nuvrail side — see
[Troubleshooting](#troubleshooting).

---

## Step 4 — End-to-end verification

Run these two checks in order, in a Cursor **Agent** chat. Together they prove
reads pass through and writes are staged — the whole reason Nuvrail is in the
path.

### 4a — A read (should just work, no Nuvrail approval)

Ask the Cursor agent:

> "List the 5 most recent emails in my inbox — just subjects and senders."

Cursor calls the MCP server's search/fetch tools, which run `SELECT INBOX` +
`UID SEARCH` + `FETCH` through Nuvrail. Reads pass through instantly, so you get
the list back with **no Nuvrail approval prompt**. (Cursor itself may ask you to
allow the tool call the first time — that's Cursor's own permission, not
Nuvrail's; approve it.) If this works, the wire is good end to end.

### 4b — A write (should stage and wait for you)

Ask Cursor to do something that changes the mailbox — e.g.:

> "Archive the newsletter from news@example.com."

or

> "Send a one-line test email to me@example.com saying 'Nuvrail test'."

What should happen:

1. Cursor reports the action was taken (the MCP server received `OK [STAGED]`
   from Nuvrail and returns success to Cursor — this is by design; the agent
   keeps working).
2. **You get a Nuvrail approval notification** (phone/browser PWA) showing the
   exact operation — the message, destination folder, or recipient.
3. Open the Nuvrail approval UI, review, and **Approve** — only then does it
   execute against the real mail server. **Reject** reverts and Cursor is told
   on its next command.

If the write shows up as a pending operation in Nuvrail waiting for your tap,
the integration is working correctly: Cursor can act, but nothing lands on your
real mailbox without you — even if you've enabled Cursor's auto-run.

> You can also approve/reject from the API instead of the UI — see
> [Approving operations](../../README.md#approving-operations).

---

## What Cursor can and can't do through Nuvrail

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

This mapping is enforced by Nuvrail regardless of what the MCP server or Cursor
tries — the approval boundary is in the proxy, not in the client. In particular,
turning on Cursor's **auto-run / YOLO** mode does not weaken it: auto-run only
skips *Cursor's* confirmation, and the write still stages at Nuvrail for you.

---

## Troubleshooting

| Symptom                                                        | Likely cause / fix                                                                                                                                                        |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nuvrail-email` server not listed in Cursor                    | JSON syntax error in `mcp.json`, wrong file location, or the server toggle is off. Validate the JSON, confirm the path (`~/.cursor/mcp.json` or `<project>/.cursor/mcp.json`), enable it in **Settings → Tools & MCP**. |
| Server shows errored in **Settings → Tools & MCP**             | `command` couldn't launch — Node/`npx` not on PATH, or wrong package name. Expand the server row for its error output.                                                    |
| Tools listed but never called                                  | You're in inline-edit or Ask mode. MCP tools are invoked from **Agent** mode — start an Agent chat.                                                                       |
| Connection refused / timeout                                   | Wrong host/port, or Nuvrail isn't running. Confirm Nuvrail is up (`curl http://localhost:8080/health` for the local trial) and the port matches the table in this guide. |
| TLS / certificate error on the local trial                     | The local dev ports `10143`/`10587` are **plaintext** — set the server's TLS option to `false` for the trial. Use `993`/`465` with TLS only against a production deploy. |
| Auth fails (login rejected)                                    | You're using your real mailbox password or a stale token. Use the **Nuvrail agent username + agent token**; reconnect the mailbox in Nuvrail to mint a fresh token.      |
| Reads work but writes never prompt you                         | Notifications not enabled on the Nuvrail PWA. Open the approval UI directly (`https://nuvrail.example.com`) — the pending op will be there; enable PWA notifications.     |
| A staged op vanished / Cursor got a rejection it didn't expect | Pending operations **expire after 48h** if not acted on — the proxy reverts and reports a rejection to the agent. Approve sooner, or ask Cursor to retry.                 |

---

## Security notes

- **Cursor only ever holds the Nuvrail agent token**, which is scoped to one
  mailbox and revocable in Nuvrail without touching your real credentials. That
  is the point of the proxy — the blast radius of a leaked token is "someone can
  *stage* operations you still have to approve," not "someone owns your inbox."
- **Nothing is deleted, ever.** `EXPUNGE` is blocked at the gateway; the worst a
  compromised or confused agent can do is *stage* a move-to-Trash that you then
  reject.
- **Keep your token out of version control.** A global `~/.cursor/mcp.json` is
  outside your repos; a project `.cursor/mcp.json` is not — `.gitignore` it if it
  holds the token. If it leaks, revoke the agent token in Nuvrail and mint a new
  one.
- **Cursor auto-run does not bypass the approval boundary.** Nuvrail's staging
  is enforced in the proxy, independent of Cursor's own tool-confirmation
  setting.

---

_See also: [Claude Desktop integration recipe](claude-desktop.md) ·
[Configuring your AI agent](../../README.md#configuring-your-ai-agent) ·
[Approving operations](../../README.md#approving-operations) ·
provider guides — [Gmail](../providers/gmail.md) · [iCloud](../providers/icloud.md) · [Outlook](../providers/outlook.md) · [generic IMAP/SMTP](../provider-imap-guide.md)._
