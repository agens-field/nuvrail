"""
Shared fixtures for Nuvrail E2E tests.

Stands up all three in-process components simultaneously:

  ┌─────────────────────────────────────────────────────────┐
  │  test                                                   │
  │   ├─► IMAP proxy  (asyncio server, ephemeral port)      │
  │   ├─► SMTP proxy  (asyncio server, ephemeral port)      │
  │   ├─► FastAPI app (httpx.AsyncClient, in-process)       │
  │   │    └── all share the same tmp_path SQLite DB        │
  │   ├─► direct IMAP (mail.example.com:993)          │
  │   └─► direct SMTP (mail.example.com:587)          │
  └─────────────────────────────────────────────────────────┘

All three components (IMAP proxy, SMTP proxy, FastAPI) write to the same
tmp_path-isolated DB so that operations staged by the proxies are visible
to the API and vice versa.

DB injection strategy:
  - FastAPI: app.dependency_overrides[get_db_path] = lambda: db_path
             app.dependency_overrides[get_auth_db_path] = lambda: db_path
             Both overrides are required: get_db_path covers operations/audit
             routes; get_auth_db_path covers the get_current_user dependency
             in api/auth.py which runs on every authenticated endpoint.
  - Proxies: gateway.staging functions use keyword-only default db_path.
    We patch __kwdefaults__['db_path'] on each staging function so proxy
    calls (which omit db_path) write to the tmp DB.

Auth in e2e tests:
  The e2e_setup fixture registers a test user, logs in, and includes the
  bearer token in the returned dict under key "auth_headers". Tests pass
  this to every api_client call that hits an authenticated endpoint.
  This exercises the full auth stack (register → login → bearer token →
  get_current_user validation) rather than bypassing it.
"""
from __future__ import annotations

import asyncio
import contextlib
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
    """Patch db_path defaults on staging and state_db functions, and the module-level DB_PATH.

    Functions use 'db_path: Path = DB_PATH' as a keyword-only default.
    DB_PATH is captured at import time, so we must also patch the module-level
    attribute so that proxy code calling state_db functions with DB_PATH (the
    constant, not the function default) also writes to the test DB.

    Returns a dict of originals for restoration at teardown.
    """
    import gateway.proxy as _proxy
    import gateway.staging as _staging
    import gateway.state_db as _state_db

    originals: dict = {}

    # Patch staging functions
    for fn in [
        _staging.create_operation,
        _staging.get_operation,
        _staging.list_operations,
        _staging.update_operation_status,
    ]:
        if fn.__kwdefaults__ is None:
            fn.__kwdefaults__ = {}
        originals[fn] = fn.__kwdefaults__.get("db_path")
        fn.__kwdefaults__["db_path"] = db_path

    # Patch state_db functions used by proxy for revert injection and sync
    for fn in [
        _state_db.get_pending_reverts,
        _state_db.mark_reverts_delivered,
        _state_db.get_message,
        _state_db.get_or_create_folder,
        _state_db.update_folder_stats,
        _state_db.upsert_folders_from_list,
        _state_db.upsert_message,
        _state_db.snapshot_messages,
        _state_db.apply_optimistic_flag_update,
        _state_db.restore_from_snapshot,
        _state_db.insert_pending_reverts,
        _state_db.remove_messages_from_folder,
        _state_db.get_pending_move_uids_for_folder,
    ]:
        if fn.__kwdefaults__ is None:
            fn.__kwdefaults__ = {}
        originals[fn] = fn.__kwdefaults__.get("db_path")
        fn.__kwdefaults__["db_path"] = db_path

    # Patch the module-level DB_PATH constant so proxy's handle_client and
    # start_proxy (which pass DB_PATH directly, not via __kwdefaults__) also
    # use the test DB.
    originals["_state_db_DB_PATH"] = _state_db.DB_PATH
    originals["_proxy_DB_PATH"] = _proxy.DB_PATH
    _state_db.DB_PATH = db_path
    _proxy.DB_PATH = db_path

    return originals


def _restore_staging_db(originals: dict) -> None:
    """Restore the original db_path defaults after test teardown."""
    import gateway.proxy as _proxy
    import gateway.state_db as _state_db

    # Restore module-level DB_PATH constants
    if "_state_db_DB_PATH" in originals:
        _state_db.DB_PATH = originals.pop("_state_db_DB_PATH")
    if "_proxy_DB_PATH" in originals:
        _proxy.DB_PATH = originals.pop("_proxy_DB_PATH")

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
    # Skip cleanly when upstream credentials are absent (no env vars set).
    if not os.environ.get("NUVRAIL_TEST_IMAP_HOST") or not os.environ.get("NUVRAIL_TEST_SMTP_HOST"):
        pytest.skip(
            "NUVRAIL_TEST_IMAP_HOST / NUVRAIL_TEST_SMTP_HOST not set — "
            "skipping e2e tests. See .env.example for required variables."
        )

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

    # 7. Set up FastAPI with dependency overrides for both DB-path dependencies.
    #    get_db_path  — used by operations, audit, and agents routes
    #    get_auth_db_path — used by get_current_user (api/auth.py) which runs
    #                       on every authenticated endpoint; must point to the
    #                       same test DB or bearer token lookups will fail.
    from api.auth import get_auth_db_path
    from api.main import app
    from api.routes.operations import get_db_path

    app.dependency_overrides[get_db_path] = lambda: db_path
    app.dependency_overrides[get_auth_db_path] = lambda: db_path
    transport = httpx.ASGITransport(app=app)
    api_client = httpx.AsyncClient(transport=transport, base_url="http://test")

    # 8. Register a test user and log in to obtain a bearer token.
    #    This exercises the full Lane 3 auth stack. The token is included in
    #    every e2e API call via auth_headers so all authenticated endpoints work.
    register_resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "e2e-test@example.com",
            "password": "e2e-test-password",
            "display_name": "E2E Test User",
        },
    )
    assert register_resp.status_code == 201, (
        f"e2e_setup: failed to register test user: {register_resp.status_code} {register_resp.text}"
    )

    login_resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "e2e-test@example.com", "password": "e2e-test-password"},
    )
    assert login_resp.status_code == 200, (
        f"e2e_setup: failed to log in test user: {login_resp.status_code} {login_resp.text}"
    )
    bearer_token = login_resp.json()["token"]
    auth_headers = {"Authorization": f"Bearer {bearer_token}"}

    # 9. Create per-protocol agent credentials for proxy authentication.
    #    Proxy LOGIN/AUTH expects agent_username + agent_token, not upstream creds.
    smtp_agent_resp = await api_client.post(
        "/api/v1/agents",
        headers=auth_headers,
        json={
            "label": "e2e-smtp-agent",
            "upstream_host": os.environ["NUVRAIL_TEST_SMTP_HOST"],
            "upstream_imap_port": int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993")),
            "upstream_smtp_port": int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587")),
            "upstream_user": os.environ["NUVRAIL_TEST_SMTP_USER"],
            "upstream_password": os.environ["NUVRAIL_TEST_SMTP_PASS"],
        },
    )
    assert smtp_agent_resp.status_code == 201, (
        "e2e_setup: failed to create SMTP agent credential: "
        f"{smtp_agent_resp.status_code} {smtp_agent_resp.text}"
    )

    imap_agent_resp = await api_client.post(
        "/api/v1/agents",
        headers=auth_headers,
        json={
            "label": "e2e-imap-agent",
            "upstream_host": os.environ["NUVRAIL_TEST_IMAP_HOST"],
            "upstream_imap_port": int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993")),
            "upstream_smtp_port": int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587")),
            "upstream_user": os.environ["NUVRAIL_TEST_IMAP_USER"],
            "upstream_password": os.environ["NUVRAIL_TEST_IMAP_PASS"],
        },
    )
    assert imap_agent_resp.status_code == 201, (
        "e2e_setup: failed to create IMAP agent credential: "
        f"{imap_agent_resp.status_code} {imap_agent_resp.text}"
    )
    proxy_agent_auth = {
        "smtp": {
            "username": smtp_agent_resp.json()["agent_username"],
            "token": smtp_agent_resp.json()["agent_token"],
        },
        "imap": {
            "username": imap_agent_resp.json()["agent_username"],
            "token": imap_agent_resp.json()["agent_token"],
        },
    }

    yield {
        "imap_host": imap_host,
        "imap_port": imap_port,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "api_client": api_client,
        "db_path": db_path,
        "auth_headers": auth_headers,
        "proxy_agent_auth": proxy_agent_auth,
    }

    # 10. Teardown — close everything and restore patches.
    await api_client.aclose()
    app.dependency_overrides.clear()

    imap_server.close()
    await imap_server.wait_closed()
    imap_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await imap_task

    smtp_server.close()
    await smtp_server.wait_closed()
    smtp_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await smtp_task

    _restore_staging_db(staging_originals)


# ---------------------------------------------------------------------------
# Convenience fixtures that extract upstream config from environment
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def upstream_imap_cfg() -> dict:
    host = os.environ.get("NUVRAIL_TEST_IMAP_HOST")
    if not host:
        pytest.skip("NUVRAIL_TEST_IMAP_HOST not set — skipping e2e tests.")
    return {
        "host": host,
        "port": int(os.environ.get("NUVRAIL_TEST_IMAP_PORT", "993")),
        "user": os.environ["NUVRAIL_TEST_IMAP_USER"],
        "password": os.environ["NUVRAIL_TEST_IMAP_PASS"],
    }


@pytest.fixture(scope="function")
def upstream_smtp_cfg() -> dict:
    host = os.environ.get("NUVRAIL_TEST_SMTP_HOST")
    if not host:
        pytest.skip("NUVRAIL_TEST_SMTP_HOST not set — skipping e2e tests.")
    return {
        "host": host,
        "port": int(os.environ.get("NUVRAIL_TEST_SMTP_PORT", "587")),
        "user": os.environ["NUVRAIL_TEST_SMTP_USER"],
        "password": os.environ["NUVRAIL_TEST_SMTP_PASS"],
    }
