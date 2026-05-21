"""Failure dossier construction and triage helpers."""

from __future__ import annotations

from typing import Any

from .redaction import redact


def build_failure_dossier(
    *,
    run_manifest: dict[str, Any],
    e2e_result: dict[str, Any],
    jenkins_result: dict[str, Any] | None,
    deployment: dict[str, Any] | None,
    elastic_summary: dict[str, Any] | None,
    grafana_metrics: dict[str, Any] | None,
    pod_logs_excerpt: str | None,
    gpu_utilization: dict[str, Any] | None,
    diff_summary: dict[str, Any],
    previous_iteration_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a sanitized, bounded failure dossier for the agent."""
    dossier = {
        "run_manifest": run_manifest,
        "e2e_failure_summary": {
            "status": e2e_result.get("status"),
            "first_error": e2e_result.get("first_error"),
            "failed_test": e2e_result.get("failed_test"),
            "duration_seconds": e2e_result.get("duration_seconds"),
        },
        "jenkins_result": jenkins_result,
        "deployment": deployment,
        "pod_status": (deployment or {}).get("pod_status"),
        "container_restart_counts": (deployment or {}).get("restart_counts"),
        "pod_logs_excerpt": _bound_text(pod_logs_excerpt, 200),
        "elastic_summary": elastic_summary,
        "grafana_summary": grafana_metrics,
        "gpu_utilization_summary": gpu_utilization,
        "previous_iteration": previous_iteration_summary,
        "diff_summary": diff_summary,
    }
    return redact(dossier)


def _bound_text(text: str | None, max_lines: int) -> str | None:
    if text is None:
        return None
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    half = max_lines // 2
    head = lines[:half]
    tail = lines[-half:]
    return "\n".join(head + [f"... [{len(lines) - max_lines} lines omitted] ..."] + tail)
