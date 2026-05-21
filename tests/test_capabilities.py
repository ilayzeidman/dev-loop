from pathlib import Path

from harness.capabilities import load_default_registry
from harness.capabilities.base import CapabilityResult


def test_load_registry_has_expected_capabilities():
    reg = load_default_registry()
    names = set(reg.all_names())
    assert "trigger_dev_jenkins_build" in names
    assert "query_elastic_for_current_run" in names
    assert "run_immutable_e2e" in names
    # diagnostics are agent-requestable
    assert "query_elastic_for_current_run" in reg.agent_requestable()
    # trigger_dev_jenkins_build is NOT agent-requestable
    assert "trigger_dev_jenkins_build" not in reg.agent_requestable()


def test_agent_cannot_request_internal_capability():
    reg = load_default_registry()
    res = reg.invoke(
        "trigger_dev_jenkins_build",
        params={}, manifest={}, ctx={},
        from_agent=True,
    )
    assert res.status == "error"
    assert "not agent-requestable" in (res.error or "")


def test_agent_forbidden_params_rejected():
    reg = load_default_registry()
    res = reg.invoke(
        "query_elastic_for_current_run",
        params={"environment": "prod"},
        manifest={}, ctx={},
        from_agent=True,
    )
    assert res.status == "error"
    assert "forbidden" in (res.error or "").lower()


def test_jenkins_forced_params_override(tmp_path: Path):
    reg = load_default_registry()
    # No replay scenario: capability uses stub data and shows forced params.
    res = reg.invoke(
        "trigger_dev_jenkins_build",
        params={"branch": "feature", "environment": "prod", "promote": True},
        manifest={"task_id": "t"}, ctx={},
        from_agent=False,
    )
    assert res.status == "ok"
    assert res.data["environment"] == "dev"
    assert res.data["promote"] is False
    assert res.data["production"] is False


def test_denied_agent_calls_are_audited():
    reg = load_default_registry()
    seen: list[dict] = []
    reg.set_audit_sink(seen.append)

    # Agent calling a non-agent-requestable capability is denied AND audited.
    reg.invoke("trigger_dev_jenkins_build", params={}, manifest={}, ctx={},
               from_agent=True)
    assert any(
        r["capability"] == "trigger_dev_jenkins_build"
        and r["from_agent"] is True
        and r["status"] == "error"
        for r in seen
    ), seen

    # Agent passing forbidden params is denied AND audited.
    reg.invoke("query_elastic_for_current_run",
               params={"environment": "prod"}, manifest={}, ctx={},
               from_agent=True)
    assert any(
        r["capability"] == "query_elastic_for_current_run"
        and r["from_agent"] is True
        and r["status"] == "error"
        and "forbidden" in (r["error"] or "").lower()
        for r in seen
    ), seen


def test_replay_capabilities_read_scenario_files(tmp_path: Path):
    sc = tmp_path / "sc"
    sc.mkdir()
    (sc / "elastic_summary.json").write_text('{"errors": [], "warnings": ["w"]}')
    reg = load_default_registry()
    res = reg.invoke(
        "query_elastic_for_current_run",
        params={"max_lines": 50}, manifest={}, ctx={"replay_scenario": str(sc)},
        from_agent=True,
    )
    assert res.status == "ok"
    assert res.data["warnings"] == ["w"]


def test_audit_redacts_error_field():
    """A capability ``error`` string can carry secrets (an upstream system
    might echo back an Authorization header on failure). The audit record
    is persisted to disk and must not preserve that secret verbatim."""
    from harness.capabilities.base import Capability
    from harness.capabilities.registry import CapabilityRegistry, CapabilitySpec

    class _Leaky(Capability):
        def invoke(self, *, params, manifest, ctx):
            return CapabilityResult(
                status="error",
                error="upstream failed: Authorization: Bearer ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )

    reg = CapabilityRegistry()
    reg._specs["x"] = CapabilitySpec(
        name="x", category="local_only", agent_requestable=False,
        timeout_seconds=60, uses_run_manifest=False, redacts_output=True,
        audit=True, prod_possible=False, forced_params={}, impl=_Leaky(),
    )
    seen: list[dict] = []
    reg.set_audit_sink(seen.append)
    reg.invoke("x", params={}, manifest={}, ctx={})
    assert seen, "audit record should have been emitted"
    err = seen[0]["error"]
    assert "ghp_" not in err
    assert "[REDACTED]" in err


def test_soft_timeout_warning_tolerates_non_dict_data():
    """A capability impl that returns ``data=None`` (e.g. ``CapabilityResult(
    status='error', data=None, error=...)``) must not crash the registry's
    soft-timeout branch."""
    import time as _time
    from harness.capabilities.base import Capability
    from harness.capabilities.registry import CapabilityRegistry, CapabilitySpec

    class _Slow(Capability):
        def invoke(self, *, params, manifest, ctx):
            # Force the soft-timeout branch via a 0-second budget.
            _time.sleep(0.01)
            return CapabilityResult(status="error", data=None, error="boom")

    reg = CapabilityRegistry()
    reg._specs["x"] = CapabilitySpec(
        name="x", category="local_only", agent_requestable=False,
        timeout_seconds=0, uses_run_manifest=False, redacts_output=False,
        audit=False, prod_possible=False, forced_params={}, impl=_Slow(),
    )
    res = reg.invoke("x", params={}, manifest={}, ctx={})
    # Should have produced the warning without crashing.
    assert res.status == "error"
    assert isinstance(res.data, dict)
    assert any("soft timeout" in w for w in res.data.get("_warnings", []))
