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


def test_replay_run_records_iteration_manifest_when_impl_invalid(tmp_path: Path):
    """When the agent's implementation_result fails schema validation,
    the iteration manifest and AI call record must still be written so
    the iteration is auditable (design §8, §20)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    scenario = tmp_path / "bad_impl_scenario"
    _make_failing_scenario(scenario)
    # Override implementation_result with something that fails schema
    # validation (missing required fields).
    (scenario / "implementation_result.json").write_text(json.dumps({
        "type": "implementation_result",
        "summary": "",  # minLength: 1 violation
    }, indent=2), encoding="utf-8")
    runner = create_runner("replay", replay_scenario=scenario)
    registry = load_default_registry()
    cfg = OrchestratorConfig(
        repo_root=repo,
        runs_dir=tmp_path / "runs",
        sandbox_dir=tmp_path / "sb",
        clean_workspace_dir=tmp_path / "clean",
        request="exercise invalid impl path",
        provider="replay",
        replay_scenario=scenario,
        policy=LoopPolicy(max_code_iterations=1),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)
    result = orch.run()
    # Iteration manifest exists.
    iter_manifest_path = (
        result.ledger_dir / "iterations" / "iter-001" / "manifest.json"
    )
    assert iter_manifest_path.exists(), list(result.ledger_dir.rglob("*"))
    m = read_json(iter_manifest_path)
    assert m["iteration"] == 1
    assert "invalid implementation_result" in (m.get("error") or "")
    # AI call for the implementation phase was recorded.
    ai_calls_dir = result.ledger_dir / "iterations" / "iter-001" / "ai_calls"
    impl_calls = [d for d in ai_calls_dir.iterdir() if "implementation" in d.name]
    assert impl_calls, list(ai_calls_dir.iterdir())


def test_replay_run_records_actual_agent_triage_even_when_invalid(tmp_path: Path):
    """When the agent's triage output fails schema validation, the AI-call
    log must preserve what the agent *actually* said, not the harness's
    synthesized fallback. The fallback must also appear in the log, clearly
    labelled, so reviewers can tell which decisions came from the harness
    vs the agent (design §20)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    scenario = tmp_path / "failing_scenario"
    _make_failing_scenario(scenario)
    # Replace the triage output with something that fails the schema
    # (missing required ``next_action``).
    (scenario / "failure_triage.json").write_text(json.dumps({
        "type": "failure_triage",
        "failure_class": "code_suspected",
        # ``next_action``, ``confidence``, etc. missing on purpose.
    }, indent=2), encoding="utf-8")
    runner = create_runner("replay", replay_scenario=scenario)
    registry = load_default_registry()
    cfg = OrchestratorConfig(
        repo_root=repo,
        runs_dir=tmp_path / "runs",
        sandbox_dir=tmp_path / "sb",
        clean_workspace_dir=tmp_path / "clean",
        request="invalid triage path",
        provider="replay",
        replay_scenario=scenario,
        policy=LoopPolicy(max_code_iterations=1),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)
    result = orch.run()
    # The AI call directory for iter-001 must contain BOTH the original
    # (invalid) agent output AND the harness fallback record.
    ai_calls_dir = result.ledger_dir / "iterations" / "iter-001" / "ai_calls"
    triage_dirs = sorted(d.name for d in ai_calls_dir.iterdir() if "triage" in d.name)
    # one for the agent, one for the harness fallback
    agent_dirs = [d for d in triage_dirs if "harness_fallback" not in d]
    fallback_dirs = [d for d in triage_dirs if "harness_fallback" in d]
    assert agent_dirs, triage_dirs
    assert fallback_dirs, triage_dirs
    agent_output = read_json(ai_calls_dir / agent_dirs[0] / "output.json")
    # The recorded agent output is what the agent actually produced
    # (missing ``next_action``), not the synthesized object.
    assert "next_action" not in agent_output, agent_output
    fallback_output = read_json(ai_calls_dir / fallback_dirs[0] / "output.json")
    assert fallback_output["next_action"] == "stop_inconclusive"
    # The fallback's input must explain why the fallback was used.
    fallback_input = read_json(ai_calls_dir / fallback_dirs[0] / "input.json")
    assert "validation_error" in fallback_input


def test_extra_diagnostics_are_redacted_before_reaching_agent(tmp_path: Path):
    """When triage asks for ``request_more_diagnostics``, the harness invokes
    the requested capability and merges the result into the dossier sent
    back to the agent. The wrapper around each capability result (request +
    .error) must be redacted at the merge point — ``result.data`` is already
    redacted by the registry, but ``error`` and the request envelope are not.
    Anything that crosses the trust boundary into the agent prompt must be
    redacted (design §15/§16).
    """
    from harness.agents.base import AgentPhase, AgentPhaseResult, AgentRunner
    from harness.capabilities.base import Capability, CapabilityResult
    from harness.capabilities import load_default_registry
    from harness.orchestrator import Orchestrator, OrchestratorConfig

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    # A scripted runner: first triage requests more diagnostics, the
    # second triage stops the loop. The second triage's ``input_bundle``
    # holds the dossier with merged ``extra_diagnostics``; we capture it
    # so the test can assert redaction.
    captured: dict = {}

    class _ScriptedRunner(AgentRunner):
        provider_name = "scripted"
        def __init__(self):
            self.triage_calls = 0
        def profile(self):
            return {"provider": "scripted"}
        def run_phase(self, phase, *, workspace_path, task_contract,
                      run_manifest, input_bundle, output_schema_name,
                      budget_seconds):
            if phase is AgentPhase.TASK_CONTRACT:
                return AgentPhaseResult(output={
                    "type": "task_contract",
                    "implementation_goal": "x",
                    "assumptions": [], "success_criteria": ["e2e passes"],
                    "non_goals": [], "likely_components": [],
                    "validation_plan": [], "ambiguities": [],
                    "can_start_without_human": True,
                }, raw_log="")
            if phase is AgentPhase.IMPLEMENTATION:
                # Make a real change so patch extraction has something to do.
                (workspace_path / "marker.txt").write_text("scripted change\n")
                return AgentPhaseResult(output={
                    "type": "implementation_result",
                    "summary": "scripted",
                    "hypothesis": "h",
                    "confidence": "medium",
                    "expected_validation": [],
                    "risk_notes": [],
                    "claimed_changed_files": ["marker.txt"],
                }, raw_log="", workspace_after=workspace_path)
            if phase is AgentPhase.FAILURE_TRIAGE:
                self.triage_calls += 1
                if self.triage_calls == 1:
                    return AgentPhaseResult(output={
                        "type": "failure_triage",
                        "failure_class": "code_suspected",
                        "confidence": "low",
                        "next_action": "request_more_diagnostics",
                        "hypothesis": "need more logs",
                        "expected_effect": "",
                        "evidence_refs": [],
                        "requested_diagnostics": [{
                            "type": "diagnostic_request",
                            "capability": "leaky_cap",
                            "reason": "want it",
                            "params": {},
                        }],
                        "human_reason": None,
                    }, raw_log="")
                # round 2: capture what the orchestrator handed in.
                import copy
                captured["second_triage_input"] = copy.deepcopy(input_bundle)
                return AgentPhaseResult(output={
                    "type": "failure_triage",
                    "failure_class": "unknown",
                    "confidence": "low",
                    "next_action": "stop_inconclusive",
                    "hypothesis": "done",
                    "expected_effect": "",
                    "evidence_refs": [],
                    "requested_diagnostics": [],
                    "human_reason": "stop",
                }, raw_log="")
            raise AssertionError(phase)

    # A capability that returns an error string containing a fake token.
    class _Leaky(Capability):
        def invoke(self, *, params, manifest, ctx):
            return CapabilityResult(
                status="error",
                error="boom Authorization: Bearer ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )

    reg = load_default_registry()
    # Register an agent-requestable capability that leaks a secret in its
    # error. We have to declare it before binding the impl.
    from harness.capabilities.registry import CapabilitySpec
    reg._specs["leaky_cap"] = CapabilitySpec(
        name="leaky_cap", category="real_dev_agent_requestable",
        agent_requestable=True, timeout_seconds=60, uses_run_manifest=False,
        redacts_output=True, audit=True, prod_possible=False,
        forced_params={}, impl=_Leaky(),
    )

    # Build a scenario where E2E fails so triage gets invoked.
    scenario = tmp_path / "fail_scenario"
    _make_failing_scenario(scenario)

    cfg = OrchestratorConfig(
        repo_root=repo,
        runs_dir=tmp_path / "runs",
        sandbox_dir=tmp_path / "sb",
        clean_workspace_dir=tmp_path / "clean",
        request="scripted",
        provider="scripted",
        replay_scenario=scenario,  # so other capabilities still answer
        policy=LoopPolicy(
            max_code_iterations=1,
            max_validation_attempts_per_iteration=1,
            max_diagnostic_rounds_per_failure=2,
        ),
    )
    orch = Orchestrator(config=cfg, runner=_ScriptedRunner(), registry=reg)
    orch.run()

    bundle = captured.get("second_triage_input")
    assert bundle is not None, "second triage was never reached"
    dossier = bundle["dossier"]
    extras = dossier.get("extra_diagnostics")
    assert extras, dossier
    flat = json.dumps(extras)
    # The fake token must not survive to the second-triage prompt.
    assert "ghp_" not in flat, flat
    assert "[REDACTED]" in flat, flat


def test_wall_clock_budget_caps_inner_loop(tmp_path: Path):
    """The wall-clock budget must terminate the run during a single
    iteration if a long attempt-loop or diagnostic chain blows past it,
    not only between iterations (design §12). With
    ``max_total_wall_clock_minutes=0`` the orchestrator must terminate
    before doing any work and produce a ``failed_budget_exceeded`` final
    status.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    scenario = tmp_path / "failing_scenario"
    _make_failing_scenario(scenario)
    runner = create_runner("replay", replay_scenario=scenario)
    registry = load_default_registry()
    cfg = OrchestratorConfig(
        repo_root=repo,
        runs_dir=tmp_path / "runs",
        sandbox_dir=tmp_path / "sb",
        clean_workspace_dir=tmp_path / "clean",
        request="budget test",
        provider="replay",
        replay_scenario=scenario,
        policy=LoopPolicy(
            max_code_iterations=3,
            max_validation_attempts_per_iteration=5,
            max_total_wall_clock_minutes=0,  # exhausted from minute zero
        ),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)
    result = orch.run()
    assert result.final_status == "failed_budget_exceeded"
    tm = read_json(result.ledger_dir / "task_manifest.json")
    assert tm.get("stop_reason") == "budget_exceeded"


def test_wall_clock_budget_caps_attempt_loop_mid_iteration(tmp_path: Path):
    """Once an iteration has started, the wall-clock check must still
    bound the inner attempt loop. We force the budget to expire AFTER the
    first attempt by monkeypatching ``wall_clock_minutes`` to flip from 0
    to a huge number once any attempt has produced output. The second
    attempt must be skipped.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    scenario = tmp_path / "failing_scenario"
    _make_failing_scenario(scenario)
    # Triage says ``rerun_same_code`` so the attempt loop would normally
    # run twice without the budget cap.
    (scenario / "failure_triage.json").write_text(json.dumps({
        "type": "failure_triage",
        "failure_class": "flaky_test_suspected",
        "confidence": "low",
        "next_action": "rerun_same_code",
        "hypothesis": "flaky",
        "expected_effect": "",
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
        request="budget test inner",
        provider="replay",
        replay_scenario=scenario,
        policy=LoopPolicy(
            max_code_iterations=1,
            max_validation_attempts_per_iteration=3,
            # Big enough that the outer stop-condition check at the top of
            # the iteration loop passes.
            max_total_wall_clock_minutes=1000,
        ),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)

    # Make the budget look exhausted only AFTER the first attempt runs.
    # The orchestrator calls ``wall_clock_minutes`` in two places per
    # iteration: once in ``check_stop_conditions`` at the top of the
    # iteration loop, and once before each inner validation attempt. To
    # let attempt #1 run and skip attempt #2 we need the first two checks
    # to be under budget and any later check to be over.
    call_count = {"n": 0}
    def _toggle():
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return 0.0
        return 99999.0
    orch.state.wall_clock_minutes = _toggle  # type: ignore[assignment]

    result = orch.run()
    # Only one attempt should have been recorded for the single iteration.
    iter_manifest = read_json(
        result.ledger_dir / "iterations" / "iter-001" / "manifest.json"
    )
    assert iter_manifest["attempts"] == [1], iter_manifest


def test_replay_run_actually_applies_patch_to_clean_workspace(tmp_path: Path):
    """The whole point of the patch-extract / clean-reapply flow is that
    the clean workspace ends up with the agent's edits. ``test_replay_run_
    passes`` only checks that ``final_status == passed`` and that the patch
    file exists — pin the stronger contract here.
    """
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
        policy=LoopPolicy(max_code_iterations=1),
    )
    orch = Orchestrator(config=cfg, runner=runner, registry=registry)
    result = orch.run()
    assert result.final_status == "passed"
    # The clean workspace for the selected iteration must contain the
    # patched files, not the original baseline content.
    iter_clean = clean_dir / f"iter-{result.selected_iteration:03d}"
    # The scenario patches src/encoder/init.py — check it's not the
    # baseline ``# placeholder``.
    init_py = iter_clean / "src" / "encoder" / "init.py"
    assert init_py.exists(), list(iter_clean.rglob("*"))
    text = init_py.read_text(encoding="utf-8")
    assert text != "# placeholder\n", text


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
