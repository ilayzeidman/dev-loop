"""End-to-end replay run."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from harness.agents import create_runner
from harness.capabilities import load_default_registry
from harness.orchestrator import Orchestrator, OrchestratorConfig
from harness.policy import LoopPolicy


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
