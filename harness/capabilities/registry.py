"""Capability registry.

Loads ``registry.yaml``, maps each capability name to an implementation,
and enforces:

  - agent_requestable check
  - timeout
  - forced parameters (e.g. environment=dev)
  - output redaction
  - audit logging
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from ..redaction import redact
from ..util import utc_now_iso, write_json
from .base import Capability, CapabilityResult

REGISTRY_YAML = Path(__file__).parent / "registry.yaml"


@dataclass
class CapabilitySpec:
    name: str
    category: str
    agent_requestable: bool
    timeout_seconds: int
    uses_run_manifest: bool
    redacts_output: bool
    audit: bool
    prod_possible: bool
    forced_params: dict[str, Any]
    impl: Capability | None = None


_FORBIDDEN_FIELDS_FOR_AGENT = {
    "environment",
    "env",
    "deploy_target",
    "promote",
    "publish_release",
    "production",
    "prod",
    "release",
    "target",
}


class CapabilityRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        self._audit_sink: Callable[[dict[str, Any]], None] | None = None

    @classmethod
    def from_yaml(cls, path: Path = REGISTRY_YAML) -> "CapabilityRegistry":
        reg = cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for c in data.get("capabilities", []):
            spec = CapabilitySpec(
                name=c["name"],
                category=c["category"],
                agent_requestable=bool(c.get("agent_requestable", False)),
                timeout_seconds=int(c.get("timeout_seconds", 60)),
                uses_run_manifest=bool(c.get("uses_run_manifest", False)),
                redacts_output=bool(c.get("redacts_output", True)),
                audit=bool(c.get("audit", True)),
                prod_possible=bool(c.get("prod_possible", False)),
                forced_params=dict(c.get("forced_params", {})),
            )
            reg._specs[spec.name] = spec
        return reg

    # registration ------------------------------------------------------

    def register_impl(self, name: str, impl: Capability) -> None:
        if name not in self._specs:
            raise ValueError(f"capability not declared in registry.yaml: {name}")
        self._specs[name].impl = impl

    def set_audit_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        self._audit_sink = sink

    # introspection -----------------------------------------------------

    def spec(self, name: str) -> CapabilitySpec:
        if name not in self._specs:
            raise KeyError(f"unknown capability: {name}")
        return self._specs[name]

    def agent_requestable(self) -> list[str]:
        return [s.name for s in self._specs.values() if s.agent_requestable]

    def all_names(self) -> list[str]:
        return list(self._specs.keys())

    # invocation --------------------------------------------------------

    def invoke(
        self,
        name: str,
        *,
        params: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
        from_agent: bool = False,
    ) -> CapabilityResult:
        spec = self.spec(name)
        params = dict(params or {})
        manifest = manifest or {}
        ctx = ctx or {}

        if from_agent and not spec.agent_requestable:
            return CapabilityResult(
                status="error",
                error=f"capability '{name}' is not agent-requestable",
            )

        if from_agent:
            forbidden = _FORBIDDEN_FIELDS_FOR_AGENT.intersection(params.keys())
            if forbidden:
                return CapabilityResult(
                    status="error",
                    error=(
                        f"agent attempted to set forbidden params: "
                        f"{sorted(forbidden)}"
                    ),
                )

        # forced params override anything else; this is the dev-only safety
        # net for Jenkins and similar.
        params.update(spec.forced_params)

        if spec.impl is None:
            return CapabilityResult(
                status="error",
                error=f"no implementation registered for capability '{name}'",
            )

        started = time.monotonic()
        try:
            result = spec.impl.invoke(
                params=params, manifest=manifest, ctx=ctx,
            )
        except Exception as exc:  # capability bugs must not crash harness
            result = CapabilityResult(
                status="error", error=f"{type(exc).__name__}: {exc}",
            )
        duration = time.monotonic() - started

        if duration > spec.timeout_seconds:
            # Soft-timeout note. We don't kill threads; we just flag it.
            result.data.setdefault("_warnings", []).append(
                f"exceeded soft timeout of {spec.timeout_seconds}s ({duration:.1f}s)"
            )

        if spec.redacts_output and result.status == "ok":
            result.data = redact(result.data)

        if spec.audit:
            self._audit(name=name, params=params, result=result, from_agent=from_agent)

        return result

    def _audit(
        self,
        *,
        name: str,
        params: dict[str, Any],
        result: CapabilityResult,
        from_agent: bool,
    ) -> None:
        record = {
            "ts_utc": utc_now_iso(),
            "capability": name,
            "from_agent": from_agent,
            "params": redact(params),
            "status": result.status,
            "error": result.error,
        }
        if self._audit_sink is not None:
            self._audit_sink(record)


def load_default_registry(impls_module: str = "harness.capabilities.impls") -> CapabilityRegistry:
    """Load the registry from yaml and bind built-in implementations."""
    from . import impls
    reg = CapabilityRegistry.from_yaml()
    impls.register_all(reg)
    return reg


def audit_to_jsonl(path: Path) -> Callable[[dict[str, Any]], None]:
    """Return an audit sink that appends JSONL records to ``path``."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    def sink(rec: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True))
            f.write("\n")
    return sink
