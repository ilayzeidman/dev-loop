"""Tests for ``dev-loop doctor`` — setup diagnostics.

The doctor is the user's first line of defence against silent misconfig
(no gitignore, missing scenario dir, default_provider that points at a
CLI that isn't installed, runs_dir not writable, ...). The contract we
pin here:

  * a freshly ``init``'d repo passes with ``ok`` everywhere
  * common breakage classes (bad provider, missing gitignore, broken
    config) surface as the right severity
  * JSON output mirrors the human-readable output and is exit-code
    consistent
  * ``--strict`` upgrades warnings to a non-zero exit
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import cli
from harness.doctor import doctor_exit_code, doctor_summary, run_doctor


def _labels(checks) -> list[str]:
    return [c.label for c in checks]


def _by_label(checks, label):
    matches = [c for c in checks if c.label == label]
    assert matches, f"no check with label={label!r} in {_labels(checks)}"
    return matches[0]


def test_fresh_init_clean_bill_of_health(tmp_path: Path):
    """`dev-loop init --starter` should leave a repo the doctor is happy with."""
    cli.main(["--repo", str(tmp_path), "init", "--starter"])
    # Pretend it's a git repo so the doctor's git-presence check is happy.
    (tmp_path / ".git").mkdir()

    checks = run_doctor(tmp_path)
    summary = doctor_summary(checks)
    assert summary["error"] == 0, _labels(checks)
    # Warnings allowed (provider CLIs may not be installed in CI) but
    # the things ``init`` controls should all pass.
    assert _by_label(checks, "config_file").level == "ok"
    assert _by_label(checks, "gitignore").level == "ok"
    assert _by_label(checks, "runs_dir").level == "ok"
    assert _by_label(checks, "scenarios_dir").level == "ok"
    assert _by_label(checks, "git_repo").level == "ok"


def test_doctor_warns_when_no_config(tmp_path: Path):
    """Empty repo: no config file, no scenarios, no gitignore — all warnings."""
    checks = run_doctor(tmp_path)
    summary = doctor_summary(checks)
    assert summary["error"] == 0
    assert _by_label(checks, "config_file").level == "warning"
    assert _by_label(checks, "scenarios_dir").level == "warning"
    # default provider is "replay" → ok even with nothing else
    assert _by_label(checks, "provider").level == "ok"


def test_doctor_flags_bad_config_as_error(tmp_path: Path):
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text(
        "policy:\n  max_code_iterations: 0\n"
    )
    checks = run_doctor(tmp_path)
    cf = _by_label(checks, "config_file")
    assert cf.level == "error"
    assert "max_code_iterations" in cf.message
    assert "config validate" in cf.hint
    assert doctor_exit_code(checks, strict=False) == 1


def test_doctor_warns_when_provider_cli_missing(tmp_path: Path, monkeypatch):
    """``default_provider: claude`` without the CLI on PATH must warn but not error."""
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text(
        "default_provider: claude\n"
    )
    # Make sure no claude/codex binaries leak from the host env.
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    checks = run_doctor(tmp_path)
    prov = _by_label(checks, "provider")
    assert prov.level == "warning"
    assert "claude" in prov.message
    assert prov.hint  # actionable
    # warnings alone don't fail the exit code...
    assert doctor_exit_code(checks, strict=False) == 0
    # ...unless --strict.
    assert doctor_exit_code(checks, strict=True) == 1


def test_doctor_warns_on_unknown_provider(tmp_path: Path):
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text("default_provider: vinyl\n")

    checks = run_doctor(tmp_path)
    prov = _by_label(checks, "provider")
    assert prov.level == "warning"
    assert "vinyl" in prov.message


def test_doctor_warns_on_missing_gitignore_runs_pattern(tmp_path: Path):
    cli.main(["--repo", str(tmp_path), "init"])
    # Overwrite the .gitignore so the runs path is missing.
    (tmp_path / ".gitignore").write_text("# nothing here\n")
    checks = run_doctor(tmp_path)
    gi = _by_label(checks, "gitignore")
    assert gi.level == "warning"
    assert ".dev-loop/runs" in gi.message


def test_doctor_warns_when_repo_has_no_git_dir(tmp_path: Path):
    cli.main(["--repo", str(tmp_path), "init"])
    # ``init`` doesn't create .git; we just want to assert the check fires.
    assert not (tmp_path / ".git").exists()
    checks = run_doctor(tmp_path)
    gr = _by_label(checks, "git_repo")
    assert gr.level == "warning"
    assert "git" in gr.message.lower()


def test_doctor_runs_ledger_reports_latest_run(tmp_path: Path):
    """When the ledger has runs the doctor should surface the most recent one."""
    cli.main(["--repo", str(tmp_path), "init"])
    runs_dir = tmp_path / ".dev-loop" / "runs"
    (runs_dir / "20260520-120000-x" / "iterations").mkdir(parents=True)
    (runs_dir / "20260520-120000-x" / "task_manifest.json").write_text(json.dumps({
        "task_id": "20260520-120000-x",
        "status": "completed",
        "final_status": "passed",
        "created_at_utc": "2026-05-20T12:00:00Z",
        "updated_at_utc": "2026-05-20T12:01:00Z",
    }))

    checks = run_doctor(tmp_path)
    ledger = _by_label(checks, "runs_ledger")
    assert ledger.level == "ok"
    assert "20260520-120000-x" in ledger.message
    assert "passed" in ledger.message


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_doctor_exits_zero_on_fresh_init(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init", "--starter"])
    (tmp_path / ".git").mkdir()
    capsys.readouterr()

    rc = cli.main(["--repo", str(tmp_path), "doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dev-loop doctor" in out
    assert "ok" in out
    assert "summary:" in out


def test_cli_doctor_json_includes_summary_and_exit_code(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()

    rc = cli.main(["--repo", str(tmp_path), "doctor", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["repo"] == str(tmp_path.resolve())
    assert isinstance(data["checks"], list)
    assert "summary" in data
    assert data["exit_code"] == rc


def test_cli_doctor_strict_flips_on_warnings(tmp_path: Path, capsys):
    """A fresh, un-init'd repo has warnings — they pass by default, fail under --strict."""
    rc = cli.main(["--repo", str(tmp_path), "doctor"])
    assert rc == 0
    capsys.readouterr()
    rc = cli.main(["--repo", str(tmp_path), "doctor", "--strict"])
    assert rc == 1


def test_cli_doctor_errors_when_config_broken(tmp_path: Path, capsys):
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text(
        "policy:\n  max_code_iterations: -1\n"
    )
    rc = cli.main(["--repo", str(tmp_path), "doctor"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "ERROR" in out or "error" in out.lower()
    assert "config validate" in out  # actionable hint


def test_cli_doctor_does_not_require_valid_config(tmp_path: Path, capsys):
    """The doctor is the diagnostic of last resort — it must *report* on
    broken configs rather than refuse to run because of them.

    (Contrast with ``implement`` / ``replay``, which short-circuit on
    config errors.)
    """
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text("policy:\n  max_code_iterations: 0\n")
    rc = cli.main(["--repo", str(tmp_path), "doctor"])
    # Non-zero (errors present) but it ran — we got output.
    assert rc == 1
    out = capsys.readouterr().out
    assert "summary:" in out
