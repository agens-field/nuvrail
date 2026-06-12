"""Tests for the external-uptime monitoring manifest + validator (#34).

These cover the two things that can silently rot:
  1. the shipped manifest stays valid and keeps the host names that actually
     deploy (a stale host here = a monitor that watches nothing);
  2. the validator rejects the shapes that would produce a broken monitor.

No network: probe functions are integration-only and not exercised here.

    test surface
    ┌────────────────────────────┬──────────────────────────────────────┐
    │ shipped manifest            │ parses, validates clean, has staging  │
    │                             │ checks with the deploy.yml host names │
    │ validator (synthetic dicts) │ catches bad type / non-https / bad    │
    │                             │ port / missing host / bad email /     │
    │                             │ missing sections                      │
    └────────────────────────────┴──────────────────────────────────────┘
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MON = Path(__file__).resolve().parents[1] / "monitoring"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_uptime", MON / "check_uptime.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cu = _load_module()


# ── The shipped manifest ───────────────────────────────────────────────────
def test_shipped_manifest_parses_and_validates():
    manifest = cu.load_manifest()
    assert cu.validate(manifest) == []


def test_shipped_manifest_has_expected_staging_endpoints():
    manifest = cu.load_manifest()
    staging = manifest["environments"]["staging"]
    # Host names must match what enterprise deploy.yml actually deploys.
    assert staging["gateway_host"] == "nuvrail-staging-gateway.fly.dev"
    assert staging["web_host"] == "nuvrail-staging-web.fly.dev"
    by_type = {(c["type"], c.get("port") or c.get("url")) for c in staging["checks"]}
    assert ("http", "https://nuvrail-staging-gateway.fly.dev/health") in by_type
    assert ("http", "https://nuvrail-staging-web.fly.dev/") in by_type
    assert ("tcp", 993) in by_type
    assert ("tcp", 465) in by_type


def test_health_check_has_coldstart_tolerance():
    # The /health check must carry a generous timeout because the API
    # auto-stops when idle and cold-starts on the first probe.
    manifest = cu.load_manifest()
    health = next(
        c for c in manifest["environments"]["staging"]["checks"]
        if c.get("url", "").endswith("/health")
    )
    assert health["http_request_timeout_seconds"] >= 10
    assert health.get("expected_body_contains") == "ok"


def test_manifest_alert_email_present():
    manifest = cu.load_manifest()
    assert "@" in manifest["alerting"]["email"]


# ── The validator, on synthetic inputs ─────────────────────────────────────
def _good() -> dict:
    return {
        "defaults": {"expected_status_code": 200},
        "environments": {
            "staging": {
                "checks": [
                    {"name": "h", "type": "http", "url": "https://x.fly.dev/health"},
                    {"name": "t", "type": "tcp", "host": "x.fly.dev", "port": 993},
                ]
            }
        },
        "alerting": {"email": "kc@nuvrail.com"},
    }


def test_validator_accepts_good():
    assert cu.validate(_good()) == []


@pytest.mark.parametrize("section", ["defaults", "environments", "alerting"])
def test_validator_flags_missing_top_section(section):
    m = _good()
    del m[section]
    problems = cu.validate(m)
    assert any(section in p for p in problems)


def test_validator_flags_bad_check_type():
    m = _good()
    m["environments"]["staging"]["checks"][0]["type"] = "ping"
    assert any("type must be one of" in p for p in cu.validate(m))


def test_validator_flags_non_https_http_check():
    m = _good()
    m["environments"]["staging"]["checks"][0]["url"] = "http://x.fly.dev/health"
    assert any("https://" in p for p in cu.validate(m))


def test_validator_flags_tcp_missing_host():
    m = _good()
    del m["environments"]["staging"]["checks"][1]["host"]
    assert any("tcp check needs a host" in p for p in cu.validate(m))


@pytest.mark.parametrize("port", [0, 70000, "993", None])
def test_validator_flags_bad_tcp_port(port):
    m = _good()
    m["environments"]["staging"]["checks"][1]["port"] = port
    assert any("valid port" in p for p in cu.validate(m))


def test_validator_flags_empty_env():
    m = _good()
    m["environments"]["staging"]["checks"] = []
    assert any("non-empty checks" in p for p in cu.validate(m))


def test_validator_flags_bad_email():
    m = _good()
    m["alerting"]["email"] = "not-an-email"
    assert any("email" in p for p in cu.validate(m))
