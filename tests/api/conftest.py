"""
Shared fixtures for the api test suite.

Resets both the slowapi rate limiter and the AuthAbuseProtector before every
test so rate-limit counts from one test don't bleed into the next.  This is
safe because all API tests run against in-memory limiters with no external
state.
"""
import pytest
from api.limiter import limiter
from api.routes.auth import LOGIN_ABUSE_PROTECTOR


@pytest.fixture(autouse=True)
def reset_rate_limiters() -> None:
    """Clear all in-memory rate-limit storage before each test."""
    limiter.reset()
    LOGIN_ABUSE_PROTECTOR.reset()
