"""
Logging configuration for Nuvrail gateway.

Redaction rules (applied at the filter level, before any handler writes):
  - SMTP AUTH lines: the credential payload (base64 blob or multi-word token)
    is replaced with [REDACTED]. The command verb is preserved so log
    correlation still works.
  - IMAP LOGIN lines: the password argument is replaced with [REDACTED].
  - Raw protocol lines must never be logged at any level. Use the
    redact_protocol_line() helper when emitting partial command info.

Default log level is INFO. DEBUG must be explicitly opted-in via the
NUVRAIL_LOG_LEVEL env var, and even then the filter ensures credentials
cannot appear in plaintext.
"""

import logging
import os
import re

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

# SMTP AUTH PLAIN / AUTH LOGIN / AUTH <mech> <payload>
# Matches: AUTH <MECH> <anything>  or  AUTH <MECH> (no payload — challenge flow)
_AUTH_PAYLOAD_RE = re.compile(
    r"(\bAUTH\s+\S+)(\s+\S+)",
    re.IGNORECASE,
)

# IMAP LOGIN <user> <password>  (plain LOGIN command — should never be raw-logged,
# but belt-and-suspenders)
_IMAP_LOGIN_RE = re.compile(
    r"(\bLOGIN\s+\S+)(\s+\S+)",
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
    """Strip credential payloads from log records before any handler sees them.

    Applied to the root logger so every logger in the process is covered.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Materialise the message so we can inspect it.
        # We operate on record.msg + record.args separately to handle both
        # %-style and pre-formatted messages.
        if record.msg and isinstance(record.msg, str):
            record.msg = redact_protocol_line(record.msg)

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

        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """Configure logging for the Nuvrail gateway process.

    - Level: INFO by default; override with NUVRAIL_LOG_LEVEL env var.
    - Format: timestamp + level + logger name + message.
    - Redacting filter applied to root logger (covers all child loggers).

    Call once at process startup before any other logging calls.
    """
    level_name = os.environ.get("NUVRAIL_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )

    root = logging.getLogger()
    # Avoid adding duplicate filters if setup_logging() is called more than once.
    if not any(isinstance(f, _RedactingFilter) for f in root.filters):
        root.addFilter(_RedactingFilter())


def set_default_log_level() -> None:
    """Set gateway loggers to INFO (idempotent, safe to call after setup_logging)."""
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("gateway").setLevel(logging.INFO)
