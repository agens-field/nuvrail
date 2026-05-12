"""
Shared pytest fixtures for Nuvrail integration and unit tests.

Loads .env at import time so all tests can access real credentials without
explicitly calling load_dotenv() themselves.
"""

import os

import pytest
from dotenv import load_dotenv

# Load .env relative to the repo root (one directory up from tests/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))


@pytest.fixture(scope="session")
def upstream_imap_config() -> dict:
    """Return upstream IMAP server config from environment.

    Skips (not errors) when credentials are absent so that `pytest` run
    without credentials shows clean skips rather than 24 confusing errors.
    Set NUVRAIL_TEST_IMAP_HOST (and _USER, _PASS) to run integration/e2e tests.
    """
    host = os.environ.get("NUVRAIL_TEST_IMAP_HOST")
    if not host:
        pytest.skip(
            "NUVRAIL_TEST_IMAP_HOST not set — skipping integration/e2e tests. "
            "See .env.example for required variables."
        )
    return {
        "host": host,
        "port": int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993")),
        "user": os.environ["NUVRAIL_TEST_IMAP_USER"],
        "password": os.environ["NUVRAIL_TEST_IMAP_PASS"],
    }


@pytest.fixture(scope="session")
def proxy_config() -> dict:
    """Return Nuvrail proxy config from environment."""
    return {
        "host": os.environ.get("NUVRAIL_PROXY_HOST", "127.0.0.1"),
        "imap_port": int(os.environ.get("NUVRAIL_PROXY_IMAP_PORT", "10143")),
        "smtp_port": int(os.environ.get("NUVRAIL_PROXY_SMTP_PORT", "10587")),
        "api_url": os.environ.get("NUVRAIL_PROXY_API_URL", "http://127.0.0.1:8000"),
        "api_key": os.environ.get("NUVRAIL_PROXY_API_KEY", ""),
    }


@pytest.fixture(scope="session")
def upstream_smtp_config() -> dict:
    """Return upstream SMTP server config from environment.

    Skips (not errors) when credentials are absent so that `pytest` run
    without credentials shows clean skips rather than confusing errors.
    Set NUVRAIL_TEST_SMTP_HOST (and _USER, _PASS) to run integration/e2e tests.
    """
    host = os.environ.get("NUVRAIL_TEST_SMTP_HOST")
    if not host:
        pytest.skip(
            "NUVRAIL_TEST_SMTP_HOST not set — skipping SMTP integration/e2e tests. "
            "See .env.example for required variables."
        )
    return {
        "host": host,
        "port": int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587")),
        "user": os.environ["NUVRAIL_TEST_SMTP_USER"],
        "password": os.environ["NUVRAIL_TEST_SMTP_PASS"],
    }
