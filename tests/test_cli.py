import json
import subprocess
from pathlib import Path

import pytest

from harness import cli


def test_init_creates_config(tmp_path: Path):
    rc = cli.main(["--repo", str(tmp_path), "init"])
    assert rc == 0
    assert (tmp_path / ".dev-loop" / "config.yaml").exists()


def test_init_appends_gitignore(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("# existing\nfoo\n")
    rc = cli.main(["--repo", str(tmp_path), "init"])
    assert rc == 0
    content = (tmp_path / ".gitignore").read_text()
    assert ".dev-loop/runs/" in content
    assert "foo" in content


def test_init_idempotent_with_force(tmp_path: Path):
    cli.main(["--repo", str(tmp_path), "init"])
    rc = cli.main(["--repo", str(tmp_path), "init"])
    assert rc == 1  # without --force
    rc = cli.main(["--repo", str(tmp_path), "init", "--force"])
    assert rc == 0


def test_init_starter_installs_demo_scenario(tmp_path: Path):
    """``dev-loop init --starter`` should put a runnable scenario in the
    repo so a brand new user can replay it without picking files by hand."""
    rc = cli.main(["--repo", str(tmp_path), "init", "--starter"])
    assert rc == 0
    starter = tmp_path / "scenarios" / "hello-dev-loop"
    assert starter.is_dir()
    assert (starter / "task_request.md").exists()
    assert (starter / "task_contract.json").exists()
    assert (starter / "implementation_result.json").exists()
    assert (starter / "e2e_result.json").exists()


def test_config_show_emits_json(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()  # discard init output
    rc = cli.main(["--repo", str(tmp_path), "config", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["default_provider"] == "replay"


def test_implement_unknown_provider_clean_error(tmp_path: Path, capsys):
    """``--provider unknown`` must produce a friendly error and exit 2,
    not a Python stack trace. End users running ``dev-loop implement``
    shouldn't see ``Traceback (most recent call last)`` for a typo.
    """
    rc = cli.main([
        "--repo", str(tmp_path),
        "implement",
        "--request", "anything",
        "--provider", "definitely-not-a-real-provider",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    # Friendly error on stderr, no Python traceback noise.
    assert "unknown provider" in captured.err.lower()
    assert "traceback" not in captured.err.lower()


def test_schema_validate_ok(tmp_path: Path):
    obj = {
        "type": "task_contract",
        "implementation_goal": "x",
        "assumptions": [], "success_criteria": ["s"],
        "non_goals": [], "likely_components": [], "validation_plan": [],
        "ambiguities": [], "can_start_without_human": True,
    }
    f = tmp_path / "obj.json"
    f.write_text(json.dumps(obj))
    rc = cli.main(["schema", "validate", str(f), "task_contract.v1.json"])
    assert rc == 0
