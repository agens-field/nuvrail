"""Tests for scripts/release_notes.py (the release workflow's gate + notes extractor).

The script is loaded by path because scripts/ is not a package. Each test
that touches the filesystem builds a throwaway repo root in tmp_path with just
the three files the script reads:

    tmp_path/
      pyproject.toml     [project].version
      web/package.json   "version"
      CHANGELOG.md
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "release_notes", REPO_ROOT / "scripts" / "release_notes.py"
)
release_notes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_notes)
ReleaseError = release_notes.ReleaseError

CHANGELOG = """\
# Changelog

## [Unreleased]

## [0.2.0] - 2026-10-01

### Fixed
- Newer thing.

## [0.1.0] - 2026-09-23

First release.

### Added
- A thing.

## Pre-release history (untagged)

### 2026-06-15 — Old milestone
- Old thing.

[Unreleased]: https://example.test/compare/v0.2.0...HEAD
[0.1.0]: https://example.test/releases/tag/v0.1.0
"""


def make_root(tmp_path, *, py="0.1.0", web="0.1.0", changelog=CHANGELOG):
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{py}"\n')
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(json.dumps({"version": web}))
    (tmp_path / "CHANGELOG.md").write_text(changelog)
    return tmp_path


# --- [1] tag format ---------------------------------------------------------

@pytest.mark.parametrize("tag", ["v0.1.0", "v1.22.333"])
def test_version_from_tag_accepts_plain_semver(tag):
    assert release_notes.version_from_tag(tag) == tag[1:]


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1", "v0.1.0-rc1", "v0.1.0 ", "release-0.1.0", ""])
def test_version_from_tag_rejects_everything_else(tag):
    with pytest.raises(ReleaseError, match=re.escape("vMAJOR.MINOR.PATCH")):
        release_notes.version_from_tag(tag)


# --- [2] version agreement --------------------------------------------------

def test_versions_match_passes_when_all_agree():
    release_notes.check_versions_match("0.1.0", "0.1.0", "0.1.0")


def test_versions_match_names_every_mismatch():
    with pytest.raises(ReleaseError) as exc:
        release_notes.check_versions_match("0.2.0", "0.1.0", "0.1.1")
    assert "pyproject.toml='0.1.0'" in str(exc.value)
    assert "web/package.json='0.1.1'" in str(exc.value)


# --- [3]/[4] section extraction ---------------------------------------------

def test_extract_section_stops_at_next_heading():
    notes = release_notes.extract_section(CHANGELOG, "0.1.0")
    assert notes == "First release.\n\n### Added\n- A thing.\n"
    assert "Old thing" not in notes


def test_extract_section_stops_at_link_references():
    notes = release_notes.extract_section(CHANGELOG, "0.2.0")
    assert notes == "### Fixed\n- Newer thing.\n"
    text = "## [0.3.0] - 2026-11-01\n- Last.\n\n[0.3.0]: https://example.test\n"
    assert release_notes.extract_section(text, "0.3.0") == "- Last.\n"


def test_extract_section_does_not_prefix_match_versions():
    text = "## [0.1.10] - 2026-09-23\n- Ten.\n"
    with pytest.raises(ReleaseError, match=re.escape("no '## [0.1.1]")):
        release_notes.extract_section(text, "0.1.1")


def test_extract_section_requires_a_dated_heading():
    with pytest.raises(ReleaseError, match=re.escape("no '## [0.1.0]")):
        release_notes.extract_section("## [0.1.0]\n- Undated.\n", "0.1.0")


def test_extract_section_rejects_duplicate_version_headings():
    # Regression: the backfilled history once carried its own "[0.1.0]".
    text = "## [0.1.0] - 2026-09-23\n- New.\n\n## [0.1.0] - 2026-06-15\n- Old.\n"
    with pytest.raises(ReleaseError, match=re.escape("2 '## [0.1.0]' sections")):
        release_notes.extract_section(text, "0.1.0")


def test_extract_section_rejects_empty_section():
    with pytest.raises(ReleaseError, match="empty"):
        release_notes.extract_section("## [0.1.0] - 2026-09-23\n\n## [0.0.9] - x\n", "0.1.0")


# --- CLI end to end ---------------------------------------------------------

def test_main_defaults_tag_to_pyproject_version(tmp_path, capsys):
    root = make_root(tmp_path)
    assert release_notes.main(["--root", str(root)]) == 0
    assert capsys.readouterr().out.startswith("First release.")


def test_main_writes_out_file(tmp_path):
    root = make_root(tmp_path)
    out = tmp_path / "notes.md"
    assert release_notes.main(["--root", str(root), "--tag", "v0.1.0", "--out", str(out)]) == 0
    assert out.read_text().startswith("First release.")


@pytest.mark.parametrize(
    ("kwargs", "tag", "reason"),
    [
        ({}, "v0.2.0", "does not match"),            # tag ahead of pyproject/web
        ({"web": "0.0.9"}, "v0.1.0", "web/package.json"),
        ({}, "v0.1.0-rc1", re.escape("vMAJOR.MINOR.PATCH")),
        ({"py": "0.3.0", "web": "0.3.0"}, None, re.escape("no '## [0.3.0]")),
    ],
)
def test_main_fails_closed(tmp_path, capsys, kwargs, tag, reason):
    root = make_root(tmp_path, **kwargs)
    argv = ["--root", str(root)] + (["--tag", tag] if tag else [])
    assert release_notes.main(argv) == 1
    assert re.search(reason, capsys.readouterr().err)


def test_repo_changelog_is_releasable_at_current_version():
    """The real repo must always be able to cut notes for its declared version."""
    tag, notes = release_notes.build_notes(REPO_ROOT, None)
    assert tag == f"v{release_notes.read_pyproject_version(REPO_ROOT)}"
    assert notes.strip()
