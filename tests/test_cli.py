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


def _fake_run(
    runs_dir: Path,
    task_id: str,
    *,
    final_status: str = "passed",
    selected: int | None = 1,
    iterations: int = 1,
    goal: str = "fix gpu init timeout",
    created: str = "2026-05-21T10:00:00Z",
    updated: str = "2026-05-21T10:01:30Z",
    e2e_status: str = "passed",
) -> Path:
    """Fabricate a minimal run directory the CLI readers can consume.

    This avoids running the full orchestrator just to exercise the
    ``runs ls`` / ``runs show`` output paths.
    """
    root = runs_dir / task_id
    (root / "iterations").mkdir(parents=True)
    (root / "task_manifest.json").write_text(json.dumps({
        "task_id": task_id,
        "status": "completed",
        "final_status": final_status,
        "selected_iteration": selected,
        "created_at_utc": created,
        "updated_at_utc": updated,
        "task_contract": {"implementation_goal": goal},
    }, indent=2))
    for i in range(1, iterations + 1):
        d = root / "iterations" / f"iter-{i:03d}"
        (d / "validations" / "attempt-001").mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "task_id": task_id,
            "iteration": i,
            "code": {"patch_hash": f"hash{i:03d}abc", "changed_files": ["src/a.py"]},
            "agent_output": {"summary": f"iter {i} did the thing"},
            "attempts": [1],
            "final_e2e_status": e2e_status,
        }))
    (root / "final_review_report.md").write_text("# report\n")
    (root / "final_review_report.json").write_text("{}\n")
    return root


def test_runs_ls_lists_newest_first(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-old", goal="old run")
    _fake_run(runs_dir, "20260520-120000-new", goal="new run")

    rc = cli.main(["--repo", str(tmp_path), "runs", "ls"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TASK_ID" in out and "STATUS" in out
    # newest-first ordering
    assert out.index("20260520-120000-new") < out.index("20260101-000000-old")
    assert "passed" in out


def test_runs_ls_empty_directory(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main(["--repo", str(tmp_path), "runs", "ls"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "no runs" in out


def test_runs_ls_status_filter_and_json(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-a", final_status="passed")
    _fake_run(
        runs_dir, "20260102-000000-b",
        final_status="failed_inconclusive", selected=None,
        e2e_status="failed",
    )

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "ls",
        "--status", "passed", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["task_id"] == "20260101-000000-a"


def test_runs_ls_tolerates_corrupt_manifest(tmp_path: Path, capsys):
    """A broken run dir must not blow up the whole listing."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-good")
    bad = runs_dir / "20260102-000000-bad"
    bad.mkdir(parents=True)
    (bad / "task_manifest.json").write_text("not valid json {")

    rc = cli.main(["--repo", str(tmp_path), "runs", "ls"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260101-000000-good" in out
    assert "20260102-000000-bad" not in out


def test_runs_ls_pagination_limits_visible_rows_and_hints_next_page(
    tmp_path: Path, capsys,
):
    """`--limit` + `--offset` page through the ledger newest-first, and
    the human-readable output points at the next-page invocation so a
    user can flip through a thousand-run ledger without re-reading docs."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    for i in range(5):
        _fake_run(
            runs_dir, f"2026010{i + 1}-000000-r{i}",
            goal=f"run {i}",
            created=f"2026-01-0{i + 1}T00:00:00Z",
            updated=f"2026-01-0{i + 1}T00:01:00Z",
        )

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "ls", "--limit", "2",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260105-000000-r4" in out
    assert "20260104-000000-r3" in out
    assert "20260103-000000-r2" not in out
    assert "showing 1-2 of 5" in out
    assert "--offset 2" in out
    assert "--limit 2" in out

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "ls",
        "--limit", "2", "--offset", "2",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260103-000000-r2" in out
    assert "20260102-000000-r1" in out
    assert "20260105-000000-r4" not in out
    assert "showing 3-4 of 5" in out


def test_runs_ls_json_includes_pagination_block(tmp_path: Path, capsys):
    """`--json` always includes a ``pagination`` block — scripts pipelining
    `runs ls --json` rely on ``has_more`` and ``matched`` to drive
    follow-up pages without reparsing the directory."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    for i in range(3):
        _fake_run(
            runs_dir, f"2026010{i + 1}-000000-r{i}",
            created=f"2026-01-0{i + 1}T00:00:00Z",
            updated=f"2026-01-0{i + 1}T00:01:00Z",
        )

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "ls",
        "--limit", "2", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    page = payload["pagination"]
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert page["returned"] == 2
    assert page["matched"] == 3
    assert page["has_more"] is True
    assert [r["task_id"] for r in payload["runs"]] == [
        "20260103-000000-r2", "20260102-000000-r1",
    ]


def test_runs_ls_offset_past_end_explains_to_user(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-a")
    rc = cli.main([
        "--repo", str(tmp_path), "runs", "ls", "--offset", "5",
    ])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "offset" in out and "5" in out


def test_runs_show_summarizes_one_run(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260520-120000-x", iterations=2, selected=2)

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "show", "20260520-120000-x",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260520-120000-x" in out
    assert "passed" in out
    assert "iter-001" in out and "iter-002" in out
    # selected iteration is marked
    assert "* iter-002" in out
    assert "report (md)" in out


def test_runs_show_last_alias(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-old")
    _fake_run(runs_dir, "20260520-120000-new")

    rc = cli.main(["--repo", str(tmp_path), "runs", "show", "last"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260520-120000-new" in out
    assert "20260101-000000-old" not in out


def test_runs_show_missing_run_clean_error(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main(["--repo", str(tmp_path), "runs", "show", "does-not-exist"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()
    assert "runs ls" in err


def test_runs_show_last_with_no_runs_errors_cleanly(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main(["--repo", str(tmp_path), "runs", "show", "last"])
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "no runs" in err


def test_runs_show_json_includes_iterations(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260520-120000-x", iterations=2, selected=2)

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "show", "20260520-120000-x", "--json",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["task_id"] == "20260520-120000-x"
    assert data["selected_iteration"] == 2
    assert len(data["iterations"]) == 2
    assert data["iterations"][0]["iteration"] == 1


def test_runs_diff_text_summary(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(
        runs_dir, "20260101-000000-a",
        iterations=1, selected=1, final_status="passed", e2e_status="passed",
        updated="2026-05-21T10:01:00Z",
    )
    _fake_run(
        runs_dir, "20260520-120000-b",
        iterations=2, selected=2, final_status="failed_inconclusive",
        e2e_status="failed",
        updated="2026-05-21T10:03:30Z",
    )

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "diff",
        "20260101-000000-a", "20260520-120000-b",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "A  20260101-000000-a" in out
    assert "B  20260520-120000-b" in out
    assert "iteration_count_delta:      +1" in out
    assert "same_final_status:          no" in out
    assert "first_diverging_iteration:  1" in out


def test_runs_diff_json_payload(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-a", iterations=1)
    _fake_run(runs_dir, "20260520-120000-b", iterations=2)

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "diff",
        "20260101-000000-a", "20260520-120000-b", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["a"]["task_id"] == "20260101-000000-a"
    assert payload["b"]["task_id"] == "20260520-120000-b"
    assert payload["deltas"]["both_present"] is True
    assert payload["deltas"]["iteration_count_delta"] == 1
    assert payload["a"]["audit"] == {
        "total": 0, "by_status": {}, "by_capability": {},
    }


def test_runs_diff_resolves_last_aliases(tmp_path: Path, capsys):
    """``last`` and ``last-N`` map to newest/N-th newest by sort order."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-old", iterations=1)
    _fake_run(runs_dir, "20260520-120000-new", iterations=3)

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "diff", "last-1", "last", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["a"]["task_id"] == "20260101-000000-old"
    assert payload["b"]["task_id"] == "20260520-120000-new"
    assert payload["deltas"]["iteration_count_delta"] == 2


def test_runs_diff_missing_run_clean_error(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260101-000000-a")

    rc = cli.main([
        "--repo", str(tmp_path), "runs", "diff",
        "20260101-000000-a", "does-not-exist",
    ])
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "not found" in err


def test_runs_diff_last_alias_with_no_runs_errors(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main([
        "--repo", str(tmp_path), "runs", "diff", "last-1", "last",
    ])
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "cannot resolve" in err


def _seed_ai_call_for_iter(
    runs_dir: Path, task_id: str, iteration: int, ordinal: int, phase: str,
    *, provider: str, returncode: int | None = 0, synthesized: bool = False,
) -> None:
    d = (runs_dir / task_id / "iterations" / f"iter-{iteration:03d}"
         / "ai_calls" / f"{ordinal:03d}_{phase}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "input.json").write_text("{}")
    (d / "output.json").write_text(json.dumps({"type": phase}))
    meta: dict = {"provider": provider}
    if returncode is not None:
        meta["returncode"] = returncode
    if synthesized:
        meta["synthesized"] = True
    (d / "metadata.json").write_text(json.dumps(meta))


def test_runs_show_renders_ai_calls_rollup(tmp_path: Path, capsys):
    """``runs show`` surfaces the same provider/returncode pills the
    Analyze tab shows so terminal users get CLI symmetry with the UI."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260520-120000-x", iterations=1, selected=1)
    _seed_ai_call_for_iter(
        runs_dir, "20260520-120000-x", 1, 1, "implementation",
        provider="claude", returncode=0,
    )
    _seed_ai_call_for_iter(
        runs_dir, "20260520-120000-x", 1, 11, "triage_attempt_1",
        provider="codex", returncode=2,
    )
    rc = cli.main([
        "--repo", str(tmp_path), "runs", "show", "20260520-120000-x",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ai_calls:" in out
    assert "2 calls" in out
    assert "claude=1" in out and "codex=1" in out
    assert "nonzero_rc=1" in out


def test_runs_show_omits_ai_calls_line_for_legacy_runs(tmp_path: Path, capsys):
    """Runs from before ai_calls metadata was recorded must still render
    cleanly — no empty ``ai_calls:`` line dangling under each iteration."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260520-120000-legacy", iterations=1, selected=1)
    rc = cli.main([
        "--repo", str(tmp_path), "runs", "show", "20260520-120000-legacy",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ai_calls:" not in out


def test_runs_show_json_embeds_ai_calls(tmp_path: Path, capsys):
    """``--json`` includes the per-iteration ai_calls list + rollup so
    scripts can consume the same data the Analyze tab does."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    runs_dir = tmp_path / ".dev-loop" / "runs"
    _fake_run(runs_dir, "20260520-120000-y", iterations=1, selected=1)
    _seed_ai_call_for_iter(
        runs_dir, "20260520-120000-y", 1, 1, "implementation",
        provider="claude", returncode=0,
    )
    _seed_ai_call_for_iter(
        runs_dir, "20260520-120000-y", 1, 12, "triage_attempt_1_harness_fallback",
        provider="harness", returncode=None, synthesized=True,
    )
    rc = cli.main([
        "--repo", str(tmp_path), "runs", "show", "20260520-120000-y", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    it = payload["iterations"][0]
    assert [c["name"] for c in it["ai_calls"]] == [
        "001_implementation", "012_triage_attempt_1_harness_fallback",
    ]
    rollup = it["ai_call_rollup"]
    assert rollup["total"] == 2
    assert rollup["by_provider"] == {"claude": 1, "harness": 1}
    assert rollup["synthesized"] == 1


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


# ---------------------------------------------------------------------------
# scenarios ls / show / validate


def _write_scenario(
    scenarios_dir: Path,
    name: str,
    *,
    goal: str = "demo goal",
    status: str = "passed",
    suite: str = "demo-e2e",
    duration: int = 7,
    extras_contract: dict | None = None,
) -> Path:
    d = scenarios_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "task_request.md").write_text(f"# {name}\nDo a thing.\n")
    contract = {
        "type": "task_contract",
        "implementation_goal": goal,
        "assumptions": [],
        "success_criteria": ["E2E reaches PLAYING"],
        "non_goals": [],
        "likely_components": [],
        "validation_plan": [],
        "ambiguities": [],
        "can_start_without_human": True,
    }
    if extras_contract:
        contract.update(extras_contract)
    (d / "task_contract.json").write_text(json.dumps(contract))
    (d / "implementation_result.json").write_text(json.dumps({
        "type": "implementation_result",
        "summary": "did it",
        "hypothesis": "h",
        "confidence": "medium",
        "expected_validation": [],
        "risk_notes": [],
        "claimed_changed_files": [],
    }))
    (d / "e2e_result.json").write_text(json.dumps({
        "status": status, "test_suite": suite, "duration_seconds": duration,
    }))
    return d


def test_scenarios_ls_lists_alphabetical(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "alpha", goal="alpha goal")
    _write_scenario(sc, "beta", goal="beta goal")

    rc = cli.main(["--repo", str(tmp_path), "scenarios", "ls"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NAME" in out and "LINT" in out
    assert out.index("alpha") < out.index("beta")
    assert "ok" in out


def test_scenarios_ls_empty_directory(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main(["--repo", str(tmp_path), "scenarios", "ls"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "no scenarios" in out


def test_scenarios_ls_json_payload(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "alpha")
    _write_scenario(sc, "broken", goal="")  # missing goal -> lint error

    rc = cli.main(["--repo", str(tmp_path), "scenarios", "ls", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = [s["name"] for s in payload["scenarios"]]
    assert names == ["alpha", "broken"]
    by_name = {s["name"]: s for s in payload["scenarios"]}
    assert by_name["alpha"]["valid"] is True
    assert by_name["broken"]["valid"] is False
    assert by_name["broken"]["n_errors"] >= 1


def test_scenarios_show_text_summary(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "demo", goal="implement the demo")

    rc = cli.main(["--repo", str(tmp_path), "scenarios", "show", "demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "name:" in out and "demo" in out
    assert "implement the demo" in out
    assert "lint:          ok" in out
    assert "task_request.md" in out


def test_scenarios_show_missing_clean_error(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main(["--repo", str(tmp_path), "scenarios", "show", "nope"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()
    assert "scenarios ls" in err


def test_scenarios_show_json_includes_issues(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "broken", goal="")  # goal missing

    rc = cli.main([
        "--repo", str(tmp_path), "scenarios", "show", "broken", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "broken"
    fields = [i["field"] for i in payload["issues"]]
    assert "task_contract.implementation_goal" in fields


def test_scenarios_validate_clean_returns_zero(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "alpha")
    _write_scenario(sc, "beta")

    rc = cli.main(["--repo", str(tmp_path), "scenarios", "validate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha: ok" in out
    assert "beta: ok" in out
    assert "2 scenarios, 2 clean" in out


def test_scenarios_validate_flags_errors(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "good")
    _write_scenario(sc, "broken", goal="")  # goal missing -> error

    rc = cli.main(["--repo", str(tmp_path), "scenarios", "validate"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "good: ok" in out
    assert "broken" in out
    assert "implementation_goal" in out


def test_scenarios_validate_named_scenario_only(tmp_path: Path, capsys):
    """``validate <name>`` should not look at sibling scenarios."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "good")
    _write_scenario(sc, "broken", goal="")

    rc = cli.main(["--repo", str(tmp_path), "scenarios", "validate", "good"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "good: ok" in out
    assert "broken" not in out


def test_scenarios_validate_missing_named_scenario_exits_two(
    tmp_path: Path, capsys,
):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    rc = cli.main([
        "--repo", str(tmp_path), "scenarios", "validate", "ghost",
    ])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "not found" in err


def test_scenarios_validate_json_payload(tmp_path: Path, capsys):
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    _write_scenario(sc, "good")
    _write_scenario(sc, "broken", goal="")

    rc = cli.main([
        "--repo", str(tmp_path), "scenarios", "validate", "--json",
    ])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["scenarios"] == 2
    assert payload["totals"]["clean"] == 1
    assert payload["totals"]["errors"] >= 1
    by_name = {s["name"]: s for s in payload["scenarios"]}
    assert by_name["good"]["valid"] is True
    assert by_name["broken"]["valid"] is False


def test_scenarios_validate_strict_promotes_warnings(tmp_path: Path, capsys):
    """A failed-status scenario with no first_error is a warning; --strict
    must convert that into a non-zero exit so CI can gate on it."""
    cli.main(["--repo", str(tmp_path), "init"])
    capsys.readouterr()
    sc = tmp_path / "scenarios"
    # Failed status with empty first_error -> warning only.
    d = _write_scenario(sc, "warn-only", status="failed")

    rc = cli.main(["--repo", str(tmp_path), "scenarios", "validate"])
    assert rc == 0  # warnings alone don't fail without --strict
    capsys.readouterr()

    rc = cli.main([
        "--repo", str(tmp_path), "scenarios", "validate", "--strict",
    ])
    assert rc == 1
    assert d.exists()


# capabilities ls/show -------------------------------------------------


def test_capabilities_ls_renders_table_grouped_by_category(
    tmp_path: Path, capsys,
):
    """`capabilities ls` is the CLI mirror of Build > Capabilities. The
    table must show every built-in capability and group by category so a
    user scanning for "what can the agent request" sees the
    ``real_dev_agent_requestable`` block contiguous."""
    rc = cli.main(["--repo", str(tmp_path), "capabilities", "ls"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NAME" in out and "CATEGORY" in out and "AGENT" in out
    assert "local_build" in out
    assert "trigger_dev_jenkins_build" in out
    assert "query_elastic_for_current_run" in out
    lines = out.splitlines()
    cat_idx = {
        cat: next(i for i, ln in enumerate(lines) if cat in ln)
        for cat in ("local_only", "real_dev_internal", "real_dev_agent_requestable")
    }
    sorted_cats = sorted(cat_idx, key=cat_idx.__getitem__)
    assert sorted_cats == sorted(cat_idx)


def test_capabilities_ls_filters_agent_requestable(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path),
        "capabilities", "ls", "--agent-requestable", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = [c["name"] for c in payload["capabilities"]]
    assert names
    assert "trigger_dev_jenkins_build" not in names
    assert "query_elastic_for_current_run" in names
    assert all(c["agent_requestable"] for c in payload["capabilities"])


def test_capabilities_ls_filters_by_category(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path),
        "capabilities", "ls", "--category", "local_only", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    cats = {c["category"] for c in payload["capabilities"]}
    assert cats == {"local_only"}


def test_capabilities_show_text_includes_forced_params(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path),
        "capabilities", "show", "trigger_dev_jenkins_build",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "name:" in out and "trigger_dev_jenkins_build" in out
    assert "category:" in out and "real_dev_internal" in out
    assert "agent_requestable: no" in out
    assert "forced_params:" in out
    assert "environment" in out and "dev" in out


def test_capabilities_show_json_payload(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path),
        "capabilities", "show", "query_elastic_for_current_run", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "query_elastic_for_current_run"
    assert payload["agent_requestable"] is True
    assert payload["uses_run_manifest"] is True
    assert payload["has_impl"] is True


def test_capabilities_show_missing_clean_error(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path), "capabilities", "show", "nope",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()
    assert "capabilities ls" in err


def test_capabilities_does_not_require_repo_config(tmp_path: Path):
    """`capabilities ls/show` introspects the global registry — it must
    not require a per-repo `.dev-loop/config.yaml` to exist (this is the
    one CLI surface that's repo-independent, since the registry ships
    with the package)."""
    assert not (tmp_path / ".dev-loop").exists()
    rc = cli.main(["--repo", str(tmp_path), "capabilities", "ls"])
    assert rc == 0


# playbooks ls/show ----------------------------------------------------


def test_playbooks_ls_renders_table_with_builtin_rows(
    tmp_path: Path, capsys,
):
    """`playbooks ls` mirrors the Build > Playbooks UI tab. The table must
    list every built-in playbook and tag the source so a user can tell at
    a glance whether their repo override took effect."""
    rc = cli.main(["--repo", str(tmp_path), "playbooks", "ls"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NAME" in out and "SOURCE" in out and "PHASES" in out
    assert "implement_feature.v1.md" in out
    assert "gpu_e2e_failure_triage.v1.md" in out
    assert "built-in" in out
    # The implement_feature playbook is bound to two agent phases — surface that.
    impl_line = next(
        ln for ln in out.splitlines() if ln.startswith("implement_feature.v1.md")
    )
    assert "implementation" in impl_line
    assert "task_contract" in impl_line


def test_playbooks_ls_json_payload_has_expected_fields(
    tmp_path: Path, capsys,
):
    rc = cli.main(["--repo", str(tmp_path), "playbooks", "ls", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "playbooks" in payload
    rows = payload["playbooks"]
    assert rows
    names = {r["name"] for r in rows}
    assert "implement_feature.v1.md" in names
    impl = next(r for r in rows if r["name"] == "implement_feature.v1.md")
    expected_keys = {
        "name", "source", "overridden", "has_builtin", "path",
        "size_bytes", "line_count", "agent_phases",
    }
    assert expected_keys.issubset(impl.keys())
    assert impl["source"] == "built-in"
    assert impl["overridden"] is False
    assert impl["has_builtin"] is True
    assert set(impl["agent_phases"]) == {"implementation", "task_contract"}


def test_playbooks_ls_detects_repo_override(tmp_path: Path, capsys):
    """A file at $REPO/.dev-loop/playbooks/<name>.md must flip the source
    column to ``repo-override`` so the user can confirm their edit landed."""
    pb_dir = tmp_path / ".dev-loop" / "playbooks"
    pb_dir.mkdir(parents=True)
    (pb_dir / "implement_feature.v1.md").write_text("# override\n")

    rc = cli.main([
        "--repo", str(tmp_path),
        "playbooks", "ls", "--overridden-only", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["playbooks"]) == 1
    row = payload["playbooks"][0]
    assert row["name"] == "implement_feature.v1.md"
    assert row["source"] == "repo-override"
    assert row["overridden"] is True
    assert row["has_builtin"] is True


def test_playbooks_show_text_emits_body_and_metadata(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path),
        "playbooks", "show", "implement_feature.v1.md",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "name:        implement_feature.v1.md" in out
    assert "source:      built-in" in out
    assert "agent phases: implementation, task_contract" in out
    assert "--- begin playbook ---" in out
    assert "--- end playbook ---" in out
    assert "implementation_goal" in out  # body content


def test_playbooks_show_metadata_only_omits_body(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path),
        "playbooks", "show", "implement_feature.v1.md", "--metadata-only",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "name:" in out
    assert "--- begin playbook ---" not in out
    assert "implementation_goal" not in out


def test_playbooks_show_json_includes_text(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path),
        "playbooks", "show", "implement_feature.v1.md", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "implement_feature.v1.md"
    assert payload["source"] == "built-in"
    assert "# Playbook: implement_feature" in payload["text"]


def test_playbooks_show_missing_clean_error(tmp_path: Path, capsys):
    rc = cli.main([
        "--repo", str(tmp_path), "playbooks", "show", "nope.md",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()
    assert "playbooks ls" in err


def test_playbooks_show_prefers_repo_override(tmp_path: Path, capsys):
    pb_dir = tmp_path / ".dev-loop" / "playbooks"
    pb_dir.mkdir(parents=True)
    (pb_dir / "implement_feature.v1.md").write_text("# repo override body\n")

    rc = cli.main([
        "--repo", str(tmp_path),
        "playbooks", "show", "implement_feature.v1.md", "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "repo-override"
    assert payload["text"] == "# repo override body\n"


def test_playbooks_does_not_require_repo_config(tmp_path: Path):
    """Like `capabilities`, `playbooks ls/show` must work in a repo with no
    `.dev-loop/config.yaml` — playbooks ship with the package."""
    assert not (tmp_path / ".dev-loop").exists()
    rc = cli.main(["--repo", str(tmp_path), "playbooks", "ls"])
    assert rc == 0
