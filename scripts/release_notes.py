#!/usr/bin/env python3
"""Validate a release tag and extract its CHANGELOG section as release notes.

Used by .github/workflows/release.yml. Kept as a standalone script (stdlib
only) so the exact same logic runs in CI, in the release job, and locally:

    python scripts/release_notes.py                 # check pyproject's version
    python scripts/release_notes.py --tag v0.1.0 --out notes.md

Pipeline (any failure exits non-zero with a one-line reason):

    tag (or pyproject version)
        |
        v
    [1] tag format  ^v<MAJOR>.<MINOR>.<PATCH>$   (no pre-release suffixes yet)
        |
        v
    [2] tag == v + pyproject.toml [project].version == v + web/package.json version
        |
        v
    [3] CHANGELOG.md has EXACTLY ONE "## [X.Y.Z] - YYYY-MM-DD" heading
        |
        v
    [4] section body (up to the next "## " heading / link refs) is non-empty
        |
        v
    notes -> stdout or --out file

Rule [3] exists because a backfilled, untagged section once reused a real
version number; a duplicate heading must fail loudly rather than publish the
wrong notes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - CI runs 3.11
    tomllib = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent

TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")
# A link-reference definition, e.g. "[0.1.0]: https://...", ends a section.
LINK_REF_RE = re.compile(r"^\[[^\]]+\]:\s+\S+")


class ReleaseError(Exception):
    """A release precondition failed; the message is shown to the operator."""


def version_from_tag(tag: str) -> str:
    match = TAG_RE.match(tag)
    if not match:
        raise ReleaseError(
            f"tag {tag!r} is not of the form vMAJOR.MINOR.PATCH (e.g. v0.1.0)"
        )
    return match.group("version")


def check_versions_match(version: str, pyproject_version: str, web_version: str) -> None:
    mismatched = {
        name: value
        for name, value in (
            ("pyproject.toml", pyproject_version),
            ("web/package.json", web_version),
        )
        if value != version
    }
    if mismatched:
        detail = ", ".join(f"{name}={value!r}" for name, value in mismatched.items())
        raise ReleaseError(
            f"tag version {version!r} does not match {detail}; bump them together"
        )


def extract_section(changelog: str, version: str) -> str:
    """Return the body of the ``## [version] - date`` section, stripped."""
    heading_re = re.compile(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\s*$"
    )
    lines = changelog.splitlines()
    starts = [i for i, line in enumerate(lines) if heading_re.match(line)]
    if not starts:
        raise ReleaseError(
            f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section"
        )
    if len(starts) > 1:
        raise ReleaseError(
            f"CHANGELOG.md has {len(starts)} '## [{version}]' sections; expected exactly one"
        )

    body: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.startswith("## ") or LINK_REF_RE.match(line):
            break
        body.append(line)

    notes = "\n".join(body).strip()
    if not notes:
        raise ReleaseError(f"CHANGELOG.md section [{version}] is empty")
    return notes + "\n"


def read_pyproject_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)["project"]["version"]
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)  # pragma: no cover
    if not match:  # pragma: no cover
        raise ReleaseError("could not read [project].version from pyproject.toml")
    return match.group(1)  # pragma: no cover


def read_web_version(root: Path) -> str:
    return json.loads((root / "web" / "package.json").read_text(encoding="utf-8"))["version"]


def build_notes(root: Path, tag: str | None) -> tuple[str, str]:
    """Run checks [1]-[4]; return (tag, notes)."""
    pyproject_version = read_pyproject_version(root)
    tag = tag or f"v{pyproject_version}"
    version = version_from_tag(tag)
    check_versions_match(version, pyproject_version, read_web_version(root))
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return tag, extract_section(changelog, version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="release tag, e.g. v0.1.0 (default: v + pyproject version)")
    parser.add_argument("--out", type=Path, help="write notes here instead of stdout")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        tag, notes = build_notes(args.root, args.tag)
    except ReleaseError as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1

    if args.out:
        args.out.write_text(notes, encoding="utf-8")
        print(f"release_notes: {tag} OK ({len(notes.splitlines())} lines -> {args.out})")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
