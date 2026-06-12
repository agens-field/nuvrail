#!/usr/bin/env python3
"""Validate and dry-run the external uptime-check manifest (#34).

Vendor-neutral companion to ``uptime-checks.yaml``. It exists so the manifest is
not just documentation: it can be parsed, validated, and actually executed from
any box (a CI runner, a laptop) WITHOUT a Better Uptime account. That gives us:

  1. a syntax/shape check on the manifest (catch a typo'd host or bad port);
  2. a real off-host probe of every endpoint, so "is staging actually up?" has a
     zero-credential answer before the paid monitor is wired.

    PROBE FLOW (per check)
    ┌────────────┬───────────────────────────────────────────────┐
    │ type: http │ HTTPS GET url → assert status + body-substring  │
    │ type: tcp  │ open socket host:port → assert connect succeeds │
    └────────────┴───────────────────────────────────────────────┘

This is an ops/dev tool, NOT imported by the gateway runtime, so its only extra
dependency (PyYAML) lives in the dev/ops toolchain, not in core's runtime deps.

Usage:
    python monitoring/check_uptime.py --validate                 # shape only
    python monitoring/check_uptime.py --env staging              # probe staging
    python monitoring/check_uptime.py --env staging --validate   # both

Exit code is non-zero if validation fails or (in probe mode) any check is down,
so it composes into CI or a cron one-liner.
"""
from __future__ import annotations

import argparse
import socket
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - guidance path
    sys.stderr.write(
        "PyYAML is required for this ops tool: pip install pyyaml\n"
        "(It is a dev/ops dependency only; the gateway runtime does not need it.)\n"
    )
    raise

MANIFEST = Path(__file__).with_name("uptime-checks.yaml")
DEFAULT_HTTP_TIMEOUT = 10
DEFAULT_TCP_TIMEOUT = 10
VALID_TYPES = {"http", "tcp"}
REQUIRED_TOP = ("defaults", "environments", "alerting")


def load_manifest(path: Path = MANIFEST) -> dict:
    return yaml.safe_load(path.read_text()) or {}


# ── Validation ─────────────────────────────────────────────────────────────
def validate(manifest: dict) -> list[str]:
    """Return a list of human-readable problems; empty list means valid."""
    errors: list[str] = []
    for key in REQUIRED_TOP:
        if key not in manifest:
            errors.append(f"missing top-level section: {key}")
    envs = manifest.get("environments")
    if not isinstance(envs, dict) or not envs:
        errors.append("environments: must be a non-empty mapping")
        return errors
    for env_name, env in envs.items():
        checks = (env or {}).get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append(f"{env_name}: must have a non-empty checks list")
            continue
        for i, chk in enumerate(checks):
            ctx = f"{env_name}.checks[{i}]"
            ctype = chk.get("type")
            if ctype not in VALID_TYPES:
                errors.append(f"{ctx}: type must be one of {sorted(VALID_TYPES)}, got {ctype!r}")
            if ctype == "http" and not str(chk.get("url", "")).startswith("https://"):
                errors.append(f"{ctx}: http check needs an https:// url")
            if ctype == "tcp":
                if not chk.get("host"):
                    errors.append(f"{ctx}: tcp check needs a host")
                port = chk.get("port")
                if not isinstance(port, int) or not (1 <= port <= 65535):
                    errors.append(f"{ctx}: tcp check needs a valid port, got {port!r}")
    alerting = manifest.get("alerting", {})
    if isinstance(alerting, dict) and "@" not in str(alerting.get("email", "")):
        errors.append("alerting.email: expected an email address")
    return errors


# ── Probing ────────────────────────────────────────────────────────────────
def probe_http(url: str, timeout: int, expect_status: int, expect_body: str) -> tuple[bool, str]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "nuvrail-uptime/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status = resp.status
            body = resp.read(2048).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - any failure is "down" for a probe
        return False, f"request failed: {exc}"
    if status != expect_status:
        return False, f"status {status} != {expect_status}"
    if expect_body and expect_body not in body:
        return False, f"body missing {expect_body!r}"
    return True, f"{status} ok"


def probe_tcp(host: str, port: int, timeout: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "connect ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"connect failed: {exc}"


def run_env(manifest: dict, env_name: str) -> int:
    env = manifest.get("environments", {}).get(env_name)
    if not env:
        print(f"unknown environment: {env_name}", file=sys.stderr)
        return 2
    defaults = manifest.get("defaults", {})
    http_timeout = defaults.get("http_request_timeout_seconds", DEFAULT_HTTP_TIMEOUT)
    expect_status_default = defaults.get("expected_status_code", 200)
    failures = 0
    for chk in env["checks"]:
        name = chk.get("name", "<unnamed>")
        if chk.get("type") == "http":
            ok, detail = probe_http(
                chk["url"],
                chk.get("http_request_timeout_seconds", http_timeout),
                chk.get("expected_status_code", expect_status_default),
                chk.get("expected_body_contains", ""),
            )
        else:
            ok, detail = probe_tcp(chk["host"], chk["port"], DEFAULT_TCP_TIMEOUT)
        mark = "UP  " if ok else "DOWN"
        print(f"[{mark}] {name} — {detail}")
        failures += 0 if ok else 1
    return 1 if failures else 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate / dry-run Nuvrail uptime checks (#34)")
    ap.add_argument("--validate", action="store_true", help="validate manifest shape")
    ap.add_argument("--env", help="probe an environment's endpoints (e.g. staging)")
    args = ap.parse_args(argv)
    if not args.validate and not args.env:
        ap.error("pass --validate and/or --env <name>")

    manifest = load_manifest()
    rc = 0
    if args.validate:
        problems = validate(manifest)
        if problems:
            print("MANIFEST INVALID:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            rc = 2
        else:
            print("manifest: valid")
    if args.env:
        rc = run_env(manifest, args.env) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
