"""Runner factory."""

from __future__ import annotations

from pathlib import Path

from .base import AgentRunner


def create_runner(
    provider: str,
    *,
    replay_scenario: Path | str | None = None,
) -> AgentRunner:
    provider = provider.lower()
    if provider == "replay":
        from .replay_runner import ReplayAgentRunner
        if not replay_scenario:
            raise ValueError("replay provider requires --replay-scenario")
        return ReplayAgentRunner(Path(replay_scenario))
    if provider in ("claude", "claude_code"):
        from .claude_runner import ClaudeCodeRunner
        return ClaudeCodeRunner()
    if provider == "codex":
        from .codex_runner import CodexRunner
        return CodexRunner()
    raise ValueError(f"unknown provider: {provider}")
