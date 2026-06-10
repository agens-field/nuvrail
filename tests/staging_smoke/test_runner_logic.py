"""
Unit tests for the staging-monitor runner's *aggregation + exit* logic.

These do NOT hit the network — they exercise the parts that decide whether
the scheduled workflow alerts: Check status transitions, Report summary
math, the "skipped never fails the run" rule, and the _timed wrapper's
crash-to-failure guarantee (a monitor that crashes on its own bug is
worse than useless).

  Check.fail / .skip / .ok  ──► Report.{failed,skipped,passed}
                                      │
                                      └─► main() exit code:
                                          any failed  -> 1 (alert)
                                          only skipped -> 0 (degrade)
"""
from __future__ import annotations

from tests.staging_smoke import runner as R


def test_check_status_transitions():
    c = R.Check(name="x")
    assert c.status == "pending"
    c.ok("good")
    assert c.status == "ok" and c.detail == "good"
    c.fail("bad")
    assert c.status == "failed" and c.detail == "bad"
    c.skip("later")
    assert c.status == "skipped"


def test_report_summary_partitions():
    rep = R.Report(started_at="t", gateway_url="g", web_url="w")
    rep.checks = [
        R.Check("a", status="ok"),
        R.Check("b", status="failed"),
        R.Check("c", status="skipped"),
        R.Check("d", status="ok"),
    ]
    assert len(rep.passed) == 2
    assert len(rep.failed) == 1
    assert len(rep.skipped) == 1


def test_skipped_only_does_not_alert(monkeypatch):
    """A run where everything is skipped (no creds, staging reachable) must
    exit 0 — skipped checks are graceful degradation, not failures."""
    rep = R.Report(started_at="t", gateway_url="g", web_url="w")
    rep.checks = [R.Check("a", status="ok"), R.Check("b", status="skipped")]
    monkeypatch.setattr(R, "run", lambda: rep)
    assert R.main() == 0


def test_any_failure_alerts(monkeypatch):
    rep = R.Report(started_at="t", gateway_url="g", web_url="w")
    rep.checks = [R.Check("a", status="ok"), R.Check("b", status="failed")]
    monkeypatch.setattr(R, "run", lambda: rep)
    assert R.main() == 1


def test_timed_turns_exception_into_failure():
    """If a check function raises, _timed must record it as failed, never
    propagate — the monitor itself must not crash."""

    def boom(_client, _check):
        raise RuntimeError("network exploded")

    result = R._timed("boom", boom, client=None)
    assert result.status == "failed"
    assert "RuntimeError: network exploded" in result.detail
    assert result.duration_ms >= 0


def test_timed_flags_check_that_forgets_to_set_status():
    def forgetful(_client, _check):
        pass  # never sets a status

    result = R._timed("forgetful", forgetful, client=None)
    assert result.status == "failed"
    assert "did not record a result" in result.detail


def test_report_json_is_valid_and_has_summary():
    import json

    rep = R.Report(started_at="t", gateway_url="g", web_url="w")
    rep.checks = [R.Check("a", status="ok"), R.Check("b", status="skipped")]
    parsed = json.loads(rep.to_json())
    assert parsed["summary"] == {"passed": 1, "failed": 0, "skipped": 1}
    assert parsed["gateway_url"] == "g"
    assert len(parsed["checks"]) == 2


# --------------------------------------------------------------------------
# Credentialed-flow gating: with no secrets set, the two proxy checks must
# degrade to skipped (never failed) and name the missing env vars so the
# gap is actionable in every report. These never touch the network.
# --------------------------------------------------------------------------


def _clear_cred_env(monkeypatch):
    # The module-level constants are what the checks read; null them directly.
    monkeypatch.setattr(R, "MONITOR_IMAP_USER", None)
    monkeypatch.setattr(R, "MONITOR_IMAP_PASSWORD", None)
    monkeypatch.setattr(R, "MONITOR_API_TOKEN", None)
    monkeypatch.setattr(R, "PROXY_SMTP_PORT", None)
    monkeypatch.setattr(R, "PROXY_IMAP_PORT", None)


def test_proxy_smtp_ready_lists_all_missing(monkeypatch):
    _clear_cred_env(monkeypatch)
    reason = R._proxy_smtp_ready()
    assert reason is not None
    for var in (
        "NUVRAIL_STAGING_MONITOR_IMAP_USER",
        "NUVRAIL_STAGING_MONITOR_IMAP_PASSWORD",
        "NUVRAIL_STAGING_PROXY_SMTP_PORT",
        "NUVRAIL_STAGING_MONITOR_API_TOKEN",
    ):
        assert var in reason
    assert "#18" in reason


def test_proxy_smtp_ready_passes_when_all_present(monkeypatch):
    monkeypatch.setattr(R, "MONITOR_IMAP_USER", "mon@nuvrail.test")
    monkeypatch.setattr(R, "MONITOR_IMAP_PASSWORD", "pw")
    monkeypatch.setattr(R, "MONITOR_API_TOKEN", "tok")
    monkeypatch.setattr(R, "PROXY_SMTP_PORT", "10587")
    assert R._proxy_smtp_ready() is None


def test_write_approve_deliver_skips_without_creds(monkeypatch):
    _clear_cred_env(monkeypatch)
    c = R.Check("proxy_write_approve_deliver")
    R.check_proxy_write_approve_deliver(client=None, c=c)  # must not hit network
    assert c.status == "skipped"
    assert "#18" in c.detail


def test_rejection_revert_skips_without_creds(monkeypatch):
    _clear_cred_env(monkeypatch)
    c = R.Check("rejection_revert")
    R.check_rejection_revert(client=None, c=c)  # must not hit network
    assert c.status == "skipped"


def test_rejection_revert_skips_when_only_imap_port_missing(monkeypatch):
    # SMTP creds present but no IMAP proxy port => still a clean skip, not a fail.
    monkeypatch.setattr(R, "MONITOR_IMAP_USER", "mon@nuvrail.test")
    monkeypatch.setattr(R, "MONITOR_IMAP_PASSWORD", "pw")
    monkeypatch.setattr(R, "MONITOR_API_TOKEN", "tok")
    monkeypatch.setattr(R, "PROXY_SMTP_PORT", "10587")
    monkeypatch.setattr(R, "PROXY_IMAP_PORT", None)
    c = R.Check("rejection_revert")
    R.check_rejection_revert(client=None, c=c)
    assert c.status == "skipped"
    assert "PROXY_IMAP_PORT" in c.detail
