# LangChain → Nuvrail integration recipe

_Give a **LangChain agent** a mailbox **through Nuvrail's approval proxy**, end to
end: reads pass through instantly, and every write (send, move, delete, flag)
waits for your one-tap approval before it touches the real mail server._

> **TL;DR** — LangChain speaks **IMAP/SMTP natively** (via `imaplib` /
> `smtplib`, or a community email toolkit), so there is no MCP bridge here.
> You build normal LangChain `Tool`s over IMAP/SMTP and point their
> connection at **Nuvrail's proxy ports** instead of at Gmail/iCloud directly.
> Your LangChain agent → IMAP/SMTP tools → Nuvrail → your real mail server.
> Nuvrail is invisible to the agent; it just sees a normal mail server that
> happens to stage writes.

---

## Why this shape

Unlike Claude Desktop or Cursor (which reach tools over MCP — see those
recipes), a LangChain agent runs **in your own Python process** and calls
whatever tools you hand it. Email is just another tool. The two common shapes:

- **A community email toolkit** — e.g. LangChain's `GmailToolkit`, or any
  IMAP/SMTP-based email tool. These ultimately open an IMAP/SMTP connection;
  you point that connection at Nuvrail.
- **A pair of thin `Tool`s you write** over Python's stdlib `imaplib` /
  `smtplib` — the shape shown below, because it makes the one thing that
  matters explicit: **the host/port/creds go to Nuvrail, never to your
  provider.**

Either way the rule is the same: the agent's mail connection terminates at the
**Nuvrail proxy**, authenticated with your **Nuvrail agent credentials** — never
your real mailbox password. The approval layer does the rest.

```
   LangChain agent            your IMAP/SMTP tools         Nuvrail proxy              Real mail server
  ──────────────────  call    ──────────────────  IMAP/SMTP  ─────────────  IMAP/SMTP  ────────────────
   "archive that     ───────► imaplib / smtplib  ──────────► gateway       ──────────► (Gmail, iCloud,
    newsletter"      in-proc  bound to Nuvrail   :993/:465   reads: pass    on approval  Outlook, any
                              host/port + agent              through          only        IMAP/SMTP)
                              token                          writes: STAGED
                                                                 │
                                                        you approve/reject
                                                          (phone / browser)
```

Everything the agent can do without asking you (read, search, list) flows
straight through. Everything that changes the mailbox (send, move, delete,
flag, create folder) comes back to you as a one-tap approval. `EXPUNGE` is
permanently blocked at the gateway — the agent can never permanently delete a
message.

> **Nuvrail's boundary is independent of LangChain's own controls.** Whether you
> run the agent with a human-in-the-loop callback, `return_direct`, or fully
> autonomously in a loop, a write still stages at the proxy and waits for
> **your** approval. Running the LangChain agent unattended does not bypass
> Nuvrail — that is the point of putting the boundary in the proxy, not in the
> client.

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
   mailbox. These are what your LangChain tools log in with. If you lost the
   token, reconnect the mailbox to mint a fresh one.
3. **Python 3.10+** and LangChain installed:

   ```bash
   pip install langchain langchain-openai
   ```

   (Any chat model provider works; `langchain-openai` is used in the example.
   `imaplib` and `smtplib` are in the standard library — no extra install.)

> **Never give your LangChain tools your real mailbox password.** The whole
> point of Nuvrail is that the agent side only ever holds the scoped, revocable
> Nuvrail agent token. If you ever need to cut the agent off, revoke that token
> in Nuvrail; your real mailbox credentials are untouched.

---

## Nuvrail proxy connection settings

These are the values your tools connect to — **the Nuvrail proxy, not your mail
provider**. Use whichever row matches your deployment:

| Setting     | Production Nuvrail            | Local trial (`docker compose up`) |
| ----------- | ----------------------------- | --------------------------------- |
| IMAP host   | `nuvrail.example.com`         | `localhost`                       |
| IMAP port   | `993` (implicit TLS)          | `10143` (**plaintext**, dev only) |
| SMTP host   | `nuvrail.example.com`         | `localhost`                       |
| SMTP port   | `465` (implicit TLS)          | `10587` (**plaintext**, dev only) |
| Username    | your Nuvrail `agent_username` | your Nuvrail `agent_username`     |
| Password    | your Nuvrail `agent_token`    | your Nuvrail `agent_token`        |

> **TLS caveat for the local trial.** The `docker compose` trial exposes the
> proxy on `10143`/`10587` **without TLS** for evaluation on `localhost` only —
> so the local example below uses `imaplib.IMAP4` / `smtplib.SMTP` (plaintext).
> For any real deployment use the production ports (`993`/`465`) with
> `imaplib.IMAP4_SSL` / `smtplib.SMTP_SSL` — see
> [Deploying to production](../../README.md#deploying-to-production).

---

## Step 1 — Put the Nuvrail connection in your environment

Keep the token out of your source. Read it from the environment:

```bash
# Production Nuvrail (TLS on)
export NUVRAIL_IMAP_HOST=nuvrail.example.com
export NUVRAIL_IMAP_PORT=993
export NUVRAIL_SMTP_HOST=nuvrail.example.com
export NUVRAIL_SMTP_PORT=465
export NUVRAIL_TLS=true
export NUVRAIL_USER=nuvrail_abc123
export NUVRAIL_TOKEN=your-nuvrail-agent-token
export OPENAI_API_KEY=sk-...            # or your model provider's key
```

For the **local trial**, use the plaintext dev ports and turn TLS off:

```bash
export NUVRAIL_IMAP_HOST=localhost
export NUVRAIL_IMAP_PORT=10143
export NUVRAIL_SMTP_HOST=localhost
export NUVRAIL_SMTP_PORT=10587
export NUVRAIL_TLS=false
export NUVRAIL_USER=nuvrail_abc123
export NUVRAIL_TOKEN=your-nuvrail-agent-token
```

---

## Step 2 — Build IMAP/SMTP tools pointed at Nuvrail

Three small tools cover the demo: **list recent** and **search** (reads, pass
through) and **send** (a write, staged for your approval). The connection
helpers select TLS vs. plaintext from `NUVRAIL_TLS`, so the same code runs
against the local trial and production.

```python
# nuvrail_langchain.py
import email
import imaplib
import os
import smtplib
from email.message import EmailMessage

from langchain.agents import AgentType, initialize_agent
from langchain.tools import Tool
from langchain_openai import ChatOpenAI

# --- Nuvrail connection (proxy host/port + AGENT token, never your provider) ---
IMAP_HOST = os.environ["NUVRAIL_IMAP_HOST"]
IMAP_PORT = int(os.environ["NUVRAIL_IMAP_PORT"])
SMTP_HOST = os.environ["NUVRAIL_SMTP_HOST"]
SMTP_PORT = int(os.environ["NUVRAIL_SMTP_PORT"])
USER = os.environ["NUVRAIL_USER"]
TOKEN = os.environ["NUVRAIL_TOKEN"]
TLS = os.environ.get("NUVRAIL_TLS", "true").lower() == "true"


def _imap():
    """Open an authenticated IMAP connection to the Nuvrail proxy."""
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) if TLS \
        else imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    conn.login(USER, TOKEN)          # Nuvrail agent creds, not your mailbox pw
    return conn


def list_recent(n: str = "5") -> str:
    """Read tool: subjects + senders of the N most recent inbox messages.
    A pure read — Nuvrail passes it through instantly, no approval."""
    conn = _imap()
    try:
        conn.select("INBOX")
        _, data = conn.search(None, "ALL")
        ids = data[0].split()[-int(n or 5):]
        out = []
        for msg_id in reversed(ids):
            _, msg_data = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            hdr = email.message_from_bytes(msg_data[0][1])
            out.append(f"- {hdr.get('Subject', '(no subject)')} — {hdr.get('From', '?')}")
        return "\n".join(out) or "(inbox empty)"
    finally:
        conn.logout()


def search_inbox(query: str) -> str:
    """Read tool: IMAP SEARCH by substring in the subject. Passes through."""
    conn = _imap()
    try:
        conn.select("INBOX")
        # IMAP SEARCH is server-side; Nuvrail forwards it unchanged.
        _, data = conn.search(None, "SUBJECT", f'"{query}"')
        ids = data[0].split()
        return f"{len(ids)} match(es) for {query!r} in subject." if ids \
            else f"No matches for {query!r}."
    finally:
        conn.logout()


def send_email(spec: str) -> str:
    """Write tool: send mail. Input is 'to|subject|body'. Nuvrail STAGES this —
    it returns OK immediately but nothing is sent until you approve in Nuvrail."""
    try:
        to_addr, subject, body = (part.strip() for part in spec.split("|", 2))
    except ValueError:
        return "Bad input. Use 'to@example.com|Subject|Body text'."
    msg = EmailMessage()
    msg["From"] = USER
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) if TLS \
        else smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    try:
        server.login(USER, TOKEN)
        server.send_message(msg)     # Nuvrail returns OK [STAGED] here
    finally:
        server.quit()
    return ("Handed to Nuvrail. It is STAGED, not sent — approve it in the "
            "Nuvrail UI / PWA and only then does it leave for the real server.")


TOOLS = [
    Tool("list_recent_emails", list_recent,
         "List the N most recent inbox emails (subjects + senders). Input: a number."),
    Tool("search_inbox", search_inbox,
         "Search the inbox subject line for a term. Input: the search term."),
    Tool("send_email", send_email,
         "Send an email. Input EXACTLY 'to@example.com|Subject line|Body text'."),
]

if __name__ == "__main__":
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    agent = initialize_agent(
        TOOLS, llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION, verbose=True,
    )
    # A read (passes through) then a write (stages for your approval):
    print(agent.run("List my 5 most recent emails, then send a one-line "
                    "test email to me@example.com saying 'Nuvrail + LangChain test'."))
```

Notes:

- **The connection helpers are the whole integration.** `_imap()` and the SMTP
  block point at the **Nuvrail** host/port with the **Nuvrail agent token**.
  Swap in `GmailToolkit` or any other email toolkit and the only thing that
  changes is *which library opens the socket* — the host/port/creds it must be
  given are still Nuvrail's.
- **`login(USER, TOKEN)` uses your Nuvrail agent username + token.** If you find
  yourself typing your real Gmail/iCloud password here, stop — that defeats the
  proxy.
- **`EXPUNGE` isn't a tool on purpose.** Even if you added one, the gateway
  blocks it. Nothing you build client-side can permanently delete mail.

---

## Step 3 — Run it and watch writes stage

```bash
python nuvrail_langchain.py
```

The read (`list_recent_emails` / `search_inbox`) returns immediately — Nuvrail
forwards reads with no approval. The `send_email` call returns success to the
agent right away too (Nuvrail answers `OK [STAGED]`), **but the mail has not
gone anywhere yet.**

---

## Step 4 — End-to-end verification

Run these two checks. Together they prove reads pass through and writes are
staged — the whole reason Nuvrail is in the path.

### 4a — A read (should just work, no approval)

The agent's `list_recent_emails` / `search_inbox` calls run `SELECT INBOX` +
`SEARCH` + `FETCH` through Nuvrail. You should get results back with **no
approval prompt**. If this works, the wire is good end to end.

### 4b — A write (should stage and wait for you)

When the agent calls `send_email`:

1. The tool returns success to the agent (the SMTP server accepted the message
   — Nuvrail returned `OK [STAGED]`; the agent keeps working). This is by
   design.
2. **You get a Nuvrail approval notification** (phone/browser PWA) showing the
   exact operation — recipient, subject, body.
3. Open the Nuvrail approval UI, review, and **Approve** — only then does it
   actually send. **Reject** reverts and the agent is told on its next command.

If the send shows up as a pending operation in Nuvrail waiting for your tap, the
integration is working correctly: the LangChain agent can act, but nothing lands
on your real mailbox without you.

> You can also approve/reject from the API instead of the UI — see
> [Approving operations](../../README.md#approving-operations).

---

## What a LangChain agent can and can't do through Nuvrail

| Action                                          | Through Nuvrail                   |
| ----------------------------------------------- | --------------------------------- |
| Read / fetch a message                          | ✅ passes through instantly       |
| Search (`SEARCH`, `UID SEARCH`)                 | ✅ passes through instantly       |
| List folders (`LIST`, `LSUB`), `STATUS`, `NOOP` | ✅ passes through instantly       |
| Move / copy (`UID MOVE`, `UID COPY`)            | ⏸ staged — needs your approval    |
| Flag changes (`UID STORE`)                      | ⏸ staged — needs your approval    |
| Send outbound email (SMTP `DATA`)               | ⏸ staged — needs your approval    |
| Create / rename a folder                        | ⏸ staged — needs your approval    |
| Permanent delete (`EXPUNGE`)                    | 🚫 permanently blocked at gateway |

This mapping is enforced by Nuvrail regardless of what your tools or the agent
try — the approval boundary is in the proxy, not in your Python.

---

## Troubleshooting

| Symptom                                          | Likely cause / fix                                                                                                                                                      |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ConnectionRefusedError` on connect              | Wrong host/port, or Nuvrail isn't running. Confirm Nuvrail is up (`curl http://localhost:8080/health` for the local trial) and the port matches the table above.       |
| `ssl.SSLError` / `WRONG_VERSION_NUMBER`          | You used `IMAP4_SSL`/`SMTP_SSL` against the **plaintext** local dev ports. Set `NUVRAIL_TLS=false` for the trial; use TLS only against `993`/`465` on a real deploy.    |
| `imaplib.error: ... AUTHENTICATIONFAILED`        | You're using your real mailbox password or a stale token. Use the **Nuvrail agent username + agent token**; reconnect the mailbox in Nuvrail to mint a fresh token.     |
| Reads work but a send never prompts you          | Notifications not enabled on the Nuvrail PWA. Open the approval UI directly — the pending op will be there; enable PWA notifications.                                    |
| Agent reports "sent" but recipient never got it  | Correct behavior until you approve. Nuvrail returned `OK [STAGED]`; the message waits for your tap. Approve it in the UI, or it expires (and reverts) after 48h.         |
| A staged op vanished / agent got a surprise error | Pending operations **expire after 48h** if not acted on — the proxy reverts and reports a rejection to the agent on its next command. Approve sooner, or re-run.        |

---

## Security notes

- **Your LangChain tools only ever hold the Nuvrail agent token**, scoped to one
  mailbox and revocable in Nuvrail without touching your real credentials. The
  blast radius of a leaked token is "someone can *stage* operations you still
  have to approve," not "someone owns your inbox."
- **Nothing is deleted, ever.** `EXPUNGE` is blocked at the gateway; the worst a
  looping or confused agent can do is *stage* a move-to-Trash that you reject.
- **Keep the token out of source.** Read it from the environment (as above), not
  a committed config. If it leaks, revoke the agent token in Nuvrail and mint a
  new one.

---

_See also: [Claude Desktop integration recipe](claude-desktop.md) ·
[Cursor integration recipe](cursor.md) ·
[Configuring your AI agent](../../README.md#configuring-your-ai-agent) ·
[Approving operations](../../README.md#approving-operations) ·
provider guides — [Gmail](../providers/gmail.md) · [iCloud](../providers/icloud.md) · [Outlook](../providers/outlook.md) · [generic IMAP/SMTP](../provider-imap-guide.md)._
