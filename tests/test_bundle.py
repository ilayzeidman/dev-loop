"""Tests for the config-bundle export/import surface.

Covers the pure :mod:`harness.bundle` module — the HTTP plumbing has its
own coverage in :mod:`tests.test_ui_server`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.bundle import (
    BUNDLE_FORMAT,
    BUNDLE_FORMAT_VERSION,
    BundleError,
    apply_bundle,
    build_bundle,
    bundle_to_json,
    preview_apply,
    validate_bundle,
)
from harness.config import write_default_config


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_build_bundle_empty_repo(tmp_path: Path):
    """A pristine directory still produces a valid (mostly-empty) bundle."""
    b = build_bundle(tmp_path)
    assert b["format"] == BUNDLE_FORMAT
    assert b["format_version"] == BUNDLE_FORMAT_VERSION
    assert b["config"]["present"] is False
    assert b["config"]["yaml"] == ""
    assert b["scenarios"] == []
    # The package ships playbooks, so this should be non-empty even on a
    # fresh user repo (they're the harness defaults).
    assert isinstance(b["playbooks"], list)
    # Roundtrips through JSON.
    text = bundle_to_json(b)
    assert json.loads(text) == b


def test_build_bundle_includes_config_and_scenarios(tmp_path: Path):
    write_default_config(tmp_path)
    _write(tmp_path / "scenarios" / "foo-001" / "task_request.md", "# foo\n")
    _write(tmp_path / "scenarios" / "foo-001" / "files" / "src" / "x.py", "print('hi')\n")
    # A binary file should be skipped — bundles are text-only.
    (tmp_path / "scenarios" / "foo-001" / "blob.bin").write_bytes(b"\x00\x01\x02")

    b = build_bundle(tmp_path, note="tuned for foo")
    assert b["config"]["present"] is True
    assert "default_provider" in b["config"]["yaml"]
    assert b["note"] == "tuned for foo"
    sc = [s for s in b["scenarios"] if s["name"] == "foo-001"]
    assert len(sc) == 1
    files = sc[0]["files"]
    assert "task_request.md" in files
    assert files["files/src/x.py"] == "print('hi')\n"
    assert "blob.bin" not in files
    assert "blob.bin" in sc[0].get("skipped", [])


def test_validate_bundle_rejects_garbage():
    with pytest.raises(BundleError, match="bundle must be"):
        validate_bundle("not an object")
    with pytest.raises(BundleError, match="unrecognised bundle format"):
        validate_bundle({"format": "something-else", "format_version": 1})
    with pytest.raises(BundleError, match="unsupported bundle format_version"):
        validate_bundle({"format": BUNDLE_FORMAT, "format_version": 999})


def test_validate_bundle_rejects_unsafe_scenario_paths():
    bad = {
        "format": BUNDLE_FORMAT,
        "format_version": 1,
        "scenarios": [{"name": "ok", "files": {"../escape.txt": "x"}}],
        "playbooks": [],
    }
    with pytest.raises(BundleError, match="unsafe file path"):
        validate_bundle(bad)

    bad2 = {
        "format": BUNDLE_FORMAT,
        "format_version": 1,
        "scenarios": [{"name": "..", "files": {}}],
        "playbooks": [],
    }
    with pytest.raises(BundleError, match="name is missing or unsafe"):
        validate_bundle(bad2)


def test_preview_apply_classifies_new_conflict_identical(tmp_path: Path):
    write_default_config(tmp_path)
    # Existing identical scenario file:
    _write(tmp_path / "scenarios" / "foo" / "task_request.md", "hello\n")
    # Existing differing scenario file:
    _write(tmp_path / "scenarios" / "foo" / "task_contract.json", "{\"a\": 1}\n")

    bundle = {
        "format": BUNDLE_FORMAT,
        "format_version": 1,
        "config": {"present": True, "yaml": "default_provider: claude\n"},
        "scenarios": [{
            "name": "foo",
            "files": {
                "task_request.md": "hello\n",         # identical
                "task_contract.json": "{\"a\": 2}\n",  # conflict
                "extra.txt": "new!\n",                  # new
            },
        }],
        "playbooks": [],
    }
    preview = preview_apply(bundle, tmp_path)
    by_path = {c["path"]: c for c in preview["changes"]}
    assert by_path["scenarios/foo/task_request.md"]["status"] == "identical"
    assert by_path["scenarios/foo/task_contract.json"]["status"] == "conflict"
    assert by_path["scenarios/foo/extra.txt"]["status"] == "new"
    # Existing .dev-loop/config.yaml differs from the bundle's, so it's a conflict.
    assert by_path[".dev-loop/config.yaml"]["status"] == "conflict"
    assert preview["totals"] == {"new": 1, "conflict": 2, "identical": 1}


def test_apply_bundle_skip_keeps_destination(tmp_path: Path):
    _write(tmp_path / "scenarios" / "foo" / "task_request.md", "keep me\n")
    bundle = {
        "format": BUNDLE_FORMAT, "format_version": 1,
        "config": {"present": False, "yaml": ""},
        "scenarios": [{
            "name": "foo",
            "files": {
                "task_request.md": "from bundle\n",
                "fresh.txt": "fresh\n",
            },
        }],
        "playbooks": [],
    }
    report = apply_bundle(bundle, tmp_path, on_conflict="skip")
    actions = {a["path"]: a for a in report["actions"]}
    assert actions["scenarios/foo/task_request.md"]["action"] == "skipped"
    assert actions["scenarios/foo/fresh.txt"]["action"] == "wrote"
    assert (tmp_path / "scenarios" / "foo" / "task_request.md").read_text(
        encoding="utf-8") == "keep me\n"
    assert (tmp_path / "scenarios" / "foo" / "fresh.txt").read_text(
        encoding="utf-8") == "fresh\n"


def test_apply_bundle_overwrite_replaces(tmp_path: Path):
    _write(tmp_path / "scenarios" / "foo" / "task_request.md", "old\n")
    bundle = {
        "format": BUNDLE_FORMAT, "format_version": 1,
        "config": {"present": False, "yaml": ""},
        "scenarios": [{"name": "foo", "files": {"task_request.md": "new\n"}}],
        "playbooks": [],
    }
    report = apply_bundle(bundle, tmp_path, on_conflict="overwrite")
    actions = {a["path"]: a["action"] for a in report["actions"]}
    assert actions["scenarios/foo/task_request.md"] == "overwrote"
    assert (tmp_path / "scenarios" / "foo" / "task_request.md").read_text(
        encoding="utf-8") == "new\n"


def test_apply_bundle_rename_keeps_both(tmp_path: Path):
    _write(tmp_path / "scenarios" / "foo" / "task_request.md", "old\n")
    bundle = {
        "format": BUNDLE_FORMAT, "format_version": 1,
        "config": {"present": False, "yaml": ""},
        "scenarios": [{"name": "foo", "files": {"task_request.md": "new\n"}}],
        "playbooks": [],
    }
    report = apply_bundle(bundle, tmp_path, on_conflict="rename")
    actions = [a for a in report["actions"]
               if a["path"] == "scenarios/foo/task_request.md"]
    assert len(actions) == 1
    assert actions[0]["action"] == "renamed"
    # Original kept; the renamed sibling carries the bundle's content.
    assert (tmp_path / "scenarios" / "foo" / "task_request.md").read_text(
        encoding="utf-8") == "old\n"
    assert (tmp_path / "scenarios" / "foo" / "task_request.md.imported"
            ).read_text(encoding="utf-8") == "new\n"


def test_apply_bundle_include_filter(tmp_path: Path):
    """Per-item include list lets the UI write only checked entries."""
    bundle = {
        "format": BUNDLE_FORMAT, "format_version": 1,
        "config": {"present": True, "yaml": "default_provider: claude\n"},
        "scenarios": [{
            "name": "foo",
            "files": {
                "a.txt": "A\n", "b.txt": "B\n",
            },
        }],
        "playbooks": [],
    }
    report = apply_bundle(
        bundle, tmp_path,
        include=["scenarios/foo/a.txt"],
    )
    actions = {a["path"]: a["action"] for a in report["actions"]}
    assert actions == {"scenarios/foo/a.txt": "wrote"}
    assert (tmp_path / "scenarios" / "foo" / "a.txt").exists()
    assert not (tmp_path / "scenarios" / "foo" / "b.txt").exists()
    # And the config — also not included — must remain absent.
    assert not (tmp_path / ".dev-loop" / "config.yaml").exists()


def test_apply_bundle_rejects_bad_conflict_policy(tmp_path: Path):
    bundle = {
        "format": BUNDLE_FORMAT, "format_version": 1,
        "config": {"present": False, "yaml": ""},
        "scenarios": [], "playbooks": [],
    }
    with pytest.raises(BundleError, match="on_conflict must be"):
        apply_bundle(bundle, tmp_path, on_conflict="merge")


def test_apply_bundle_refuses_traversal_escape(tmp_path: Path):
    """The validator should already block ``..`` paths, but the writer
    also enforces a sandbox boundary as belt-and-braces."""
    bundle = {
        "format": BUNDLE_FORMAT, "format_version": 1,
        "config": {"present": False, "yaml": ""},
        "scenarios": [{
            "name": "ok",
            # Validator blocks this:
            "files": {"../../escape.txt": "pwn\n"},
        }],
        "playbooks": [],
    }
    with pytest.raises(BundleError):
        apply_bundle(bundle, tmp_path)


def test_export_import_roundtrip_into_fresh_repo(tmp_path: Path):
    """The whole point: tune RepoA, ship a bundle to RepoB, get the same files."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir(); repo_b.mkdir()

    write_default_config(repo_a)
    _write(repo_a / ".dev-loop" / "config.yaml",
           "default_provider: claude\npolicy:\n  max_code_iterations: 9\n")
    _write(repo_a / "scenarios" / "encoder-oom-001" / "task_request.md",
           "# encoder OOM\n")
    _write(repo_a / "scenarios" / "encoder-oom-001" / "task_contract.json",
           '{"type":"task_contract","implementation_goal":"x"}\n')

    bundle = build_bundle(repo_a, note="encoder triage preset")
    # JSON-roundtrip — simulates ship+paste.
    bundle = json.loads(bundle_to_json(bundle))
    validate_bundle(bundle)

    preview = preview_apply(bundle, repo_b)
    # repo_b started empty, so nothing should *conflict*. (Playbooks live
    # in the harness package directory so they'll show up as "identical"
    # in this in-process test — that's fine.)
    assert not any(c["status"] == "conflict" for c in preview["changes"])
    assert preview["note"] == "encoder triage preset"
    # The repo-local config and scenario files are net new.
    new_paths = {c["path"] for c in preview["changes"] if c["status"] == "new"}
    assert ".dev-loop/config.yaml" in new_paths
    assert "scenarios/encoder-oom-001/task_request.md" in new_paths

    report = apply_bundle(bundle, repo_b)
    assert all(a["action"] in ("wrote", "identical") for a in report["actions"])
    assert (repo_b / ".dev-loop" / "config.yaml").read_text(encoding="utf-8") == \
        (repo_a / ".dev-loop" / "config.yaml").read_text(encoding="utf-8")
    assert (repo_b / "scenarios" / "encoder-oom-001" / "task_request.md").read_text(
        encoding="utf-8") == "# encoder OOM\n"

    # A second apply is a no-op (everything identical).
    again = apply_bundle(bundle, repo_b)
    actions = {a["action"] for a in again["actions"]}
    assert actions <= {"identical"}
