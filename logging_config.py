"""
Logging configuration for Nuvrail.

Every long-running process (IMAP proxy, SMTP proxy, API) calls
``setup_logging()`` once at startup. It installs one stream handler on the root
logger and attaches ``_RedactingFilter`` to that *handler*:

    getLogger("gateway.smtp_proxy").warning(...)      (any named logger)
              │  propagates up
              ▼
    root logger ──► StreamHandler ──[_RedactingFilter]──► stderr

The filter sits on the handler, not the logger, on purpose. A logger-level
filter only runs for records created on that exact logger. Records from child
loggers propagate to the root's handlers but skip the root's filters, so a
filter on the root logger would never see them.

Redaction rules:
  - SMTP ``AUTH <mech> ...`` / IMAP ``AUTHENTICATE <mech> ...``: everything
    after the mechanism, to end of line, becomes [REDACTED].
  - IMAP ``LOGIN <user> ...``: everything after the user argument (quoted or
    bare), to end of line, becomes [REDACTED].
  - Raw protocol lines should still never be logged. Call sites log the verb
    only, or pass the line through redact_protocol_line() first. The filter is
    the safety net behind that rule, not a replacement for it.

What the filter redacts: the string arguments of a record, and the message
itself when the record has no arguments (a pre-formatted message). A format
string with arguments is developer-written text such as "auth failed for
user=%s", so it is left alone; the data it formats is what gets redacted.

Level: INFO by default; set LOG_LEVEL (the same variable the container passes
to uvicorn's --log-level). The filter applies at every level, DEBUG included.
"""

import logging
import os
import re

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

# SMTP "AUTH <mech> <payload...>" and IMAP "<tag> AUTHENTICATE <mech> <payload...>".
# The payload may be several tokens, so redact to end of line. A bare
# "AUTH PLAIN" (challenge flow, no inline payload) is left as-is.
_AUTH_PAYLOAD_RE = re.compile(
    r"(\bAUTH(?:ENTICATE)?\s+\S+)\s+\S[^\r\n]*",
    re.IGNORECASE,
)

# IMAP "<tag> LOGIN <user> <password>". The user may be a quoted string; the
# password may be quoted and contain spaces, so redact everything after the
# user argument to end of line.
_IMAP_LOGIN_RE = re.compile(
    r'(\bLOGIN\s+(?:"(?:[^"\\]|\\.)*"|\S+))\s+\S[^\r\n]*',
    re.IGNORECASE,
)


def redact_protocol_line(line: str) -> str:
    """Return a copy of *line* with credential payloads replaced by [REDACTED].

    Safe to call on any SMTP or IMAP protocol line. Non-auth lines are returned
    unchanged.
    """
    line = _AUTH_PAYLOAD_RE.sub(r"\1 [REDACTED]", line)
    line = _IMAP_LOGIN_RE.sub(r"\1 [REDACTED]", line)
    return line


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class _RedactingFilter(logging.Filter):
    """Strip credential payloads from log records before a handler writes them.

    Attach to handlers (see setup_logging), not loggers, so records from every
    named logger are covered.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (redact_protocol_line(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    redact_protocol_line(a) if isinstance(a, str) else a
                    for a in record.args
                )
        elif isinstance(record.msg, str):
            # Pre-formatted message (f-string or concatenation): the data is
            # already baked into msg, so redact msg itself.
            record.msg = redact_protocol_line(record.msg)

        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """Configure logging for a Nuvrail process. Idempotent.

    - Level: LOG_LEVEL env var (default INFO).
    - Format: timestamp + level + logger name + message.
    - _RedactingFilter attached to every root handler, so every named logger
      in the process is covered.

    Call once at process startup, before any other logging calls.
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(level)

    for handler in root.handlers:
        if not any(isinstance(f, _RedactingFilter) for f in handler.filters):
            handler.addFilter(_RedactingFilter())


def set_default_log_level() -> None:
    """Set gateway loggers to INFO (idempotent, safe to call after setup_logging)."""
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("gateway").setLevel(logging.INFO)
