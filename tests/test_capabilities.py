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
