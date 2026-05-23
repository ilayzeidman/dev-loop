"""Tests for the shared CLI agent runner base.

Exercises ClaudeCodeRunner / CodexRunner via stub shell-script CLIs that
deterministically emit canned JSON, errors, or non-zero exits. This lets
us cover prompt assembly, output parsing, error reporting, and metadata
without needing the real provider CLIs installed.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from harness.agents.base import AgentPhase
from harness.agents.cli_base import extract_json
from harness.agents.claude_runner import ClaudeCodeRunner
from harness.agents.codex_runner import CodexRunner


def _make_stub(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make_workspace(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=repo, check=True)
    return repo


def test_extract_json_picks_fenced_block():
    text = (
        "some chatter before\n"
        "```json\n"
        '{"type": "task_contract", "implementation_goal": "x"}\n'
        "```\n"
        "trailing prose\n"
    )
    obj = extract_json(text, expected_type="task_contract")
    assert obj["implementation_goal"] == "x"


def test_extract_json_skips_wrong_type():
    text = (
        '```json\n{"type": "other_thing", "x": 1}\n```\n'
        '```json\n{"type": "task_contract", "implementation_goal": "y"}\n```\n'
    )
    obj = extract_json(text, expected_type="task_contract")
    assert obj["implementation_goal"] == "y"


def test_extract_json_falls_back_to_raw_text():
    text = '   {"type": "task_contract", "implementation_goal": "z"}   '
    obj = extract_json(text, expected_type="task_contract")
    assert obj["implementation_goal"] == "z"


def test_extract_json_raises_when_missing():
    with pytest.raises(ValueError):
        extract_json("no json here", expected_type="task_contract")


def test_claude_runner_missing_cli_raises():
    r = ClaudeCodeRunner(cli_path=None)
    r.cli_path = None  # simulate not found on PATH
    with pytest.raises(RuntimeError, match="CLI not found"):
        r.run_phase(
            AgentPhase.TASK_CONTRACT,
            workspace_path=Path("."),
            task_contract=None,
            run_manifest=None,
            input_bundle={},
            output_schema_name="task_contract.v1.json",
            budget_seconds=5,
        )


def test_claude_runner_happy_path_returns_parsed_output(tmp_path: Path):
    workspace = _make_workspace(tmp_path)
    stub = _make_stub(
        tmp_path / "bin" / "claude",
        'cat > /dev/null\n'
        'echo \'```json\'\n'
        'echo \'{"type": "task_contract", "implementation_goal": "from-stub",'
        ' "assumptions": [], "success_criteria": ["x"], "non_goals": [],'
        ' "likely_components": [], "validation_plan": ["v"], "ambiguities": [],'
        ' "can_start_without_human": true}\'\n'
        'echo \'```\'\n',
    )

    r = ClaudeCodeRunner(cli_path=str(stub), model="test-model")
    result = r.run_phase(
        AgentPhase.TASK_CONTRACT,
        workspace_path=workspace,
        task_contract=None,
        run_manifest=None,
        input_bundle={"original_request": "do the thing"},
        output_schema_name="task_contract.v1.json",
        budget_seconds=10,
    )
    assert result.output["type"] == "task_contract"
    assert result.output["implementation_goal"] == "from-stub"
    assert result.metadata["provider"] == "claude"
    assert result.metadata["returncode"] == 0
    assert "argv" in result.metadata
    assert "--model" in result.metadata["argv"]
    assert "test-model" in result.metadata["argv"]
    prompt_dump = workspace / ".harness" / "prompt-task_contract.md"
    assert prompt_dump.exists()
    text = prompt_dump.read_text(encoding="utf-8")
    assert "Trusted instructions" in text
    assert "Untrusted evidence" in text
    assert "do the thing" in text


def test_claude_runner_nonzero_exit_surfaces_stderr(tmp_path: Path):
    workspace = _make_workspace(tmp_path)
    stub = _make_stub(
        tmp_path / "bin" / "claude",
        'cat > /dev/null\n'
        'echo "boom: auth required" >&2\n'
        'exit 7\n',
    )
    r = ClaudeCodeRunner(cli_path=str(stub), model="m")
    with pytest.raises(RuntimeError) as excinfo:
        r.run_phase(
            AgentPhase.TASK_CONTRACT,
            workspace_path=workspace,
            task_contract=None,
            run_manifest=None,
            input_bundle={},
            output_schema_name="task_contract.v1.json",
            budget_seconds=10,
        )
    msg = str(excinfo.value)
    assert "exited with code 7" in msg
    assert "boom: auth required" in msg


def test_claude_runner_unparseable_output_surfaces_stderr(tmp_path: Path):
    workspace = _make_workspace(tmp_path)
    stub = _make_stub(
        tmp_path / "bin" / "claude",
        'cat > /dev/null\n'
        'echo "no json today"\n'
        'echo "warning: model degraded" >&2\n',
    )
    r = ClaudeCodeRunner(cli_path=str(stub), model="m")
    with pytest.raises(RuntimeError) as excinfo:
        r.run_phase(
            AgentPhase.IMPLEMENTATION,
            workspace_path=workspace,
            task_contract={"type": "task_contract", "implementation_goal": "x"},
            run_manifest=None,
            input_bundle={},
            output_schema_name="implementation_result.v1.json",
            budget_seconds=10,
        )
    msg = str(excinfo.value)
    assert "failed to parse claude output" in msg
    assert "warning: model degraded" in msg


def test_codex_runner_uses_exec_subcommand(tmp_path: Path):
    workspace = _make_workspace(tmp_path)
    stub = _make_stub(
        tmp_path / "bin" / "codex",
        'echo "argv: $@" >&2\n'
        'cat > /dev/null\n'
        'echo \'{"type": "failure_triage", "failure_class": "code_suspected",'
        ' "confidence": "medium", "next_action": "modify_code",'
        ' "hypothesis": "x", "expected_effect": "y", "evidence_refs": [],'
        ' "requested_diagnostics": [], "human_reason": null}\'\n',
    )
    r = CodexRunner(cli_path=str(stub), model="cx")
    result = r.run_phase(
        AgentPhase.FAILURE_TRIAGE,
        workspace_path=workspace,
        task_contract=None,
        run_manifest={"task_id": "t"},
        input_bundle={"failure": "e2e"},
        output_schema_name="failure_triage.v1.json",
        budget_seconds=10,
    )
    assert result.output["type"] == "failure_triage"
    assert result.metadata["provider"] == "codex"
    argv = result.metadata["argv"]
    assert argv[0] == str(stub)
    assert "exec" in argv
    assert "cx" in argv
    assert "exec --model cx" in result.metadata["stderr_tail"]


def test_runner_picks_up_cli_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stub = _make_stub(tmp_path / "bin" / "claude", 'true\n')
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(stub))
    monkeypatch.setenv("CLAUDE_MODEL", "env-model")
    r = ClaudeCodeRunner()
    assert r.cli_path == str(stub)
    assert r.model == "env-model"
    prof = r.profile()
    assert prof["provider"] == "claude"
    assert prof["model"] == "env-model"
