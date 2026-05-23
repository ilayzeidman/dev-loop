"""Claude Code runner adapter.

Thin adapter over :class:`CliAgentRunner` that knows the ``claude`` CLI's
headless invocation flags. All prompt assembly, subprocess handling, output
parsing, and error reporting live in the shared base.
"""

from __future__ import annotations

from .cli_base import CliAgentRunner


class ClaudeCodeRunner(CliAgentRunner):
    provider_name = "claude"
    provider_label = "claude"
    env_cli_path = "CLAUDE_CLI_PATH"
    env_model = "CLAUDE_MODEL"
    default_model = "claude-sonnet-4-6"

    def build_argv(self) -> list[str]:
        assert self.cli_path is not None
        return [
            self.cli_path,
            "--print",
            "--model", self.model,
            "--max-turns", str(self.max_turns),
        ]
