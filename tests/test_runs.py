"""Direct coverage of ``harness.runs``.

The CLI ``runs`` subcommands and the UI Analyze tab compare view both
import from this module, so its public surface is now load-bearing for
two shipping surfaces. These tests pin that contract without going
through either surface — failures here point straight at the shared
helpers rather than at downstream rendering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness import runs


def _write_run(
    runs_dir: Path,
    task_id: str,
    *,
    final_status: str = "passed",
    selected: int | None = 1,
    iterations: int = 1,
    goal: str = "fix gpu init timeout",
    scenario: str | None = "demo",
    created: str = "2026-05-21T10:00:00Z",
    updated: str = "2026-05-21T10:01:30Z",
    e2e_status: str = "passed",
    changed_files: list[str] | None = None,
    patch_hash_prefix: str = "hash",
    write_reports: bool = True,
    audit_entries: list[dict[str, Any]] | None = None,
) -> Path:
    """Fabricate a minimal run directory the readers can consume."""
    root = runs_dir / task_id
    (root / "iterations").mkdir(parents=True)
    tm: dict[str, Any] = {
        "task_id": task_id,
        "status": "completed",
        "final_status": final_status,
        "selected_iteration": selected,
        "stop_reason": "selected_passed" if final_status == "passed" else "exhausted",
        "created_at_utc": created,
        "updated_at_utc": updated,
        "task_contract": {
            "implementation_goal": goal,
            "scenario": scenario,
        },
    }
    (root / "task_manifest.json").write_text(json.dumps(tm, indent=2))
    files = changed_files if changed_files is not None else ["src/a.py"]
    for i in range(1, iterations + 1):
        d = root / "iterations" / f"iter-{i:03d}"
        (d / "validations" / "attempt-001").mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "task_id": task_id,
            "iteration": i,
            "code": {
                "patch_hash": f"{patch_hash_prefix}{i:03d}",
                "changed_files": files,
            },
            "agent_output": {"summary": f"iter {i} summary"},
            "final_e2e_status": e2e_status,
        }))
    if write_reports:
        (root / "final_review_report.md").write_text("# report\n")
        (root / "final_review_report.json").write_text("{}\n")
    if audit_entries:
        (root / "capability_audit.jsonl").write_text(
            "\n".join(json.dumps(e) for e in audit_entries) + "\n",
            encoding="utf-8",
        )
    return root


def test_run_duration_seconds_basic():
    tm = {
        "created_at_utc": "2026-05-21T10:00:00Z",
        "updated_at_utc": "2026-05-21T10:02:30Z",
    }
    assert runs.run_duration_seconds(tm) == 150


def test_run_duration_seconds_clamped_to_zero_on_inversion():
    """A clock skew that makes updated < created must not produce a
    negative duration — UI clients render this as a non-negative integer."""
    tm = {
        "created_at_utc": "2026-05-21T10:02:30Z",
        "updated_at_utc": "2026-05-21T10:00:00Z",
    }
    assert runs.run_duration_seconds(tm) == 0


def test_run_duration_seconds_returns_none_when_unparseable():
    assert runs.run_duration_seconds({}) is None
    assert runs.run_duration_seconds({"created_at_utc": "garbage"}) is None
    assert runs.run_duration_seconds({
        "created_at_utc": "2026-05-21T10:00:00Z",
        "updated_at_utc": "not-a-date",
    }) is None


def test_count_iterations_missing_dir(tmp_path: Path):
    assert runs.count_iterations(tmp_path / "nope") == 0


def test_count_iterations_ignores_files(tmp_path: Path):
    iters = tmp_path / "iterations"
    iters.mkdir()
    (iters / "iter-001").mkdir()
    (iters / "iter-002").mkdir()
    (iters / "stray.txt").write_text("noise")
    assert runs.count_iterations(tmp_path) == 2


def test_list_runs_empty_when_directory_missing(tmp_path: Path):
    assert runs.list_runs(tmp_path / "does-not-exist") == []


def test_list_runs_returns_newest_first(tmp_path: Path):
    _write_run(tmp_path, "20260101-000000-old", goal="old run")
    _write_run(tmp_path, "20260520-120000-new", goal="new run")
    listing = runs.list_runs(tmp_path)
    assert [r["task_id"] for r in listing] == [
        "20260520-120000-new", "20260101-000000-old",
    ]
    assert listing[0]["goal"] == "new run"
    assert listing[0]["iterations"] == 1
    assert listing[0]["duration_seconds"] == 90


def test_list_runs_skips_corrupt_and_unmanifested_dirs(tmp_path: Path):
    _write_run(tmp_path, "20260101-000000-good")
    bad = tmp_path / "20260102-000000-bad"
    bad.mkdir()
    (bad / "task_manifest.json").write_text("{not json")
    no_manifest = tmp_path / "20260103-000000-bare"
    no_manifest.mkdir()
    (tmp_path / "loose.txt").write_text("not a run dir")

    ids = [r["task_id"] for r in runs.list_runs(tmp_path)]
    assert ids == ["20260101-000000-good"]


def test_show_run_returns_none_for_unknown(tmp_path: Path):
    assert runs.show_run(tmp_path, "missing") is None


def test_show_run_returns_none_when_manifest_corrupt(tmp_path: Path):
    bad = tmp_path / "20260101-000000-bad"
    bad.mkdir()
    (bad / "task_manifest.json").write_text("{not json")
    assert runs.show_run(tmp_path, "20260101-000000-bad") is None


def test_show_run_populates_iterations_and_report_paths(tmp_path: Path):
    _write_run(
        tmp_path, "20260520-120000-x",
        iterations=2, selected=2,
        changed_files=["src/a.py", "src/b.py"],
    )
    detail = runs.show_run(tmp_path, "20260520-120000-x")
    assert detail is not None
    assert detail["task_id"] == "20260520-120000-x"
    assert detail["selected_iteration"] == 2
    assert detail["scenario"] == "demo"
    assert detail["stop_reason"] == "selected_passed"
    assert [it["iteration"] for it in detail["iterations"]] == [1, 2]
    assert detail["iterations"][0]["patch_hash"] == "hash001"
    assert detail["iterations"][0]["changed_files"] == ["src/a.py", "src/b.py"]
    assert detail["iterations"][0]["attempts"] == 1
    assert detail["report_md"] is not None
    assert detail["report_json"] is not None


def test_show_run_omits_reports_when_absent(tmp_path: Path):
    _write_run(tmp_path, "20260520-120000-y", write_reports=False)
    detail = runs.show_run(tmp_path, "20260520-120000-y")
    assert detail is not None
    assert detail["report_md"] is None
    assert detail["report_json"] is None


def test_show_run_tolerates_iteration_with_corrupt_manifest(tmp_path: Path):
    """One unreadable iteration manifest must not blank out the whole run."""
    _write_run(tmp_path, "20260520-120000-z", iterations=2)
    bad = tmp_path / "20260520-120000-z" / "iterations" / "iter-002" / "manifest.json"
    bad.write_text("{garbage")
    detail = runs.show_run(tmp_path, "20260520-120000-z")
    assert detail is not None
    iters = detail["iterations"]
    assert len(iters) == 2
    assert iters[1]["patch_hash"] is None
    assert iters[1]["changed_files"] == []


def test_audit_rollup_missing_file_returns_zeroed(tmp_path: Path):
    assert runs.audit_rollup(tmp_path) == {
        "total": 0, "by_status": {}, "by_capability": {},
    }


def test_audit_rollup_counts_by_status_and_capability(tmp_path: Path):
    (tmp_path / "capability_audit.jsonl").write_text(
        "\n".join([
            json.dumps({"capability": "shell.run", "status": "allowed"}),
            json.dumps({"capability": "fs.write", "status": "allowed"}),
            json.dumps({"capability": "shell.run", "status": "denied"}),
            "",  # blank line tolerated
            "{not json}",  # malformed line skipped
        ]) + "\n",
        encoding="utf-8",
    )
    roll = runs.audit_rollup(tmp_path)
    assert roll["total"] == 3
    assert roll["by_status"] == {"allowed": 2, "denied": 1}
    assert roll["by_capability"] == {"shell.run": 2, "fs.write": 1}


def test_audit_rollup_handles_missing_keys(tmp_path: Path):
    (tmp_path / "capability_audit.jsonl").write_text(
        json.dumps({}) + "\n",
        encoding="utf-8",
    )
    roll = runs.audit_rollup(tmp_path)
    assert roll == {"total": 1, "by_status": {"?": 1}, "by_capability": {"?": 1}}


def test_diff_deltas_both_missing():
    assert runs.diff_deltas(None, None) == {"both_present": False}
    assert runs.diff_deltas({"task_id": "x"}, None) == {"both_present": False}
    assert runs.diff_deltas(None, {"task_id": "y"}) == {"both_present": False}


def test_diff_deltas_identical_summaries():
    summary = {
        "goal": "g", "scenario": "s", "final_status": "passed",
        "duration_seconds": 90,
        "iterations": [
            {"final_e2e_status": "passed", "patch_hash": "h1",
             "changed_files": ["src/a.py"]},
        ],
        "audit": {"total": 4},
    }
    d = runs.diff_deltas(summary, summary)
    assert d["both_present"] is True
    assert d["same_goal"] is True
    assert d["same_scenario"] is True
    assert d["same_final_status"] is True
    assert d["iteration_count_delta"] == 0
    assert d["duration_seconds_delta"] == 0
    assert d["iteration_status_agreement"] == 1
    assert d["iteration_status_compared"] == 1
    assert d["first_diverging_iteration"] is None
    assert d["files_only_a"] == []
    assert d["files_only_b"] == []
    assert d["files_both"] == ["src/a.py"]
    assert d["audit_total_delta"] == 0


def test_diff_deltas_flags_first_divergence_and_files():
    a = {
        "goal": "g", "scenario": "s", "final_status": "passed",
        "duration_seconds": 60,
        "iterations": [
            {"final_e2e_status": "passed", "patch_hash": "h1",
             "changed_files": ["src/a.py"]},
            {"final_e2e_status": "passed", "patch_hash": "h2",
             "changed_files": ["src/a.py", "src/shared.py"]},
        ],
        "audit": {"total": 2},
    }
    b = {
        "goal": "g", "scenario": "s", "final_status": "failed_inconclusive",
        "duration_seconds": 200,
        "iterations": [
            {"final_e2e_status": "passed", "patch_hash": "h1",
             "changed_files": ["src/a.py"]},
            {"final_e2e_status": "failed", "patch_hash": "h2x",
             "changed_files": ["src/b.py", "src/shared.py"]},
            {"final_e2e_status": "failed", "patch_hash": "h3",
             "changed_files": ["src/b.py"]},
        ],
        "audit": {"total": 5},
    }
    d = runs.diff_deltas(a, b)
    assert d["both_present"] is True
    assert d["same_goal"] is True
    assert d["same_scenario"] is True
    assert d["same_final_status"] is False
    assert d["iteration_count_delta"] == 1
    assert d["duration_seconds_delta"] == 140
    assert d["iteration_status_compared"] == 2
    assert d["iteration_status_agreement"] == 1
    assert d["first_diverging_iteration"] == 2
    assert d["files_only_a"] == []
    assert d["files_only_b"] == ["src/b.py"]
    assert d["files_both"] == ["src/a.py", "src/shared.py"]
    assert d["audit_total_delta"] == 3


def test_diff_deltas_duration_delta_none_when_either_side_missing():
    a = {"goal": "g", "scenario": "s", "final_status": "passed",
         "duration_seconds": None,
         "iterations": [], "audit": {"total": 0}}
    b = {"goal": "g", "scenario": "s", "final_status": "passed",
         "duration_seconds": 30,
         "iterations": [], "audit": {"total": 0}}
    assert runs.diff_deltas(a, b)["duration_seconds_delta"] is None
    assert runs.diff_deltas(b, a)["duration_seconds_delta"] is None


def test_diff_runs_end_to_end(tmp_path: Path):
    _write_run(
        tmp_path, "20260101-000000-a",
        iterations=1, e2e_status="passed",
        audit_entries=[{"capability": "shell.run", "status": "allowed"}],
    )
    _write_run(
        tmp_path, "20260520-120000-b",
        iterations=2, e2e_status="failed",
        final_status="failed_inconclusive", selected=None,
        patch_hash_prefix="other",
        changed_files=["src/b.py"],
        audit_entries=[
            {"capability": "shell.run", "status": "allowed"},
            {"capability": "fs.write", "status": "denied"},
        ],
    )

    result = runs.diff_runs(tmp_path, "20260101-000000-a", "20260520-120000-b")
    assert result["a"]["task_id"] == "20260101-000000-a"
    assert result["b"]["task_id"] == "20260520-120000-b"
    assert result["a"]["audit"]["total"] == 1
    assert result["b"]["audit"]["total"] == 2
    deltas = result["deltas"]
    assert deltas["both_present"] is True
    assert deltas["iteration_count_delta"] == 1
    assert deltas["audit_total_delta"] == 1
    assert deltas["same_final_status"] is False
    assert deltas["first_diverging_iteration"] == 1
    assert deltas["files_only_a"] == ["src/a.py"]
    assert deltas["files_only_b"] == ["src/b.py"]


def test_diff_runs_missing_side(tmp_path: Path):
    _write_run(tmp_path, "20260101-000000-a")
    result = runs.diff_runs(tmp_path, "20260101-000000-a", "does-not-exist")
    assert result["a"]["task_id"] == "20260101-000000-a"
    assert result["b"] is None
    assert result["deltas"] == {"both_present": False}
