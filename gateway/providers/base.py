"""
Abstract provider interface.

Implemented by GmailProvider (Phase 0) and OutlookProvider (Phase 2).
Isolates all provider-specific OAuth2 and IMAP auth logic.

Phase 2, sub-milestone 2.0
"""
from abc import ABC, abstractmethod


class ProviderConnection(ABC):

    @abstractmethod
    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        ...

    @abstractmethod
    async def connect_imap(self):
        """Return an authenticated aioimaplib connection."""
        ...
