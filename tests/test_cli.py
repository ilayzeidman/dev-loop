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


def test_config_show_emits_json(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()  # discard init output
    rc = cli.main(["--repo", str(tmp_path), "config", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["default_provider"] == "replay"


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
