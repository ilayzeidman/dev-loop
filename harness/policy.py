"""Loop policy and convergence control."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopPolicy:
    max_code_iterations: int = 5
    max_validation_attempts_per_iteration: int = 2
    max_diagnostic_rounds_per_failure: int = 3
    max_total_wall_clock_minutes: int = 120
    allow_code_change_on_confidence: tuple[str, ...] = ("high", "medium")
    low_confidence_actions: tuple[str, ...] = (
        "request_more_diagnostics",
        "rerun_same_code",
        "ask_human",
        "stop_inconclusive",
    )


@dataclass
class LoopState:
    started_at: float = field(default_factory=time.monotonic)
    code_iterations_done: int = 0
    failure_fingerprints: list[str] = field(default_factory=list)

    def wall_clock_minutes(self) -> float:
        return (time.monotonic() - self.started_at) / 60.0


def fingerprint_failure(e2e_result: dict[str, Any]) -> str:
    """Stable string identifying a failure for "same failure twice" detection."""
    parts = [
        str(e2e_result.get("status", "")),
        str(e2e_result.get("failure_class", "")),
        str(e2e_result.get("first_error", "")),
        str(e2e_result.get("failed_test", "")),
    ]
    return "|".join(parts)


def check_stop_conditions(
    *,
    policy: LoopPolicy,
    state: LoopState,
    last_triage: dict[str, Any] | None,
) -> str | None:
    """Return a stop reason if the loop must terminate, else None."""
    if state.wall_clock_minutes() >= policy.max_total_wall_clock_minutes:
        return "budget_exceeded"
    if state.code_iterations_done >= policy.max_code_iterations:
        return "max_code_iterations_reached"
    # same_failure_fingerprint_after_2_code_iterations
    if len(state.failure_fingerprints) >= 2:
        last_two = state.failure_fingerprints[-2:]
        if last_two[0] == last_two[1] and last_two[0]:
            return "same_failure_fingerprint_after_2_code_iterations"
    if last_triage is not None:
        action = last_triage.get("next_action")
        if action == "ask_human":
            return "agent_requested_human"
        if action == "stop_inconclusive":
            return "agent_stopped_inconclusive"
        if action == "declare_environment_issue":
            return "environment_issue_declared"
        if action == "declare_harness_issue":
            return "harness_issue_declared"
    return None


def code_change_allowed(policy: LoopPolicy, triage: dict[str, Any]) -> bool:
    """Whether triage permits a new code iteration."""
    if triage.get("next_action") != "modify_code":
        return False
    return triage.get("confidence") in policy.allow_code_change_on_confidence
