#!/usr/bin/env python3
"""
report_ci_kpis.py — CI KPI reporter for Ripple Path (Engineering Health path).

Reads pytest JSON report output, computes pass rate, and POSTs the values
to the Ripple Path API for two KPIs on the Engineering Health path:

  - Test suite pass rate per release  (KPI 3e755749)
  - Deploy success rate               (KPI 537c8ca8)

Called from .github/workflows/ci.yml after every successful push to main.

Usage:
    python scripts/report_ci_kpis.py <pytest-report.json>

Required env:
    RIPPLEPATH_API_TOKEN   Bearer token for the Ripple Path agent API.

Optional env:
    RIPPLEPATH_BASE_URL    Defaults to https://srp.animalhorde.com
    RIPPLEPATH_ENG_PATH    Engineering Health path ID (default below)

The script exits 0 even if the API call fails — CI should not break because
the KPI reporter is down. Failures are printed to stderr for visibility.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("RIPPLEPATH_BASE_URL", "https://srp.animalhorde.com").rstrip("/")
TOKEN = os.environ.get("RIPPLEPATH_API_TOKEN", "")
ENG_PATH_ID = os.environ.get(
    "RIPPLEPATH_ENG_PATH", "6c8bb4ae-fa0d-440e-993c-5859b9a0a769"
)

# KPI IDs on Engineering Health path
KPI_TEST_PASS_RATE = "3e755749-fa6d-4d8f-9357-4f34dbf418d6"
KPI_DEPLOY_SUCCESS = "537c8ca8-2042-4497-a47f-497caa53539f"


def _post_kpi(path_id: str, kpi_id: str, value: float, note: str) -> None:
    if not TOKEN:
        print(f"[report_ci_kpis] No RIPPLEPATH_API_TOKEN — skipping KPI {kpi_id}", file=sys.stderr)
        return

    payload = json.dumps({"value": round(value, 4), "note": note}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/agent/paths/{path_id}/kpis/{kpi_id}",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[report_ci_kpis] KPI {kpi_id} updated → {value:.2f}% ({resp.status})")
    except Exception as exc:  # noqa: BLE001
        print(f"[report_ci_kpis] WARNING: KPI {kpi_id} update failed: {exc}", file=sys.stderr)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: report_ci_kpis.py <pytest-report.json>", file=sys.stderr)
        sys.exit(1)

    report_path = Path(sys.argv[1])
    if not report_path.exists():
        print(f"[report_ci_kpis] Report file not found: {report_path}", file=sys.stderr)
        sys.exit(0)  # Don't break CI

    try:
        data = json.loads(report_path.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"[report_ci_kpis] Failed to parse report: {exc}", file=sys.stderr)
        sys.exit(0)

    summary = data.get("summary", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    errors = summary.get("errors", 0)

    if total == 0:
        print("[report_ci_kpis] No tests found in report — skipping", file=sys.stderr)
        sys.exit(0)

    # Test pass rate: passed / (passed + failed + errors), skipped excluded
    run = passed + failed + errors
    pass_rate = (passed / run * 100) if run > 0 else 0.0
    note = f"CI run: {passed} passed, {failed} failed, {errors} errors of {total} total"

    print(f"[report_ci_kpis] Test pass rate: {pass_rate:.2f}% — {note}")
    _post_kpi(ENG_PATH_ID, KPI_TEST_PASS_RATE, pass_rate, note)

    # Deploy success rate: 100% if CI passed (all tests green), 0% if any failures.
    # On a successful push to main that passes CI, this is a successful deploy.
    deploy_success = 100.0 if (failed == 0 and errors == 0) else 0.0
    deploy_note = (
        "All tests green — deploy counted as successful"
        if deploy_success == 100.0
        else f"Deploy blocked by {failed + errors} test failure(s)"
    )
    print(f"[report_ci_kpis] Deploy success rate: {deploy_success:.0f}% — {deploy_note}")
    _post_kpi(ENG_PATH_ID, KPI_DEPLOY_SUCCESS, deploy_success, deploy_note)


if __name__ == "__main__":
    main()
