"""
Per-session unsolicited IMAP response queue.

When an operation is rejected, the AI agent's local view is stale.
This module queues unsolicited FETCH responses that the proxy injects
before the tagged response to the AI's next command.

IMAP rule: unsolicited (untagged) responses can be sent at any time
between command completions. Safe injection points: before the
tagged OK/NO/BAD that closes a command.

Sub-milestone: 1.3
"""


class PendingNotifications:
    """Per-session queue of unsolicited IMAP responses to inject."""

    def __init__(self) -> None:
        self._queues: dict[str, list[str]] = {}

    def queue_revert(self, operation_id: str, session_id: str) -> None:
        """Queue unsolicited FETCH lines to correct AI state after rejection."""
        # TODO: implement in sub-milestone 1.3
        raise NotImplementedError

    def flush_pending(self, session_id: str) -> list[str]:
        """Return and clear all queued unsolicited responses for a session."""
        return self._queues.pop(session_id, [])
