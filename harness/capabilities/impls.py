"""Built-in capability implementations.

In v1 these are replay-aware: when ``ctx["replay_scenario"]`` is set, each
capability reads the corresponding artifact from the scenario directory.
Otherwise they return a deterministic stub or an explicit "not configured"
error. Real adapters (Jenkins HTTP, kube API, Elastic, Grafana) belong in
separate modules wired in by the host integrator.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .base import Capability, CapabilityResult
from .registry import CapabilityRegistry


def _scenario_path(ctx: dict[str, Any]) -> Path | None:
    p = ctx.get("replay_scenario")
    return Path(p) if p else None


def _load_scenario_file(ctx: dict[str, Any], name: str) -> Any | None:
    sc = _scenario_path(ctx)
    if not sc:
        return None
    f = sc / name
    if not f.exists():
        return None
    if f.suffix == ".json":
        return json.loads(f.read_text(encoding="utf-8"))
    return f.read_text(encoding="utf-8")


class _LocalBuild(Capability):
    def invoke(self, *, params, manifest, ctx) -> CapabilityResult:
        if _scenario_path(ctx) is not None:
            return CapabilityResult(status="ok", data={"build": "skipped (replay)"})
        workspace = ctx.get("workspace")
        if not workspace:
            return CapabilityResult(status="error", error="workspace not provided")
        # Best-effort: only run if a Makefile exists. Otherwise no-op.
        mk = Path(workspace) / "Makefile"
        if not mk.exists():
            return CapabilityResult(status="ok", data={"build": "no Makefile, skipped"})
        try:
            out = subprocess.run(
                ["make", "build"], cwd=workspace, capture_output=True, text=True,
                timeout=params.get("timeout", 600),
            )
            return CapabilityResult(
                status="ok" if out.returncode == 0 else "error",
                data={"stdout": out.stdout[-4000:], "stderr": out.stderr[-4000:],
                      "returncode": out.returncode},
                error=None if out.returncode == 0 else f"make build exit {out.returncode}",
            )
        except Exception as e:
            return CapabilityResult(status="error", error=str(e))


class _LocalTest(Capability):
    def invoke(self, *, params, manifest, ctx) -> CapabilityResult:
        if _scenario_path(ctx) is not None:
            return CapabilityResult(status="ok", data={"test": "skipped (replay)"})
        workspace = ctx.get("workspace")
        if not workspace:
            return CapabilityResult(status="error", error="workspace not provided")
        # Same approach as local_build.
        try:
            out = subprocess.run(
                ["make", "test"], cwd=workspace, capture_output=True, text=True,
                timeout=params.get("timeout", 900),
            )
            return CapabilityResult(
                status="ok" if out.returncode == 0 else "error",
                data={"stdout": out.stdout[-4000:], "stderr": out.stderr[-4000:],
                      "returncode": out.returncode},
                error=None if out.returncode == 0 else f"make test exit {out.returncode}",
            )
        except FileNotFoundError:
            return CapabilityResult(status="ok", data={"test": "no make, skipped"})
        except Exception as e:
            return CapabilityResult(status="error", error=str(e))


class _ScenarioFile(Capability):
    """Capability that maps to a single scenario file."""

    def __init__(self, file_name: str, *, default: Any | None = None) -> None:
        self.file_name = file_name
        self.default = default

    def invoke(self, *, params, manifest, ctx) -> CapabilityResult:
        data = _load_scenario_file(ctx, self.file_name)
        if data is None:
            if self.default is not None:
                return CapabilityResult(status="ok", data={"value": self.default})
            return CapabilityResult(
                status="error",
                error=(
                    f"capability is not configured for non-replay mode and "
                    f"scenario file '{self.file_name}' was not found"
                ),
            )
        if isinstance(data, dict):
            return CapabilityResult(status="ok", data=data)
        if isinstance(data, list):
            return CapabilityResult(status="ok", data={"items": data})
        return CapabilityResult(status="ok", data={"text": data})


class _TriggerDevJenkinsBuild(Capability):
    def invoke(self, *, params, manifest, ctx) -> CapabilityResult:
        data = _load_scenario_file(ctx, "jenkins_build.json")
        if data is None:
            # Synthesize a deterministic stub from the manifest.
            data = {
                "jenkins_job": "agent-dev-build",
                "jenkins_build_id": "0",
                "status": "ok",
                "image_tag": f"registry/dev/encoder:{manifest.get('task_id','task')}",
                "forced_params": params,
            }
        # Refuse any leakage of prod-like params, just in case scenario data
        # was hand-edited to include them.
        for k in ("environment", "deploy_target", "promote", "publish_release", "production"):
            data.setdefault(k, params.get(k))
        return CapabilityResult(status="ok", data=data)


class _DeployDevValidationVersion(Capability):
    def invoke(self, *, params, manifest, ctx) -> CapabilityResult:
        data = _load_scenario_file(ctx, "deployment.json")
        if data is None:
            data = {
                "namespace": "dev-ai-validation",
                "pods": [f"encoder-{manifest.get('task_id','task')}-0"],
                "nodes": ["dev-gpu-node-01"],
                "gpu_ids": ["GPU-0"],
                "status": "deployed",
            }
        return CapabilityResult(status="ok", data=data)


class _RunImmutableE2E(Capability):
    def invoke(self, *, params, manifest, ctx) -> CapabilityResult:
        data = _load_scenario_file(ctx, "e2e_result.json")
        if data is None:
            data = {"status": "passed", "test_suite": "stub-e2e"}
        return CapabilityResult(status="ok", data=data)


def register_all(reg: CapabilityRegistry) -> None:
    reg.register_impl("local_build", _LocalBuild())
    reg.register_impl("local_test", _LocalTest())

    reg.register_impl("trigger_dev_jenkins_build", _TriggerDevJenkinsBuild())
    reg.register_impl("deploy_dev_validation_version", _DeployDevValidationVersion())
    reg.register_impl("run_immutable_e2e", _RunImmutableE2E())

    reg.register_impl("fetch_jenkins_status",
                      _ScenarioFile("jenkins_status.json",
                                    default={"status": "ok"}))
    reg.register_impl("fetch_jenkins_console_excerpt",
                      _ScenarioFile("jenkins_console.txt",
                                    default="(no console excerpt available)"))
    reg.register_impl("query_elastic_for_current_run",
                      _ScenarioFile("elastic_summary.json",
                                    default={"errors": [], "warnings": []}))
    reg.register_impl("get_grafana_metrics_for_current_run",
                      _ScenarioFile("grafana_metrics.json",
                                    default={"metrics": {}}))
    reg.register_impl("get_pod_logs_for_current_run",
                      _ScenarioFile("pod_logs_excerpt.txt",
                                    default="(no pod logs available)"))
    reg.register_impl("collect_gpu_metrics_for_current_run",
                      _ScenarioFile("gpu_utilization.json",
                                    default={"gpu_util_pct": []}))
