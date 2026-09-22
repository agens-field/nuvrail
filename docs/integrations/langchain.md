# LangChain → Nuvrail integration recipe

_Give a **LangChain** agent a mailbox **through Nuvrail's approval proxy**, end
to end: reads pass through instantly, and every write (send, move, delete, flag)
waits for your one-tap approval before it touches the real mail server._

> **TL;DR** — LangChain agents call **tools you write**, and email tools are
> just thin wrappers over Python's stdlib `imaplib` / `smtplib`. Nuvrail *is* an
> IMAP/SMTP server, so — unlike an MCP client — **there is no bridge to run**.
> Point the same `imaplib` / `smtplib` code you'd normally aim at Gmail at the
> **Nuvrail proxy** instead, and every write your agent makes stages for your
> approval. LangChain agent → your `@tool` functions → Nuvrail → real mail server.

---

## Why this shape

A LangChain agent doesn't speak a wire protocol at all — it calls **tools**, and
you decide what a tool does. The idiomatic way to give an agent email is a
handful of `@tool` functions that open an IMAP or SMTP connection with the
Python standard library (`imaplib`, `smtplib`) and do one thing each: search,
fetch, send, move.

That is exactly what makes Nuvrail a drop-in here. Nuvrail presents a **normal
IMAP/SMTP server** — the agent's mail code can't tell it's there. So you take the
email tools you'd point at Gmail/iCloud and change three things: **host, port,
and credentials** → the Nuvrail proxy and your Nuvrail agent token. Nothing else
in your agent changes, and now every write is staged.

```
     LangChain agent            your @tool functions          Nuvrail proxy              Real mail server
  ──────────────────  calls   ────────────────────  IMAP/SMTP  ─────────────  IMAP/SMTP  ────────────────
   "archive that      ──────► imaplib / smtplib     ──────────► gateway       ──────────► (Gmail, iCloud,
    newsletter"       tool     over a socket        :993/:465   reads: pass    on approval  Outlook, any
                       call    (stdlib, no bridge)              through          only        IMAP/SMTP)
                                                                writes: STAGED
                                                                    │
                                                           you approve/reject
                                                             (phone / browser)
```

Everything the agent can do without asking you (read, search, list) flows
straight through. Everything that changes the mailbox (send, move, delete, flag,
create folder) comes back to you as a one-tap approval. `EXPUNGE` is permanently
blocked at the gateway — the agent can never permanently delete a message.

> **This is an approval boundary the agent framework cannot turn off.** A
> LangChain agent decides on its own which tools to call — there is no built-in
> human confirmation before a tool runs. Nuvrail's staging sits *underneath* the
> tool: even a fully autonomous agent that calls `send_email` on its own still
> stages the send at the proxy and waits for **your** approval. The safety does
> not depend on the agent, the prompt, or the model behaving.

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
   mailbox. These are what your tools log in with. If you lost the token,
   reconnect the mailbox to mint a fresh one.
3. **Python 3.10+** and the LangChain packages for your model. This recipe uses
   OpenAI as an example; swap in any chat model LangChain supports.

   ```bash
   pip install langchain langchain-openai
   ```

   The email tools themselves need **no extra dependency** — `imaplib`,
   `smtplib`, and `email` are all in the Python standard library.

> **Never give your tools the real mailbox password.** The whole point of
> Nuvrail is that the agent side only ever holds the scoped, revocable Nuvrail
> agent token. If you ever need to cut the agent off, revoke that token in
> Nuvrail; your real mailbox credentials are untouched.

---

## Nuvrail proxy connection settings

These are the values your tools connect to — **the Nuvrail proxy, not your mail
provider**. Use whichever row matches your deployment:

| Setting        | Production Nuvrail            | Local trial (`docker compose up`) |
| -------------- | ---------------------------- | --------------------------------- |
| IMAP host      | `nuvrail.example.com`        | `localhost`                       |
| IMAP port      | `993` (implicit TLS)         | `10143` (**plaintext**, dev only) |
| SMTP host      | `nuvrail.example.com`        | `localhost`                       |
| SMTP port      | `465` (implicit TLS)         | `10587` (**plaintext**, dev only) |
| Username       | your Nuvrail `agent_username` | your Nuvrail `agent_username`     |
| Password       | your Nuvrail `agent_token`    | your Nuvrail `agent_token`        |

> **TLS caveat for the local trial.** The `docker compose` trial exposes the
> proxy on `10143`/`10587` **without TLS** for evaluation on `localhost` only,
> so the code below picks `IMAP4`/`SMTP` (plaintext) vs `IMAP4_SSL`/`SMTP_SSL`
> (TLS) off a single `NUVRAIL_TLS` flag. For any real deployment use the
> production ports (`993`/`465`) with TLS on — see
> [Deploying to production](../../README.md#deploying-to-production).

---

## Step 1 — Point connection settings at Nuvrail

Read the Nuvrail proxy host/port/creds from the environment so the same code
runs against the local trial and production without edits:

```bash
# Local trial (docker compose up) — plaintext dev ports on localhost
export NUVRAIL_HOST=localhost
export NUVRAIL_IMAP_PORT=10143
export NUVRAIL_SMTP_PORT=10587
export NUVRAIL_TLS=false

# Production Nuvrail (TLS on) — comment the block above and use these instead
# export NUVRAIL_HOST=nuvrail.example.com
# export NUVRAIL_IMAP_PORT=993
# export NUVRAIL_SMTP_PORT=465
# export NUVRAIL_TLS=true

export NUVRAIL_USER=nuvrail_abc123          # your Nuvrail agent username
export NUVRAIL_TOKEN=your-nuvrail-agent-token
export OPENAI_API_KEY=sk-...                # for the example model
```

---

## Step 2 — Write the email tools (pointed at Nuvrail)

Three `@tool` functions cover the common agent-email loop: **search + read** (pass
through), and **send** + **move** (staged). They are ordinary `imaplib` /
`smtplib` — the only Nuvrail-specific thing is that host/port/creds resolve to the
proxy.

```python
# nuvrail_email_tools.py
import email
import imaplib
import os
import smtplib
from email.message import EmailMessage

from langchain_core.tools import tool

HOST = os.environ["NUVRAIL_HOST"]
IMAP_PORT = int(os.environ["NUVRAIL_IMAP_PORT"])
SMTP_PORT = int(os.environ["NUVRAIL_SMTP_PORT"])
USER = os.environ["NUVRAIL_USER"]
TOKEN = os.environ["NUVRAIL_TOKEN"]
TLS = os.environ.get("NUVRAIL_TLS", "true").lower() == "true"


def _imap() -> imaplib.IMAP4:
    # TLS -> IMAP4_SSL (:993); local trial -> plaintext IMAP4 (:10143).
    conn = imaplib.IMAP4_SSL(HOST, IMAP_PORT) if TLS else imaplib.IMAP4(HOST, IMAP_PORT)
    conn.login(USER, TOKEN)  # the Nuvrail agent token, NOT the real mailbox password
    return conn


@tool
def search_inbox(query: str = "ALL", limit: int = 5) -> str:
    """Search the inbox and return subjects + senders of matching messages.
    `query` is an IMAP search key (e.g. 'UNSEEN', 'FROM news@example.com').
    This is a READ — it passes through Nuvrail instantly, no approval needed."""
    conn = _imap()
    try:
        conn.select("INBOX")
        _, data = conn.uid("SEARCH", None, query)
        uids = data[0].split()[-limit:]
        out = []
        for uid in reversed(uids):
            _, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            hdr = email.message_from_bytes(msg_data[0][1])
            out.append(f"uid={uid.decode()} | from={hdr.get('From')} | subject={hdr.get('Subject')}")
        return "\n".join(out) or "No matching messages."
    finally:
        conn.logout()


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email. Through Nuvrail this STAGES the send for human approval —
    it is not delivered until a human approves it in the Nuvrail app."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = USER, to, subject
    msg.set_content(body)
    smtp = smtplib.SMTP_SSL(HOST, SMTP_PORT) if TLS else smtplib.SMTP(HOST, SMTP_PORT)
    try:
        smtp.login(USER, TOKEN)
        smtp.send_message(msg)
        # Nuvrail returned OK [STAGED]; the agent sees success and keeps working.
        return "Send staged in Nuvrail — awaiting your approval before it leaves the mailbox."
    finally:
        smtp.quit()


@tool
def archive_message(uid: str) -> str:
    """Move a message (by UID) from the inbox to the Archive folder. Through
    Nuvrail this STAGES the move for human approval before it takes effect."""
    conn = _imap()
    try:
        conn.select("INBOX")
        conn.uid("MOVE", uid, "Archive")  # staged at the proxy
        return f"Move of uid={uid} to Archive staged in Nuvrail — awaiting your approval."
    finally:
        conn.logout()


TOOLS = [search_inbox, send_email, archive_message]
```

> **Why the send/move tools report "staged," not "sent."** Nuvrail answers a
> write with `OK [STAGED]` immediately so the agent isn't blocked — the operation
> is real but parked, waiting for your tap. Wording the tool's return string as
> *"staged, awaiting approval"* keeps the agent's mental model honest instead of
> letting it tell the user "done" when nothing has landed yet.

---

## Step 3 — Wire the tools into an agent

Any LangChain agent constructor works; this uses a plain tool-calling agent:

```python
# agent.py
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from nuvrail_email_tools import TOOLS

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are an inbox assistant. Use the email tools to read and act "
               "on mail. Writes are staged for the user's approval — tell the "
               "user when something is staged rather than claiming it is done."),
    ("human", "{input}"),
    ("placeholder", "{agent_scratchpad}"),
])
agent = create_tool_calling_agent(llm, TOOLS, prompt)
executor = AgentExecutor(agent=agent, tools=TOOLS, verbose=True)

if __name__ == "__main__":
    print(executor.invoke({"input": "What are my 5 most recent emails?"}))
```

Run it:

```bash
python agent.py
```

---

## Step 4 — End-to-end verification

Run these two checks in order. Together they prove reads pass through and writes
are staged — the whole reason Nuvrail is in the path.

### 4a — A read (should just work, no approval)

Prompt the agent:

> "List the 5 most recent emails in my inbox — just subjects and senders."

The agent calls `search_inbox`, which runs `SELECT INBOX` + `UID SEARCH` +
`FETCH` through Nuvrail. Reads pass through instantly, so you get the list back
with **no approval prompt**. If this works, the wire is good end to end.

### 4b — A write (should stage and wait for you)

Prompt the agent to change the mailbox — e.g.:

> "Send a one-line test email to me@example.com saying 'Nuvrail test'."

or

> "Archive the newsletter from news@example.com."

What should happen:

1. The tool returns its *"staged — awaiting approval"* string and the agent
   reports the action was staged (the SMTP/IMAP call received `OK [STAGED]` from
   Nuvrail — this is by design; the agent keeps working).
2. **You get a Nuvrail approval notification** (phone/browser PWA) showing the
   exact operation — the recipient, message, or destination folder.
3. Open the Nuvrail approval UI, review, and **Approve** — only then does it
   execute against the real mail server. **Reject** reverts and the agent is told
   on its next command.

If the write shows up as a pending operation in Nuvrail waiting for your tap, the
integration is working correctly: the agent can act, but nothing lands on your
real mailbox without you.

> You can also approve/reject from the API instead of the UI — see
> [Approving operations](../../README.md#approving-operations).

---

## What a LangChain agent can and can't do through Nuvrail

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

This mapping is enforced by Nuvrail regardless of what your tools or the agent
try — the approval boundary is in the proxy, not in your code. Writing a
`delete_forever` tool wouldn't change it: `EXPUNGE` is refused at the gateway.

---

## Troubleshooting

| Symptom                                                        | Likely cause / fix                                                                                                                                                        |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `KeyError: 'NUVRAIL_HOST'` (or similar) at import              | The env vars from Step 1 aren't exported in the shell running the agent. Re-export them (or load a `.env`) before `python agent.py`.                                     |
| `ssl.SSLError` / `WRONG_VERSION_NUMBER` on the local trial     | You used `IMAP4_SSL`/`SMTP_SSL` against the plaintext dev ports. Set `NUVRAIL_TLS=false` for the local trial (`10143`/`10587`); use TLS only against `993`/`465`.        |
| `ConnectionRefusedError` / timeout                             | Wrong host/port, or Nuvrail isn't running. Confirm Nuvrail is up (`curl http://localhost:8080/health` for the local trial) and the port matches the table in this guide. |
| `imaplib.IMAP4.error: LOGIN failed` / SMTP auth error          | You're using the real mailbox password or a stale token. Use the **Nuvrail agent username + agent token**; reconnect the mailbox in Nuvrail to mint a fresh token.       |
| Reads work but writes never prompt you                         | Notifications not enabled on the Nuvrail PWA. Open the approval UI directly (`https://nuvrail.example.com`) — the pending op will be there; enable PWA notifications.     |
| Agent says "sent" but nothing was delivered                    | That's Nuvrail working — the send is *staged*, not delivered, until you approve it. Approve it in the app. (Tighten the tool's return string / system prompt so the agent says "staged.") |
| A staged op vanished / the agent got a rejection it didn't expect | Pending operations **expire after 48h** if not acted on — the proxy reverts and reports a rejection to the agent. Approve sooner, or re-run the agent.                |

---

## Security notes

- **The agent only ever holds the Nuvrail agent token**, which is scoped to one
  mailbox and revocable in Nuvrail without touching your real credentials. That
  is the point of the proxy — the blast radius of a leaked token is "someone can
  *stage* operations you still have to approve," not "someone owns your inbox."
- **Nothing is deleted, ever.** `EXPUNGE` is blocked at the gateway; the worst a
  confused or prompt-injected agent can do is *stage* a move-to-Trash that you
  then reject.
- **The approval boundary does not depend on the agent's cooperation.** LangChain
  has no built-in human-in-the-loop before a tool runs, and a prompt-injected
  agent will happily call `send_email`. Nuvrail stages that write anyway — the
  guarantee lives in the proxy, not in the prompt or the model.
- **Keep the token out of version control.** It lives in your environment
  (`NUVRAIL_TOKEN`), not in the source. Don't hard-code it into
  `nuvrail_email_tools.py` or commit a `.env` that contains it. If it leaks,
  revoke the agent token in Nuvrail and mint a new one.

---

_See also: [Claude Desktop integration recipe](claude-desktop.md) ·
[Cursor integration recipe](cursor.md) ·
[Configuring your AI agent](../../README.md#configuring-your-ai-agent) ·
[Approving operations](../../README.md#approving-operations) ·
provider guides — [Gmail](../providers/gmail.md) · [iCloud](../providers/icloud.md) · [Outlook](../providers/outlook.md) · [generic IMAP/SMTP](../provider-imap-guide.md)._
