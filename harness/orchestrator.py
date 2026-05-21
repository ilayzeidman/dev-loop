"""Main autonomous loop.

The harness owns workflow state, validation, manifest, and external
capabilities. The agent reasons, edits code, and emits schema-validated
decisions. This module wires it all together.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import schemas
from .agents import AgentPhase, AgentRunner
from .capabilities import CapabilityRegistry
from .capabilities.registry import audit_to_jsonl
from .manifests import TaskLedger, slugify
from .patch import PatchInfo, apply_patch_to_clean, extract_patch
from .policy import (
    LoopPolicy,
    LoopState,
    check_stop_conditions,
    code_change_allowed,
    fingerprint_failure,
)
from .redaction import redact
from .report import render_review_report
from .triage import build_failure_dossier
from .util import utc_now_iso, write_json, write_text


@dataclass
class OrchestratorConfig:
    repo_root: Path
    runs_dir: Path
    sandbox_dir: Path
    clean_workspace_dir: Path
    request: str
    provider: str
    replay_scenario: Path | None = None
    policy: LoopPolicy = field(default_factory=LoopPolicy)
    harness_version: str = "0.1.0"


@dataclass
class OrchestratorResult:
    task_id: str
    final_status: str
    selected_iteration: int | None
    ledger_dir: Path
    report_path: Path


class Orchestrator:
    def __init__(
        self,
        *,
        config: OrchestratorConfig,
        runner: AgentRunner,
        registry: CapabilityRegistry,
    ) -> None:
        self.cfg = config
        self.runner = runner
        self.registry = registry
        self.state = LoopState()

    # public ------------------------------------------------------------

    def run(self) -> OrchestratorResult:
        task_id = self._allocate_task_id()
        ledger = TaskLedger.create(self.cfg.runs_dir, task_id)
        base_sha = self._head_sha(self.cfg.repo_root)
        ledger.record_baseline(base_sha=base_sha, original_prompt=self.cfg.request)

        self.registry.set_audit_sink(audit_to_jsonl(ledger.root / "capability_audit.jsonl"))

        # Phase: task contract ------------------------------------------
        contract_input = {"original_request": self.cfg.request}
        contract_res = self.runner.run_phase(
            AgentPhase.TASK_CONTRACT,
            workspace_path=self.cfg.repo_root,
            task_contract=None,
            run_manifest=None,
            input_bundle=contract_input,
            output_schema_name="task_contract.v1.json",
            budget_seconds=300,
        )
        try:
            schemas.validate("task_contract.v1.json", contract_res.output)
        except Exception as e:
            ledger.update_task_manifest(
                status="aborted",
                final_status="failed_inconclusive",
                error=f"invalid task_contract: {e}",
            )
            return self._finalize_failure(
                ledger=ledger,
                final_status="failed_inconclusive",
                contract=None,
                iteration_records=[],
                selected_iteration=None,
                base_sha=base_sha,
            )

        contract = contract_res.output
        ledger.update_task_manifest(task_contract=contract, status="contract_ready")

        if not contract.get("can_start_without_human", False):
            # Ambiguous task: stop and ask human.
            ledger.update_task_manifest(
                status="awaiting_human",
                final_status="failed_human_required",
            )
            return self._finalize_failure(
                ledger=ledger,
                final_status="failed_human_required",
                contract=contract,
                iteration_records=[],
                selected_iteration=None,
                base_sha=base_sha,
            )

        # ----------------------------------------------------------------
        iteration_records: list[dict[str, Any]] = []
        selected_iteration: int | None = None
        last_triage: dict[str, Any] | None = None
        prev_failure_dossier: dict[str, Any] | None = None
        prev_iteration_summary: dict[str, Any] | None = None
        loop_done = False
        hit_stop_reason: str | None = None

        for iteration in range(1, self.cfg.policy.max_code_iterations + 1):
            stop_reason = check_stop_conditions(
                policy=self.cfg.policy, state=self.state, last_triage=last_triage,
            )
            if stop_reason:
                hit_stop_reason = stop_reason
                ledger.update_task_manifest(status="stopped", stop_reason=stop_reason)
                break

            try:
                iter_record = self._run_iteration(
                    ledger=ledger,
                    iteration=iteration,
                    base_sha=base_sha,
                    contract=contract,
                    prev_failure_dossier=prev_failure_dossier,
                    prev_iteration_summary=prev_iteration_summary,
                )
            except Exception as e:
                # An unexpected failure inside the iteration (sandbox prep
                # blew up, the runner crashed, disk full, etc.) must not
                # leave the task ledger half-written. Record a synthesized
                # failed-iteration record so ``_write_report`` and the
                # outer loop have well-formed state, then stop the loop
                # with ``failed_inconclusive``.
                reason = f"iteration {iteration} crashed: {type(e).__name__}: {e}"
                try:
                    ledger.write_iteration_manifest(iteration, {
                        "task_id": ledger.task_id,
                        "iteration": iteration,
                        "code": {
                            "base_sha": base_sha,
                            "patch_hash": None,
                            "changed_files": [],
                            "claim_mismatches": {},
                        },
                        "agent_output": None,
                        "attempts": [],
                        "final_e2e_status": "failed",
                        "error": reason,
                    })
                except Exception:
                    # If even the manifest write fails, fall through —
                    # we still want to terminate cleanly below.
                    pass
                iter_record = _failed_iteration_record(
                    iteration=iteration, reason=reason,
                )
                iteration_records.append(iter_record)
                self.state.code_iterations_done += 1
                last_triage = iter_record["final_attempt"]["triage"]
                hit_stop_reason = "harness_issue_declared"
                break
            iteration_records.append(iter_record)
            self.state.code_iterations_done += 1

            if iter_record["final_attempt"]["e2e"]["status"] == "passed":
                selected_iteration = iteration
                loop_done = True
                break

            # Failure path: triage.
            self.state.failure_fingerprints.append(
                fingerprint_failure(iter_record["final_attempt"]["e2e"])
            )
            last_triage = iter_record["final_attempt"]["triage"]
            prev_failure_dossier = iter_record["final_attempt"]["dossier"]
            prev_iteration_summary = {
                "iteration": iteration,
                "summary": iter_record["agent_summary"],
                "e2e_status": iter_record["final_attempt"]["e2e"]["status"],
            }

            if not code_change_allowed(self.cfg.policy, last_triage):
                # Triage didn't authorize another iteration.
                break

        # ----------------------------------------------------------------
        final_status = self._final_status(
            loop_done=loop_done,
            selected_iteration=selected_iteration,
            last_triage=last_triage,
            hit_stop_reason=hit_stop_reason,
        )

        report_path = self._write_report(
            ledger=ledger,
            contract=contract,
            iteration_records=iteration_records,
            selected_iteration=selected_iteration,
            base_sha=base_sha,
            final_status=final_status,
        )
        ledger.update_task_manifest(
            status="completed",
            final_status=final_status,
            selected_iteration=selected_iteration,
        )

        return OrchestratorResult(
            task_id=task_id,
            final_status=final_status,
            selected_iteration=selected_iteration,
            ledger_dir=ledger.root,
            report_path=report_path,
        )

    # internals ---------------------------------------------------------

    def _allocate_task_id(self) -> str:
        slug = slugify(self.cfg.request)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{ts}-{slug}"

    def _run_iteration(
        self,
        *,
        ledger: TaskLedger,
        iteration: int,
        base_sha: str,
        contract: dict[str, Any],
        prev_failure_dossier: dict[str, Any] | None,
        prev_iteration_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        iter_dir = ledger.create_iteration(iteration)

        sandbox = self._prepare_sandbox(base_sha, iteration)

        # Phase: implementation
        impl_input = {
            "iteration": iteration,
            "previous_failure_dossier": prev_failure_dossier,
            "previous_iteration_summary": prev_iteration_summary,
            "agent_requestable_capabilities": self.registry.agent_requestable(),
        }
        impl_res = self.runner.run_phase(
            AgentPhase.IMPLEMENTATION,
            workspace_path=sandbox,
            task_contract=contract,
            run_manifest=None,
            input_bundle=impl_input,
            output_schema_name="implementation_result.v1.json",
            budget_seconds=1800,
        )
        # Record the AI call before validation so the raw output survives
        # even when the agent emits something that fails schema validation —
        # otherwise the iteration dir would be empty and the audit trail
        # would lose the only evidence of what the agent actually produced.
        ledger.record_ai_call(
            iteration, 1, "implementation",
            input_obj=redact(impl_input),
            output_obj=impl_res.output if isinstance(impl_res.output, dict) else {"_raw": str(impl_res.output)},
            raw_provider_log=impl_res.raw_log,
        )

        try:
            schemas.validate("implementation_result.v1.json", impl_res.output)
        except Exception as e:
            reason = f"invalid implementation_result: {e}"
            # Write an iteration manifest so the iteration is auditable
            # even though no patch was extracted. Per design §8 every
            # completed iteration must have a manifest.
            ledger.write_iteration_manifest(iteration, {
                "task_id": ledger.task_id,
                "iteration": iteration,
                "code": {
                    "base_sha": base_sha,
                    "patch_hash": None,
                    "changed_files": [],
                    "claim_mismatches": {},
                },
                "agent_output": impl_res.output if isinstance(impl_res.output, dict) else None,
                "attempts": [],
                "final_e2e_status": "failed",
                "error": reason,
            })
            return _failed_iteration_record(iteration=iteration, reason=reason)

        # Patch extraction
        patch = extract_patch(
            sandbox,
            claimed_files=impl_res.output.get("claimed_changed_files", []),
        )
        write_text(iter_dir / "patch.diff", patch.diff)
        write_json(iter_dir / "changed_files.json", {
            "changed_files": patch.changed_files,
            "untracked_files": patch.untracked_files,
            "deleted_files": patch.deleted_files,
            "binary_files": patch.binary_files,
            "claim_mismatches": patch.claim_mismatches,
            "patch_hash": patch.patch_hash,
        })

        # Apply to clean workspace
        clean_ws = self.cfg.clean_workspace_dir / f"iter-{iteration:03d}"
        try:
            apply_patch_to_clean(
                base_repo=self.cfg.repo_root,
                base_sha=base_sha,
                patch_diff=patch.diff,
                dest=clean_ws,
            )
            apply_ok = True
            apply_err = None
        except subprocess.CalledProcessError as e:
            apply_ok = False
            apply_err = (e.stderr or "").strip() or str(e)

        # Validation attempts
        attempts: list[dict[str, Any]] = []
        attempt_index = 0
        e2e_status = "failed"
        final_attempt: dict[str, Any] | None = None

        if not apply_ok:
            attempt_index += 1
            attempt_dir = ledger.create_attempt(iteration, attempt_index)
            record = {
                "attempt": attempt_index,
                "outcome": "patch_apply_failed",
                "error": apply_err,
                "e2e": {"status": "failed", "first_error": apply_err},
                "triage": _synth_triage_apply_failed(apply_err),
                "dossier": None,
            }
            write_json(attempt_dir / "outcome.json", record)
            attempts.append(record)
            final_attempt = record
        else:
            budget_hit = False
            for attempt in range(1, self.cfg.policy.max_validation_attempts_per_iteration + 1):
                # Wall-clock budget can be blown DURING an iteration if a
                # single attempt's agent phases (impl + triage + diagnostic
                # rounds) run long. Subprocess-level timeouts bound each
                # phase, but the cumulative budget needs its own check
                # (design §12) — otherwise a single iteration can keep
                # spending well past ``max_total_wall_clock_minutes``.
                if self.state.wall_clock_minutes() >= self.cfg.policy.max_total_wall_clock_minutes:
                    budget_hit = True
                    break
                attempt_index = attempt
                record = self._run_validation_attempt(
                    ledger=ledger,
                    iteration=iteration,
                    attempt=attempt,
                    base_sha=base_sha,
                    contract=contract,
                    patch=patch,
                    clean_ws=clean_ws,
                    impl_output=impl_res.output,
                    prev_iteration_summary=prev_iteration_summary,
                )
                attempts.append(record)
                final_attempt = record
                e2e_status = record["e2e"]["status"]
                if e2e_status == "passed":
                    break
                if record["triage"]["next_action"] != "rerun_same_code":
                    break
            # If we hit the budget before producing any attempt, synthesize
            # a placeholder final_attempt so the outer loop and the report
            # writer don't trip on ``None`` (they index into
            # ``final_attempt["e2e"]["status"]`` etc).
            if final_attempt is None and budget_hit:
                final_attempt = {
                    "attempt": 0,
                    "outcome": "budget_exceeded_before_attempt",
                    "e2e": {"status": "failed",
                            "first_error": "wall-clock budget exceeded"},
                    "triage": {
                        "type": "failure_triage",
                        "failure_class": "harness_suspected",
                        "confidence": "high",
                        "next_action": "stop_inconclusive",
                        "hypothesis": "wall-clock budget exceeded",
                        "expected_effect": "",
                        "evidence_refs": [],
                        "requested_diagnostics": [],
                        "human_reason": "budget_exceeded",
                    },
                    "dossier": None,
                }

        iter_manifest = {
            "task_id": ledger.task_id,
            "iteration": iteration,
            "code": {
                "base_sha": base_sha,
                "patch_hash": patch.patch_hash,
                "changed_files": patch.changed_files,
                "claim_mismatches": patch.claim_mismatches,
            },
            "agent_output": impl_res.output,
            "attempts": [a["attempt"] for a in attempts],
            "final_e2e_status": (final_attempt or {}).get("e2e", {}).get("status"),
        }
        ledger.write_iteration_manifest(iteration, iter_manifest)

        return {
            "iteration": iteration,
            "agent_summary": impl_res.output.get("summary"),
            "agent_output": impl_res.output,
            "patch": {
                "hash": patch.patch_hash,
                "changed_files": patch.changed_files,
                "claim_mismatches": patch.claim_mismatches,
            },
            "attempts": attempts,
            "final_attempt": final_attempt,
        }

    def _run_validation_attempt(
        self,
        *,
        ledger: TaskLedger,
        iteration: int,
        attempt: int,
        base_sha: str,
        contract: dict[str, Any],
        patch: PatchInfo,
        clean_ws: Path,
        impl_output: dict[str, Any],
        prev_iteration_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        attempt_dir = ledger.create_attempt(iteration, attempt)
        run_manifest = _make_run_manifest(
            task_id=ledger.task_id,
            iteration=iteration,
            attempt=attempt,
            base_sha=base_sha,
            patch=patch,
        )

        # 1. local build
        build = self.registry.invoke(
            "local_build",
            params={}, manifest=run_manifest,
            ctx={"workspace": str(clean_ws), "replay_scenario": self._scenario_str()},
        )
        write_json(attempt_dir / "local_build.json", _result_as_json(build))

        # 2. local test
        test = self.registry.invoke(
            "local_test",
            params={}, manifest=run_manifest,
            ctx={"workspace": str(clean_ws), "replay_scenario": self._scenario_str()},
        )
        write_json(attempt_dir / "local_test.json", _result_as_json(test))

        # 3. dev jenkins build
        jenkins = self.registry.invoke(
            "trigger_dev_jenkins_build",
            params={"branch": "dev-loop", "commit_sha": base_sha, "version": patch.patch_hash[:7]},
            manifest=run_manifest,
            ctx={"replay_scenario": self._scenario_str()},
        )
        write_json(attempt_dir / "jenkins_build.json", _result_as_json(jenkins))

        # 4. deploy dev validation version
        deployment = self.registry.invoke(
            "deploy_dev_validation_version",
            params={"image_tag": jenkins.data.get("image_tag")},
            manifest=run_manifest,
            ctx={"replay_scenario": self._scenario_str()},
        )
        write_json(attempt_dir / "deployment.json", _result_as_json(deployment))

        # update manifest with deployment info
        run_manifest["build"] = {
            "version": jenkins.data.get("version") or patch.patch_hash[:7],
            "image_tag": jenkins.data.get("image_tag"),
            "jenkins_job": jenkins.data.get("jenkins_job"),
            "jenkins_build_id": jenkins.data.get("jenkins_build_id"),
        }
        run_manifest["deployment"] = {
            "namespace": deployment.data.get("namespace"),
            "pods": deployment.data.get("pods", []),
            "nodes": deployment.data.get("nodes", []),
            "gpu_ids": deployment.data.get("gpu_ids", []),
        }

        # 5. immutable E2E
        e2e = self.registry.invoke(
            "run_immutable_e2e",
            params={}, manifest=run_manifest,
            ctx={"replay_scenario": self._scenario_str()},
        )
        e2e_data = e2e.data
        write_json(attempt_dir / "e2e_result.json", _result_as_json(e2e))

        run_manifest["e2e"] = {
            "test_suite": e2e_data.get("test_suite", "immutable-e2e"),
            "status": e2e_data.get("status", "failed"),
            "started_at_utc": e2e_data.get("started_at_utc", utc_now_iso()),
            "finished_at_utc": e2e_data.get("finished_at_utc", utc_now_iso()),
            "device_id": e2e_data.get("device_id"),
        }
        run_manifest["diagnostic_windows"] = e2e_data.get("diagnostic_windows", {
            "elastic_from_utc": run_manifest["e2e"]["started_at_utc"],
            "elastic_to_utc": run_manifest["e2e"]["finished_at_utc"],
            "grafana_from_utc": run_manifest["e2e"]["started_at_utc"],
            "grafana_to_utc": run_manifest["e2e"]["finished_at_utc"],
        })
        run_manifest["status"] = (
            "passed" if run_manifest["e2e"]["status"] == "passed" else "failed_e2e"
        )
        write_json(attempt_dir / "run_manifest.json", run_manifest)

        if run_manifest["e2e"]["status"] == "passed":
            outcome = {
                "attempt": attempt,
                "outcome": "passed",
                "e2e": e2e_data,
                "triage": None,
                "dossier": None,
            }
            write_json(attempt_dir / "outcome.json", outcome)
            return outcome

        # 6. collect diagnostics
        diag_dir = attempt_dir / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        diag = self._collect_diagnostics(run_manifest, diag_dir)

        # 7. build dossier
        dossier = build_failure_dossier(
            run_manifest=run_manifest,
            e2e_result=e2e_data,
            jenkins_result=jenkins.data,
            deployment=deployment.data,
            elastic_summary=diag.get("elastic"),
            grafana_metrics=diag.get("grafana"),
            pod_logs_excerpt=diag.get("pod_logs"),
            gpu_utilization=diag.get("gpu"),
            diff_summary={
                "patch_hash": patch.patch_hash,
                "changed_files": patch.changed_files,
                "claim_mismatches": patch.claim_mismatches,
            },
            previous_iteration_summary=prev_iteration_summary,
        )

        # 8. triage
        triage_input = {
            "dossier": dossier,
            "agent_requestable_capabilities": self.registry.agent_requestable(),
            "implementation_result": impl_output,
        }
        triage_res = self.runner.run_phase(
            AgentPhase.FAILURE_TRIAGE,
            workspace_path=clean_ws,
            task_contract=contract,
            run_manifest=run_manifest,
            input_bundle=triage_input,
            output_schema_name="failure_triage.v1.json",
            budget_seconds=900,
        )
        triage = self._validate_or_synth_triage(
            triage_res=triage_res,
            ledger=ledger,
            iteration=iteration,
            ordinal=10 + attempt,
            phase=f"triage_attempt_{attempt}",
            triage_input=triage_input,
        )

        # 9. bounded follow-up diagnostic requests
        rounds = 0
        while (
            triage.get("next_action") == "request_more_diagnostics"
            and rounds < self.cfg.policy.max_diagnostic_rounds_per_failure
            and self.state.wall_clock_minutes()
            < self.cfg.policy.max_total_wall_clock_minutes
        ):
            rounds += 1
            extra = self._fulfill_diagnostic_requests(
                triage.get("requested_diagnostics", []),
                run_manifest=run_manifest,
                attempt_dir=attempt_dir,
                round_idx=rounds,
            )
            # Re-redact: ``result.data`` from each capability is already
            # redacted by the registry, but ``result.error`` and the
            # wrapping ``request`` dict are not. Anything that crosses the
            # trust boundary into ``triage_input`` (which is fed to the
            # agent prompt) must be redacted (design §15, §16).
            dossier = {**dossier, "extra_diagnostics": redact(extra)}
            triage_input["dossier"] = dossier
            triage_res = self.runner.run_phase(
                AgentPhase.FAILURE_TRIAGE,
                workspace_path=clean_ws,
                task_contract=contract,
                run_manifest=run_manifest,
                input_bundle=triage_input,
                output_schema_name="failure_triage.v1.json",
                budget_seconds=900,
            )
            triage = self._validate_or_synth_triage(
                triage_res=triage_res,
                ledger=ledger,
                iteration=iteration,
                ordinal=20 + attempt * 10 + rounds,
                phase=f"triage_attempt_{attempt}_round_{rounds}",
                triage_input=triage_input,
            )

        outcome = {
            "attempt": attempt,
            "outcome": "failed_e2e",
            "e2e": e2e_data,
            "triage": triage,
            "dossier": dossier,
            "diagnostics_rounds": rounds,
        }
        write_json(attempt_dir / "outcome.json", outcome)
        return outcome

    def _validate_or_synth_triage(
        self,
        *,
        triage_res,
        ledger: TaskLedger,
        iteration: int,
        ordinal: int,
        phase: str,
        triage_input: dict[str, Any],
    ) -> dict[str, Any]:
        """Schema-validate the agent's triage output and record an AI call.

        Critically: the AI call record must preserve the *actual* agent
        output, even when it fails schema validation, so the audit trail
        shows what the agent really produced (design §20). When the output
        is invalid we synthesize a harness fallback and record it as a
        separate, clearly-labeled AI-like call so downstream reviewers can
        tell which decisions were the model's vs the harness's.
        """
        raw_output = triage_res.output
        try:
            schemas.validate("failure_triage.v1.json", raw_output)
            ledger.record_ai_call(
                iteration, ordinal, phase,
                input_obj=redact(triage_input),
                output_obj=raw_output,
                raw_provider_log=triage_res.raw_log,
            )
            return raw_output
        except Exception as e:
            # 1. Record the *actual* agent output as the AI call so audit
            #    preserves what the model produced.
            ledger.record_ai_call(
                iteration, ordinal, phase,
                input_obj=redact(triage_input),
                output_obj=raw_output if isinstance(raw_output, dict)
                           else {"_raw": str(raw_output)},
                raw_provider_log=triage_res.raw_log,
            )
            # 2. Record the harness-synthesized fallback separately so it's
            #    obvious in the audit trail that this decision came from the
            #    harness, not the agent.
            synth = _synth_triage_invalid_output(str(e))
            ledger.record_ai_call(
                iteration, ordinal, f"{phase}_harness_fallback",
                input_obj={"reason": "agent triage failed schema validation",
                           "validation_error": str(e)},
                output_obj=synth,
                raw_provider_log=None,
            )
            return synth

    # diagnostics -------------------------------------------------------

    def _collect_diagnostics(
        self, run_manifest: dict[str, Any], diag_dir: Path,
    ) -> dict[str, Any]:
        ctx = {"replay_scenario": self._scenario_str()}
        res: dict[str, Any] = {}

        elastic = self.registry.invoke(
            "query_elastic_for_current_run",
            params={"severity": ["error", "warn"], "max_lines": 200},
            manifest=run_manifest, ctx=ctx,
        )
        write_json(diag_dir / "elastic_summary.json", _result_as_json(elastic))
        res["elastic"] = elastic.data

        grafana = self.registry.invoke(
            "get_grafana_metrics_for_current_run",
            params={}, manifest=run_manifest, ctx=ctx,
        )
        write_json(diag_dir / "grafana_metrics.json", _result_as_json(grafana))
        res["grafana"] = grafana.data

        pods = self.registry.invoke(
            "get_pod_logs_for_current_run",
            params={"max_lines": 200}, manifest=run_manifest, ctx=ctx,
        )
        pod_logs = pods.data.get("text") or pods.data.get("excerpt") or ""
        write_text(diag_dir / "pod_logs_excerpt.txt", pod_logs)
        res["pod_logs"] = pod_logs

        gpu = self.registry.invoke(
            "collect_gpu_metrics_for_current_run",
            params={}, manifest=run_manifest, ctx=ctx,
        )
        write_json(diag_dir / "gpu_utilization.json", _result_as_json(gpu))
        res["gpu"] = gpu.data
        return res

    def _fulfill_diagnostic_requests(
        self,
        requests: list[dict[str, Any]],
        *,
        run_manifest: dict[str, Any],
        attempt_dir: Path,
        round_idx: int,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        ctx = {"replay_scenario": self._scenario_str()}
        for i, req in enumerate(requests, start=1):
            try:
                schemas.validate("diagnostic_request.v1.json", req)
                cap = req["capability"]
                params = req.get("params", {})
                result = self.registry.invoke(
                    cap, params=params, manifest=run_manifest,
                    ctx=ctx, from_agent=True,
                )
                rec = {
                    "request": req,
                    "result": _result_as_json(result),
                }
            except Exception as e:
                rec = {"request": req, "error": str(e)}
            out.append(rec)
            write_json(
                attempt_dir / "diagnostics" / f"extra_round_{round_idx:02d}_req_{i:02d}.json",
                rec,
            )
        return out

    # sandbox -----------------------------------------------------------

    def _prepare_sandbox(self, base_sha: str, iteration: int) -> Path:
        sb = self.cfg.sandbox_dir / f"iter-{iteration:03d}"
        if sb.exists():
            shutil.rmtree(sb)
        sb.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", str(self.cfg.repo_root), str(sb)],
            check=True, cwd=self.cfg.repo_root.parent,
        )
        subprocess.run(
            ["git", "checkout", "--quiet", base_sha],
            check=True, cwd=sb,
        )
        return sb

    # report ------------------------------------------------------------

    def _write_report(
        self,
        *,
        ledger: TaskLedger,
        contract: dict[str, Any],
        iteration_records: list[dict[str, Any]],
        selected_iteration: int | None,
        base_sha: str,
        final_status: str,
    ) -> Path:
        final_patch: dict[str, Any] = {}
        if selected_iteration is not None:
            iter_rec = next(r for r in iteration_records if r["iteration"] == selected_iteration)
            final_patch = iter_rec["patch"]
        changed_files = final_patch.get("changed_files", [])
        out_of_scope = _detect_out_of_scope(
            changed_files, contract.get("likely_components", [])
        )

        report = {
            "task_id": ledger.task_id,
            "original_request": self.cfg.request,
            "task_contract": contract,
            "agent_profile": self.runner.profile(),
            "harness_profile": self._harness_profile(),
            "final_status": final_status,
            "selected_iteration": selected_iteration,
            "iterations": [_summarize_iteration(r) for r in iteration_records],
            "final_patch_summary": final_patch,
            "changed_files": changed_files,
            "out_of_scope_files": out_of_scope,
            "validation_evidence": {
                "final_attempt": (
                    iteration_records[-1]["final_attempt"] if iteration_records else None
                ),
            },
            "risk_notes": _aggregate_risk_notes(iteration_records),
            "human_review_focus_areas": _human_review_areas(
                changed_files, out_of_scope, iteration_records,
            ),
            "base_sha": base_sha,
            "generated_at_utc": utc_now_iso(),
        }
        try:
            schemas.validate("final_review_report.v1.json", report)
        except Exception as e:
            report["schema_validation_error"] = str(e)
        write_json(ledger.root / "final_review_report.json", report)
        md = render_review_report(report)
        write_text(ledger.root / "final_review_report.md", md)
        return ledger.root / "final_review_report.md"

    def _harness_profile(self) -> dict[str, Any]:
        return {
            "harness_version": self.cfg.harness_version,
            "capability_registry": self.registry.all_names(),
            "policy": {
                "max_code_iterations": self.cfg.policy.max_code_iterations,
                "max_validation_attempts_per_iteration": self.cfg.policy.max_validation_attempts_per_iteration,
                "max_diagnostic_rounds_per_failure": self.cfg.policy.max_diagnostic_rounds_per_failure,
                "max_total_wall_clock_minutes": self.cfg.policy.max_total_wall_clock_minutes,
            },
            "schema_versions": {
                "task_contract": "v1",
                "implementation_result": "v1",
                "failure_triage": "v1",
                "diagnostic_request": "v1",
                "final_review_report": "v1",
            },
        }

    def _final_status(
        self,
        *,
        loop_done: bool,
        selected_iteration: int | None,
        last_triage: dict[str, Any] | None,
        hit_stop_reason: str | None = None,
    ) -> str:
        if loop_done and selected_iteration is not None:
            return "passed"
        # Hard stop conditions from policy.check_stop_conditions get
        # priority over whatever the agent's last triage said.
        if hit_stop_reason == "budget_exceeded":
            return "failed_budget_exceeded"
        if hit_stop_reason == "max_code_iterations_reached":
            return "failed_budget_exceeded"
        if hit_stop_reason == "same_failure_fingerprint_after_2_code_iterations":
            return "failed_stop_condition"
        if hit_stop_reason == "agent_requested_human":
            return "failed_human_required"
        if hit_stop_reason == "agent_stopped_inconclusive":
            return "failed_inconclusive"
        if hit_stop_reason in (
            "environment_issue_declared",
            "harness_issue_declared",
        ):
            return "failed_stop_condition"
        if last_triage is not None:
            action = last_triage.get("next_action")
            if action == "ask_human":
                return "failed_human_required"
            if action == "stop_inconclusive":
                return "failed_inconclusive"
            if action in ("declare_environment_issue", "declare_harness_issue"):
                return "failed_stop_condition"
        if self.state.code_iterations_done >= self.cfg.policy.max_code_iterations:
            return "failed_budget_exceeded"
        return "failed_inconclusive"

    def _finalize_failure(
        self,
        *,
        ledger: TaskLedger,
        final_status: str,
        contract: dict[str, Any] | None,
        iteration_records: list[dict[str, Any]],
        selected_iteration: int | None,
        base_sha: str,
    ) -> OrchestratorResult:
        report_path = self._write_report(
            ledger=ledger,
            contract=contract or _empty_contract(self.cfg.request),
            iteration_records=iteration_records,
            selected_iteration=selected_iteration,
            base_sha=base_sha,
            final_status=final_status,
        )
        return OrchestratorResult(
            task_id=ledger.task_id,
            final_status=final_status,
            selected_iteration=selected_iteration,
            ledger_dir=ledger.root,
            report_path=report_path,
        )

    # helpers -----------------------------------------------------------

    def _scenario_str(self) -> str | None:
        return str(self.cfg.replay_scenario) if self.cfg.replay_scenario else None

    @staticmethod
    def _head_sha(repo: Path) -> str:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        return out


# helpers ---------------------------------------------------------------


def _result_as_json(result) -> dict[str, Any]:
    return {"status": result.status, "data": result.data, "error": result.error}


def _make_run_manifest(
    *,
    task_id: str,
    iteration: int,
    attempt: int,
    base_sha: str,
    patch: PatchInfo,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "iteration": iteration,
        "validation_attempt": attempt,
        "status": "pending",
        "code": {
            "base_sha": base_sha,
            "patch_hash": patch.patch_hash,
            "changed_files": patch.changed_files,
        },
        "build": {},
        "deployment": {},
        "e2e": {},
        "diagnostic_windows": {},
    }


def _synth_triage_apply_failed(error: str | None) -> dict[str, Any]:
    return {
        "type": "failure_triage",
        "failure_class": "harness_suspected",
        "confidence": "high",
        "next_action": "declare_harness_issue",
        "hypothesis": "Patch could not be applied to clean workspace.",
        "expected_effect": "",
        "evidence_refs": [],
        "requested_diagnostics": [],
        "human_reason": error or "patch apply failed",
    }


def _synth_triage_invalid_output(reason: str) -> dict[str, Any]:
    return {
        "type": "failure_triage",
        "failure_class": "harness_suspected",
        "confidence": "low",
        "next_action": "stop_inconclusive",
        "hypothesis": "Agent triage output failed schema validation.",
        "expected_effect": "",
        "evidence_refs": [],
        "requested_diagnostics": [],
        "human_reason": reason,
    }


def _failed_iteration_record(*, iteration: int, reason: str) -> dict[str, Any]:
    return {
        "iteration": iteration,
        "agent_summary": None,
        "agent_output": None,
        "patch": {"hash": None, "changed_files": [], "claim_mismatches": {}},
        "attempts": [],
        "final_attempt": {
            "attempt": 0,
            "outcome": "agent_output_invalid",
            "e2e": {"status": "failed", "first_error": reason},
            "triage": _synth_triage_invalid_output(reason),
            "dossier": None,
        },
    }


def _summarize_iteration(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "iteration": rec["iteration"],
        "summary": rec.get("agent_summary"),
        "patch_hash": rec["patch"].get("hash"),
        "changed_files": rec["patch"].get("changed_files", []),
        "claim_mismatches": rec["patch"].get("claim_mismatches", {}),
        "attempts": [
            {
                "attempt": a["attempt"],
                "outcome": a["outcome"],
                "e2e_status": a["e2e"].get("status"),
                "triage_action": (a.get("triage") or {}).get("next_action"),
            }
            for a in rec.get("attempts", [])
        ],
    }


def _detect_out_of_scope(
    changed: list[str], likely_components: list[str],
) -> list[str]:
    if not likely_components:
        return []
    out: list[str] = []
    for f in changed:
        if not any(comp and (comp in f or f.startswith(comp)) for comp in likely_components):
            out.append(f)
    return out


def _aggregate_risk_notes(iterations: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    for r in iterations:
        agent = r.get("agent_output") or {}
        for n in agent.get("risk_notes", []) or []:
            notes.append(f"iter {r['iteration']}: {n}")
    return notes


def _human_review_areas(
    changed_files: list[str],
    out_of_scope: list[str],
    iterations: list[dict[str, Any]],
) -> list[str]:
    areas: list[str] = []
    if out_of_scope:
        areas.append(
            f"{len(out_of_scope)} file(s) changed outside the agent's claimed scope"
        )
    for r in iterations:
        mm = r["patch"].get("claim_mismatches") or {}
        if mm:
            areas.append(
                f"iter {r['iteration']}: agent claims diverged from real diff: {sorted(mm.keys())}"
            )
    return areas


def _empty_contract(req: str) -> dict[str, Any]:
    return {
        "type": "task_contract",
        "implementation_goal": req,
        "assumptions": [],
        "success_criteria": [],
        "non_goals": [],
        "likely_components": [],
        "validation_plan": [],
        "ambiguities": ["task contract could not be produced"],
        "can_start_without_human": False,
    }
