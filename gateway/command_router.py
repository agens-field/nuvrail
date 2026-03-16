"""
IMAP command classifier.

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
    """Returns 'read', 'write', or 'blocked'."""
    # TODO: implement in sub-milestone 0.2
    raise NotImplementedError
