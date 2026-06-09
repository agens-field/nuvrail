"""
E2E test: anti-spam outbound send rate limit (R13) against the live proxy.

This is the staging proof legal asked for in R13 — it exercises the real
chokepoint end to end:

  ┌──────────────────────────────────────────────────────────────┐
  │ NUVRAIL_SEND_MAX_PER_WINDOW = CAP (set low for the test)       │
  │                                                                │
  │  for i in 1..CAP:                                              │
  │     SMTP proxy send  ──► STAGED ──► API approve ──► EXECUTED   │  ← real relay
  │                                       (counts toward window)   │
  │                                                                │
  │  send #(CAP+1):                                                │
  │     SMTP proxy send  ──► STAGED                                │
  │     API approve      ──► 500, op status = 'failed'            │  ← refused
  │                          BEFORE any relay (fail-closed)        │
  │                                                                │
  │  assert audit_log has a 'send_rate_exceeded' row              │
  └──────────────────────────────────────────────────────────────┘

The limiter reads its config from the environment on every call
(``load_send_rate_config`` inside ``enforce_send_rate``), so setting the env
var via monkeypatch takes effect immediately — no app/proxy restart needed.

The CAP messages that DO relay are real mail; we track their UIDs and delete
them in teardown so we don't litter the upstream test mailbox. The refused
(CAP+1)th send never relays, so there's nothing to clean up for it.

Skips cleanly when upstream test credentials are absent (e2e_setup handles
the skip), matching the rest of the e2e suite.
"""
from __future__ import annotations

import time

import pytest

from tests.e2e.helpers import delete_message_by_uid, wait_for_message
from tests.e2e.test_send_and_verify import _smtp_send_via_proxy

# Keep the cap tiny so the test sends as little real mail as possible while
# still proving the boundary (relay up to CAP, refuse CAP+1).
_CAP = 2


@pytest.mark.asyncio(loop_scope="session")
async def test_send_rate_limit_refuses_over_cap(
    e2e_setup: dict,
    upstream_imap_cfg: dict,
    upstream_smtp_cfg: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving more than CAP sends in the window is refused + audited.

    1. Set NUVRAIL_SEND_MAX_PER_WINDOW=CAP (large window so nothing expires).
    2. Send + approve CAP messages → all execute and arrive (real relay).
    3. Send the (CAP+1)th → STAGED, but approve returns 500 and the op is
       marked 'failed' (refused before relay).
    4. Assert a 'send_rate_exceeded' audit row exists for the agent.
    5. Cleanup: delete the CAP messages that actually landed upstream.
    """
    monkeypatch.setenv("NUVRAIL_SEND_MAX_PER_WINDOW", str(_CAP))
    monkeypatch.setenv("NUVRAIL_SEND_WINDOW_SECONDS", "3600")

    api_client = e2e_setup["api_client"]
    smtp_host = e2e_setup["smtp_host"]
    smtp_port = e2e_setup["smtp_port"]
    agent_user = e2e_setup["proxy_agent_auth"]["smtp"]["username"]
    agent_token = e2e_setup["proxy_agent_auth"]["smtp"]["token"]
    recipient = upstream_smtp_cfg["user"]
    headers = e2e_setup["auth_headers"]

    timestamp = time.time()
    subjects = [f"E2E RateLimit {timestamp:.0f}-{i}" for i in range(_CAP)]
    uids_to_delete: list[int] = []

    try:
        # --- Step 2: send + approve exactly CAP messages (these relay) ---
        for subject in subjects:
            op_id = await _smtp_send_via_proxy(
                smtp_host, smtp_port, agent_user, agent_token, subject,
                to_addr=recipient,
            )
            approve_resp = await api_client.post(
                f"/api/v1/operations/{op_id}/approve", headers=headers
            )
            assert approve_resp.status_code == 200, (
                f"send within cap should succeed: "
                f"{approve_resp.status_code} {approve_resp.text}"
            )
            assert approve_resp.json()["status"] == "executed"

        # Confirm the CAP messages actually arrived (and collect UIDs to clean).
        for subject in subjects:
            uid = await wait_for_message(upstream_imap_cfg, subject, timeout=20.0)
            assert uid is not None, (
                f"within-cap message {subject!r} did not arrive within 20s"
            )
            uids_to_delete.append(uid)

        # --- Step 3: the (CAP+1)th send is staged but refused on approve ---
        over_subject = f"E2E RateLimit {timestamp:.0f}-OVER"
        over_op_id = await _smtp_send_via_proxy(
            smtp_host, smtp_port, agent_user, agent_token, over_subject,
            to_addr=recipient,
        )
        over_resp = await api_client.post(
            f"/api/v1/operations/{over_op_id}/approve", headers=headers
        )
        assert over_resp.status_code == 500, (
            f"over-cap send should be refused with 500, got "
            f"{over_resp.status_code}: {over_resp.text}"
        )
        assert "rate limit" in over_resp.text.lower(), (
            f"500 should mention the rate limit, got: {over_resp.text}"
        )

        # The refused op must be marked 'failed', not executed.
        ops_resp = await api_client.get(
            "/api/v1/operations?status=failed", headers=headers
        )
        assert ops_resp.status_code == 200
        failed_ids = [op["id"] for op in ops_resp.json()["operations"]]
        assert over_op_id in failed_ids, (
            f"refused op {over_op_id!r} should be 'failed'; failed={failed_ids}"
        )

        # --- Step 4: a send_rate_exceeded audit row was written ---
        audit_resp = await api_client.get(
            "/api/v1/audit?event=send_rate_exceeded", headers=headers
        )
        assert audit_resp.status_code == 200, (
            f"audit query failed: {audit_resp.status_code} {audit_resp.text}"
        )
        entries = audit_resp.json()["entries"]
        assert any(e.get("operation_id") == over_op_id for e in entries), (
            f"expected a send_rate_exceeded audit row for {over_op_id!r}; "
            f"got: {entries}"
        )

    finally:
        # --- Step 5: cleanup the messages that actually landed upstream ---
        for uid in uids_to_delete:
            await delete_message_by_uid(upstream_imap_cfg, uid)
