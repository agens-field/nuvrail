"""
Shared fixtures for the api test suite.

Resets the slowapi rate limiter before every test so rate-limit counts from
one test don't bleed into the next.  This is safe because all API tests run
against an in-memory limiter with no external state.
"""
import pytest
from api.limiter import limiter


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Clear the slowapi in-memory rate-limit storage before each test."""
    limiter.reset()
