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


def test_config_validate_clean(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main(["--repo", str(tmp_path), "config", "validate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_config_validate_no_file(tmp_path: Path, capsys):
    rc = cli.main(["--repo", str(tmp_path), "config", "validate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no config file" in out.lower()


def test_config_validate_flags_errors(tmp_path: Path, capsys):
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text(
        "policy:\n  max_code_iterations: 0\n"
    )
    rc = cli.main(["--repo", str(tmp_path), "config", "validate"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "error" in out.lower()
    assert "max_code_iterations" in out


def test_config_validate_warnings_pass_without_strict(tmp_path: Path, capsys):
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text("totally_unknown_thing: 1\n")
    rc = cli.main(["--repo", str(tmp_path), "config", "validate"])
    assert rc == 0
    rc = cli.main(["--repo", str(tmp_path), "config", "validate", "--strict"])
    assert rc == 1


def test_config_show_surfaces_warnings_on_stderr(tmp_path: Path, capsys):
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text("default_provider: vinyl\n")
    rc = cli.main(["--repo", str(tmp_path), "config", "show"])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["default_provider"] == "vinyl"
    assert "default_provider" in captured.err
    assert "warning" in captured.err.lower()


def test_implement_aborts_on_config_errors(tmp_path: Path, capsys):
    """A typo that lands in ``max_code_iterations`` should fail fast with
    a clear message — not surface as an opaque downstream crash. Users
    who only run ``dev-loop implement`` should still see config errors."""
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text(
        "policy:\n  max_code_iterations: 0\n"
    )
    rc = cli.main([
        "--repo", str(tmp_path),
        "implement", "--request", "anything",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "config" in err.lower()
    assert "max_code_iterations" in err


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


def test_bundle_export_and_import_via_cli(tmp_path: Path, capsys):
    """``dev-loop bundle export`` + ``import`` is the headless story for
    sharing a tuned setup between repos."""
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir(); repo_b.mkdir()
    cli.main(["--repo", str(repo_a), "init", "--starter"])
    capsys.readouterr()  # discard init output

    out_file = tmp_path / "bundle.json"
    rc = cli.main(["--repo", str(repo_a), "bundle", "export",
                   "--out", str(out_file), "--note", "preset-1"])
    assert rc == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["format"] == "dev-loop-bundle"
    assert data["note"] == "preset-1"
    assert any(s["name"] == "hello-dev-loop" for s in data["scenarios"])

    # Preview into the empty repo (no writes).
    capsys.readouterr()
    rc = cli.main(["--repo", str(repo_b), "bundle", "import", str(out_file)])
    assert rc == 0
    preview_out = capsys.readouterr().out
    assert "Preview" in preview_out
    assert "new" in preview_out
    # No writes yet.
    assert not (repo_b / ".dev-loop" / "config.yaml").exists()

    # Apply.
    rc = cli.main(["--repo", str(repo_b), "bundle", "import",
                   str(out_file), "--apply"])
    assert rc == 0
    assert (repo_b / ".dev-loop" / "config.yaml").exists()
    assert (repo_b / "scenarios" / "hello-dev-loop" / "task_request.md").exists()


def test_bundle_import_missing_file(tmp_path: Path, capsys):
    rc = cli.main(["--repo", str(tmp_path), "bundle", "import",
                   str(tmp_path / "nope.json")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err.lower()


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
