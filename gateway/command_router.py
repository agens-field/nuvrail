"""
IMAP command classifier (Milestone 0.2).

Determines whether a parsed command is:
  - "read"    → pass through to upstream
  - "write"   → intercept and stage
  - "blocked" → return OK but never forward (EXPUNGE, DELETE folder, CLOSE)

Sub-milestone: 0.2

Core invariant
--------------
The proxy's headline promise is: **an agent can never cause an EXPUNGE to
reach the real mailbox.** Deletion is always staged for human approval; it is
never applied upstream by an agent command. Every command that can *expunge*
mail as a side effect must therefore land in ``BLOCKED_COMMANDS``, not "read".

RFC commands that expunge \\Deleted messages upstream:

    ┌───────────────┬──────────────────────────────────────────────┬──────────┐
    │ Command       │ Expunge behaviour                            │ Class    │
    ├───────────────┼──────────────────────────────────────────────┼──────────┤
    │ EXPUNGE       │ Expunges all \\Deleted in selected mailbox    │ blocked  │
    │ UID EXPUNGE   │ Expunges the given \\Deleted UIDs (RFC 4315)  │ blocked* │
    │ CLOSE         │ Implicitly expunges, THEN unselects (RFC3501)│ blocked  │
    │ UNSELECT      │ Unselects WITHOUT expunging (RFC 3691)        │ read     │
    └───────────────┴──────────────────────────────────────────────┴──────────┘

    * ``UID EXPUNGE`` is parsed with ``command == "EXPUNGE"`` (the parser strips
      the UID prefix), so it is already covered by the EXPUNGE entry.

``CLOSE`` is the subtle one: clients issue it routinely to leave a mailbox, but
per RFC 3501 §6.4.2 it *first* permanently expunges every \\Deleted message,
then returns to the authenticated state. Forwarding it upstream is a covert
EXPUNGE path — it breaks the core invariant whenever the real mailbox already
has \\Deleted messages set by the human's other clients. We block it: the
client receives ``OK`` (its local view is "mailbox closed") and nothing reaches
upstream, so no mail is expunged. ``UNSELECT`` is the safe way to leave a
mailbox (no expunge), so it passes through as a read.
"""

from gateway.imap_parser import ParsedCommand

READ_COMMANDS = {
    "SELECT", "EXAMINE", "FETCH", "SEARCH", "LIST", "LSUB",
    "STATUS", "NOOP", "CAPABILITY", "ID", "LOGOUT", "CHECK",
    "SUBSCRIBE", "UNSUBSCRIBE", "NAMESPACE", "IDLE",
    "UNSELECT",  # RFC 3691: close mailbox WITHOUT expunging — safe to forward.
}

WRITE_COMMANDS = {
    "STORE", "COPY", "MOVE", "APPEND", "CREATE", "RENAME",
}

BLOCKED_COMMANDS = {
    "EXPUNGE",  # Never forwarded; \Deleted → staged trash move. Covers UID EXPUNGE.
    "DELETE",   # Folder deletion blocked
    "CLOSE",    # RFC 3501: side-effect-expunges \Deleted, THEN unselects → covert EXPUNGE.
}


def classify(cmd: ParsedCommand) -> str:
    """Return 'read', 'write', or 'blocked' for the given parsed command.

    Unknown commands default to 'read' so the proxy never crashes on extension
    commands or future RFC additions — better to pass through than to break.

    Safety note: the fail-open default is only safe because every command known
    to expunge mail is explicitly enumerated in ``BLOCKED_COMMANDS`` above. If a
    future RFC adds another expunge-capable verb, it must be added there too —
    do not rely on the default to catch it.
    """
    if cmd.command in BLOCKED_COMMANDS:
        return "blocked"
    if cmd.command in WRITE_COMMANDS:
        return "write"
    # READ_COMMANDS and all unknowns (AUTHENTICATE, LOGIN, custom extensions)
    return "read"
