"""Codex runner adapter.

Thin adapter over :class:`CliAgentRunner` symmetric to the Claude adapter.
"""

from __future__ import annotations

from .cli_base import CliAgentRunner


class CodexRunner(CliAgentRunner):
    provider_name = "codex"
    provider_label = "codex"
    env_cli_path = "CODEX_CLI_PATH"
    env_model = "CODEX_MODEL"
    default_model = "gpt-5-codex"

    def build_argv(self) -> list[str]:
        assert self.cli_path is not None
        return [self.cli_path, "exec", "--model", self.model]
