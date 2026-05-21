"""Replay agent runner.

Loads canned outputs from a scenario directory. Lets the full loop be
exercised without an LLM.

Scenario layout (per design section 21):

    scenarios/<scenario-id>/
      task_request.md
      base_repo_ref.txt
      task_contract.json                # agent output for task-contract phase
      patch.diff                        # patch the "agent" applies
      implementation_result.json        # agent output for implementation phase
      e2e_result.json                   # passed/failed + details
      elastic_summary.json
      grafana_metrics.json
      pod_logs_excerpt.txt
      gpu_utilization.json
      failure_triage.json               # agent output for triage phase
      expected_triage.json              # optional, for replay tests
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..util import read_json, read_text
from .base import AgentPhase, AgentPhaseResult, AgentRunner


class ReplayAgentRunner(AgentRunner):
    provider_name = "replay"

    def __init__(self, scenario_dir: Path) -> None:
        self.scenario_dir = Path(scenario_dir)
        if not self.scenario_dir.exists():
            raise FileNotFoundError(f"scenario dir not found: {scenario_dir}")

    def profile(self) -> dict[str, Any]:
        return {
            "provider": "replay",
            "model": "replay-fixture",
            "scenario": str(self.scenario_dir),
        }

    def run_phase(
        self,
        phase: AgentPhase,
        *,
        workspace_path: Path,
        task_contract: dict[str, Any] | None,
        run_manifest: dict[str, Any] | None,
        input_bundle: dict[str, Any],
        output_schema_name: str,
        budget_seconds: int,
    ) -> AgentPhaseResult:
        if phase is AgentPhase.TASK_CONTRACT:
            out = self._load_task_contract(input_bundle)
            return AgentPhaseResult(output=out, raw_log="replay:task_contract")

        if phase is AgentPhase.IMPLEMENTATION:
            self._apply_scenario_patch(workspace_path)
            out = self._load_implementation_result()
            return AgentPhaseResult(
                output=out,
                raw_log="replay:implementation",
                workspace_after=workspace_path,
            )

        if phase is AgentPhase.FAILURE_TRIAGE:
            out = self._load_failure_triage()
            return AgentPhaseResult(output=out, raw_log="replay:failure_triage")

        raise ValueError(f"unknown phase: {phase}")

    # ------------------------------------------------------------------

    def _load_task_contract(self, input_bundle: dict[str, Any]) -> dict[str, Any]:
        f = self.scenario_dir / "task_contract.json"
        if f.exists():
            return read_json(f)
        # Synthesize a minimal contract from the original request.
        req = input_bundle.get("original_request", "")
        return {
            "type": "task_contract",
            "implementation_goal": req or "replay scenario task",
            "assumptions": [],
            "success_criteria": ["scenario-defined E2E passes"],
            "non_goals": [],
            "likely_components": [],
            "validation_plan": ["scenario-defined E2E"],
            "ambiguities": [],
            "can_start_without_human": True,
        }

    def _load_implementation_result(self) -> dict[str, Any]:
        f = self.scenario_dir / "implementation_result.json"
        if f.exists():
            return read_json(f)
        return {
            "type": "implementation_result",
            "summary": "replay scenario implementation",
            "hypothesis": "scenario-defined",
            "confidence": "medium",
            "expected_validation": [],
            "risk_notes": [],
            "claimed_changed_files": [],
        }

    def _load_failure_triage(self) -> dict[str, Any]:
        f = self.scenario_dir / "failure_triage.json"
        if f.exists():
            return read_json(f)
        return {
            "type": "failure_triage",
            "failure_class": "unknown",
            "confidence": "low",
            "next_action": "stop_inconclusive",
            "hypothesis": "replay scenario provided no triage",
            "expected_effect": "",
            "evidence_refs": [],
            "requested_diagnostics": [],
            "human_reason": "no triage in scenario",
        }

    def _apply_scenario_patch(self, workspace: Path) -> None:
        patch = self.scenario_dir / "patch.diff"
        if patch.exists() and patch.read_text(encoding="utf-8").strip():
            subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(patch)],
                cwd=workspace, check=True,
            )
            return
        # If the scenario carries a "files/" dir, copy it on top.
        files_dir = self.scenario_dir / "files"
        if files_dir.exists():
            for src in files_dir.rglob("*"):
                if src.is_file():
                    rel = src.relative_to(files_dir)
                    dst = workspace / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
