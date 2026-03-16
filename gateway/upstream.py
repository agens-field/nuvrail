"""
Upstream IMAP connection manager.

Maintains the persistent authenticated connection to Google IMAP.
Handles XOAUTH2 authentication, token refresh, and command execution.

Phase 0: single user, single connection.

Sub-milestone: 0.3
"""


class UpstreamManager:
    """
    Singleton upstream connection.

    ┌─────────────────────────────────────────────┐
    │ UpstreamManager                             │
    │                                             │
    │  token_store ──► get_access_token()         │
    │       │                                     │
    │       └──► aioimaplib connection            │
    │               ├── passthrough reads         │
    │               └── execute staged writes     │
    └─────────────────────────────────────────────┘
    """

    def __init__(self) -> None:
        self._connection = None

    async def execute_imap_command(self, imap_command: str) -> tuple[bool, str]:
        """Issue a raw IMAP command against the upstream server."""
        # TODO: implement in sub-milestone 1.3
        raise NotImplementedError
