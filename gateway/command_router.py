"""
IMAP command classifier (Milestone 0.2).

Determines whether a parsed command is:
  - "read"    → pass through to upstream
  - "write"   → intercept and stage
  - "blocked" → return OK but never forward (EXPUNGE, DELETE folder)

Sub-milestone: 0.2
"""

from gateway.imap_parser import ParsedCommand

READ_COMMANDS = {
    "SELECT", "EXAMINE", "FETCH", "SEARCH", "LIST", "LSUB",
    "STATUS", "NOOP", "CAPABILITY", "ID", "LOGOUT", "CHECK",
    "SUBSCRIBE", "UNSUBSCRIBE", "NAMESPACE", "IDLE",
}

WRITE_COMMANDS = {
    "STORE", "COPY", "MOVE", "APPEND", "CREATE", "RENAME",
}

BLOCKED_COMMANDS = {
    "EXPUNGE",  # Never forwarded; \Deleted → staged trash move
    "DELETE",   # Folder deletion blocked
}


def classify(cmd: ParsedCommand) -> str:
    """Return 'read', 'write', or 'blocked' for the given parsed command.

    Unknown commands default to 'read' so the proxy never crashes on extension
    commands or future RFC additions — better to pass through than to break.
    """
    if cmd.command in BLOCKED_COMMANDS:
        return "blocked"
    if cmd.command in WRITE_COMMANDS:
        return "write"
    # READ_COMMANDS and all unknowns (AUTHENTICATE, LOGIN, custom extensions)
    return "read"
