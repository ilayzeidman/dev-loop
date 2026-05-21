"""Agent runner interface (provider-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class AgentPhase(str, Enum):
    TASK_CONTRACT = "task_contract"
    IMPLEMENTATION = "implementation"
    FAILURE_TRIAGE = "failure_triage"


@dataclass
class AgentPhaseResult:
    """Result of a single agent phase call."""

    output: dict[str, Any]
    raw_log: str = ""
    workspace_after: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRunner(Protocol):
    """A runner adapter for one LLM provider.

    Implementations:
      - ReplayAgentRunner: reads a scenario directory.
      - ClaudeCodeRunner / CodexRunner: invoke the respective CLI.
    """

    provider_name: str

    def profile(self) -> dict[str, Any]: ...

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
    ) -> AgentPhaseResult: ...
