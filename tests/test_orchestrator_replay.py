"""End-to-end replay run."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from harness.agents import create_runner
from harness.capabilities import load_default_registry
from harness.orchestrator import Orchestrator, OrchestratorConfig
from harness.policy import LoopPolicy
from harness.util import read_json


SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "gpu-init-timeout-001"


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n")
    (repo / "src").mkdir()
    (repo / "src" / "encoder").mkdir()
    (repo / "src" / "encoder" / "__init__.py").write_text("")
    (repo / "src" / "encoder" / "init.py").write_text("# placeholder\n")
    (repo / "src" / "encoder" / "device.py").write_text("# placeholder\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)


def test_replay_run_passes(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    runs_dir = tmp_path / "runs"
    sandbox_dir = tmp_path / "sb"
    clean_dir = tmp_path / "clean"
    runner = create_runner("replay", replay_scenario=SCENARIO)
    registry = load_default_registry()
    cfg = OrchestratorConfig(
        repo_root=repo,
        runs_dir=runs_dir,
        sandbox_dir=sandbox_dir,
        clean_workspace_dir=clean_dir,
        request="fix gpu init timeout",
        provider="replay",
        replay_scenario=SCENARIO,
        policy=LoopPolicy(max_code_iterations=2),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)
    result = orch.run()

    assert result.final_status == "passed", result.final_status
    assert result.selected_iteration == 1
    assert (result.ledger_dir / "task_manifest.json").exists()
    assert (result.ledger_dir / "final_review_report.md").exists()
    assert (result.ledger_dir / "iterations" / "iter-001" / "patch.diff").exists()
    # capability audit was recorded
    assert (result.ledger_dir / "capability_audit.jsonl").exists()


def _make_failing_scenario(scenario_dir: Path) -> None:
    """Build a scenario where E2E fails and triage returns stop_inconclusive."""
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "task_request.md").write_text("fail e2e please\n", encoding="utf-8")
    (scenario_dir / "task_contract.json").write_text(json.dumps({
        "type": "task_contract",
        "implementation_goal": "exercise the failure path",
        "assumptions": [],
        "success_criteria": ["scenario E2E reports failed"],
        "non_goals": [],
        "likely_components": ["src/encoder/init.py"],
        "validation_plan": ["replay e2e"],
        "ambiguities": [],
        "can_start_without_human": True,
    }, indent=2), encoding="utf-8")
    (scenario_dir / "implementation_result.json").write_text(json.dumps({
        "type": "implementation_result",
        "summary": "tweak something",
        "hypothesis": "nope",
        "confidence": "medium",
        "expected_validation": [],
        "risk_notes": [],
        "claimed_changed_files": ["src/encoder/init.py"],
    }, indent=2), encoding="utf-8")
    files_dir = scenario_dir / "files" / "src" / "encoder"
    files_dir.mkdir(parents=True)
    (files_dir / "init.py").write_text("# scenario modified\n", encoding="utf-8")
    (scenario_dir / "e2e_result.json").write_text(json.dumps({
        "status": "failed",
        "test_suite": "gpu-streaming-e2e",
        "started_at_utc": "2026-05-21T10:00:00Z",
        "finished_at_utc": "2026-05-21T10:00:42Z",
        "device_id": "GPU-0",
        "first_error": "device never reached PLAYING",
        "failed_test": "gpu_streaming::happy_path",
    }, indent=2), encoding="utf-8")
    (scenario_dir / "elastic_summary.json").write_text(json.dumps({
        "errors": [{"ts": "2026-05-21T10:00:05Z", "msg": "encoder timeout"}],
        "warnings": [],
    }, indent=2), encoding="utf-8")
    (scenario_dir / "grafana_metrics.json").write_text(
        '{"metrics": {"gpu_util_pct": [0, 0, 0]}}\n', encoding="utf-8")
    (scenario_dir / "gpu_utilization.json").write_text(
        '{"gpu_util_pct": [0, 0, 0]}\n', encoding="utf-8")
    (scenario_dir / "pod_logs_excerpt.txt").write_text(
        "encoder: waiting for device\nencoder: timeout\n", encoding="utf-8")
    (scenario_dir / "failure_triage.json").write_text(json.dumps({
        "type": "failure_triage",
        "failure_class": "code_suspected",
        "confidence": "low",
        "next_action": "stop_inconclusive",
        "hypothesis": "device never reaches ready",
        "expected_effect": "",
        "evidence_refs": [],
        "requested_diagnostics": [],
        "human_reason": "scenario triage opted to stop",
    }, indent=2), encoding="utf-8")


def test_replay_run_failed_e2e_invokes_triage_and_writes_dossier(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    scenario = tmp_path / "failing_scenario"
    _make_failing_scenario(scenario)
    runs_dir = tmp_path / "runs"
    sandbox_dir = tmp_path / "sb"
    clean_dir = tmp_path / "clean"
    runner = create_runner("replay", replay_scenario=scenario)
    registry = load_default_registry()
    cfg = OrchestratorConfig(
        repo_root=repo,
        runs_dir=runs_dir,
        sandbox_dir=sandbox_dir,
        clean_workspace_dir=clean_dir,
        request="exercise failure path",
        provider="replay",
        replay_scenario=scenario,
        policy=LoopPolicy(max_code_iterations=2),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)
    result = orch.run()

    assert result.final_status == "failed_inconclusive", result.final_status
    assert result.selected_iteration is None
    # The triage AI call was actually recorded.
    triage_dir = (
        result.ledger_dir
        / "iterations" / "iter-001" / "ai_calls"
    )
    triage_subdirs = [d for d in triage_dir.iterdir() if "triage" in d.name]
    assert triage_subdirs, list(triage_dir.iterdir())
    # The dossier and outcome both exist.
    attempt = (
        result.ledger_dir / "iterations" / "iter-001"
        / "validations" / "attempt-001"
    )
    outcome = read_json(attempt / "outcome.json")
    assert outcome["outcome"] == "failed_e2e"
    assert outcome["triage"]["next_action"] == "stop_inconclusive"
    assert outcome["dossier"] is not None
    # Diagnostics dir was populated.
    assert (attempt / "diagnostics" / "elastic_summary.json").exists()
    # Final review report was written and is schema-valid.
    report = read_json(result.ledger_dir / "final_review_report.json")
    assert report["final_status"] == "failed_inconclusive"
    assert "schema_validation_error" not in report


def test_replay_run_same_failure_twice_hits_stop_condition(tmp_path: Path):
    """When the same E2E failure repeats, the loop should stop with
    failed_stop_condition rather than falling through to inconclusive."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    scenario = tmp_path / "failing_scenario"
    _make_failing_scenario(scenario)
    # Override triage to keep modifying code so the loop runs twice and
    # hits the same-failure stop condition.
    (scenario / "failure_triage.json").write_text(json.dumps({
        "type": "failure_triage",
        "failure_class": "code_suspected",
        "confidence": "high",
        "next_action": "modify_code",
        "hypothesis": "try again",
        "expected_effect": "fix it",
        "evidence_refs": [],
        "requested_diagnostics": [],
        "human_reason": None,
    }, indent=2), encoding="utf-8")
    runner = create_runner("replay", replay_scenario=scenario)
    registry = load_default_registry()
    cfg = OrchestratorConfig(
        repo_root=repo,
        runs_dir=tmp_path / "runs",
        sandbox_dir=tmp_path / "sb",
        clean_workspace_dir=tmp_path / "clean",
        request="same failure twice",
        provider="replay",
        replay_scenario=scenario,
        policy=LoopPolicy(max_code_iterations=4),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)
    result = orch.run()
    assert result.final_status == "failed_stop_condition", result.final_status
    tm = read_json(result.ledger_dir / "task_manifest.json")
    assert tm.get("stop_reason") == "same_failure_fingerprint_after_2_code_iterations"
