"""
Shared fixtures for Nuvrail E2E tests.

Stands up all three in-process components simultaneously:

  ┌─────────────────────────────────────────────────────────┐
  │  test                                                   │
  │   ├─► IMAP proxy  (asyncio server, ephemeral port)      │
  │   ├─► SMTP proxy  (asyncio server, ephemeral port)      │
  │   ├─► FastAPI app (httpx.AsyncClient, in-process)       │
  │   │    └── all share the same tmp_path SQLite DB        │
  │   ├─► direct IMAP (blizzard.mxrouting.net:993)          │
  │   └─► direct SMTP (blizzard.mxrouting.net:587)          │
  └─────────────────────────────────────────────────────────┘

All three components (IMAP proxy, SMTP proxy, FastAPI) write to the same
tmp_path-isolated DB so that operations staged by the proxies are visible
to the API and vice versa.

DB injection strategy:
  - FastAPI: app.dependency_overrides[get_db_path] = lambda: db_path
  - Proxies: gateway.staging functions use keyword-only default db_path.
    We patch __kwdefaults__['db_path'] on each staging function so proxy
    calls (which omit db_path) write to the tmp DB.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Load .env so upstream credentials are available
_REPO_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_REPO_ROOT / ".env")


def _patch_staging_db(db_path: Path) -> dict:
    """Patch the db_path keyword default on all staging functions.

    staging.create_operation and related functions have:
        async def create_operation(..., db_path: Path = DB_PATH)
    where DB_PATH is captured at import time as a default argument value
    stored in __kwdefaults__. We must patch those dicts directly.

    Returns a dict of originals so the caller can restore them at teardown.
    """
    import gateway.staging as _staging

    fns = [
        _staging.create_operation,
        _staging.get_operation,
        _staging.list_operations,
        _staging.update_operation_status,
    ]
    originals = {}
    for fn in fns:
        if fn.__kwdefaults__ is None:
            fn.__kwdefaults__ = {}
        originals[fn] = fn.__kwdefaults__.get("db_path")
        fn.__kwdefaults__["db_path"] = db_path
    return originals


def _restore_staging_db(originals: dict) -> None:
    """Restore the original db_path defaults after test teardown."""
    for fn, orig in originals.items():
        if orig is None:
            fn.__kwdefaults__.pop("db_path", None)
        else:
            fn.__kwdefaults__["db_path"] = orig


# ---------------------------------------------------------------------------
# E2E setup fixture: starts IMAP proxy, SMTP proxy, and FastAPI in-process
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def e2e_setup(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Start IMAP proxy, SMTP proxy, and FastAPI app sharing a single tmp DB.

    Yields a dict:
      - imap_host, imap_port  — address of the in-process IMAP proxy
      - smtp_host, smtp_port  — address of the in-process SMTP proxy
      - api_client            — httpx.AsyncClient wired to the FastAPI app
      - db_path               — Path to the shared SQLite DB
    """
    # 1. Create an isolated DB for this test module.
    tmp_dir = tmp_path_factory.mktemp("e2e")
    db_path = tmp_dir / "nuvrail.db"

    # 2. Initialise DB tables.
    from gateway.state_db import init_db

    await init_db(db_path)

    # 3. Patch staging function defaults so proxy calls write to the tmp DB.
    staging_originals = _patch_staging_db(db_path)

    # 4. Import proxy handler functions (after patching defaults).
    from gateway.proxy import handle_client
    from gateway.smtp_proxy import handle_smtp_client

    # 5. Start IMAP proxy on an ephemeral port.
    imap_server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    imap_host, imap_port = imap_server.sockets[0].getsockname()
    imap_task = asyncio.create_task(imap_server.serve_forever())

    # 6. Start SMTP proxy on an ephemeral port.
    smtp_server = await asyncio.start_server(handle_smtp_client, "127.0.0.1", 0)
    smtp_host, smtp_port = smtp_server.sockets[0].getsockname()
    smtp_task = asyncio.create_task(smtp_server.serve_forever())

    # 7. Set up FastAPI with dependency override for get_db_path.
    from api.main import app
    from api.routes.operations import get_db_path

    app.dependency_overrides[get_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    api_client = httpx.AsyncClient(transport=transport, base_url="http://test")

    yield {
        "imap_host": imap_host,
        "imap_port": imap_port,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "api_client": api_client,
        "db_path": db_path,
    }

    # 8. Teardown — close everything and restore patches.
    await api_client.aclose()
    app.dependency_overrides.clear()

    imap_server.close()
    await imap_server.wait_closed()
    imap_task.cancel()
    try:
        await imap_task
    except (asyncio.CancelledError, Exception):
        pass

    smtp_server.close()
    await smtp_server.wait_closed()
    smtp_task.cancel()
    try:
        await smtp_task
    except (asyncio.CancelledError, Exception):
        pass

    _restore_staging_db(staging_originals)


# ---------------------------------------------------------------------------
# Convenience fixtures that extract upstream config from environment
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def upstream_imap_cfg() -> dict:
    return {
        "host": os.environ["NUVRAIL_TEST_IMAP_HOST"],
        "port": int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993")),
        "user": os.environ["NUVRAIL_TEST_IMAP_USER"],
        "password": os.environ["NUVRAIL_TEST_IMAP_PASS"],
    }


@pytest.fixture(scope="function")
def upstream_smtp_cfg() -> dict:
    return {
        "host": os.environ["NUVRAIL_TEST_SMTP_HOST"],
        "port": int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587")),
        "user": os.environ["NUVRAIL_TEST_SMTP_USER"],
        "password": os.environ["NUVRAIL_TEST_SMTP_PASS"],
    }
