"""Capability base class."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class CapabilityResult:
    status: str  # "ok" | "error" | "timeout"
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Capability(Protocol):
    """Anything callable by the harness through the registry.

    ``invoke`` receives:
      - ``params``: agent-provided fields if applicable (validated by
        ``input_schema`` at the registry layer).
      - ``manifest``: the current run manifest, from which derived params
        (pod, namespace, version, device_id, time window) are read.
      - ``ctx``: harness-side context (paths, replay scenario, etc).
    """

    def invoke(
        self,
        *,
        params: dict[str, Any],
        manifest: dict[str, Any],
        ctx: dict[str, Any],
    ) -> CapabilityResult: ...
