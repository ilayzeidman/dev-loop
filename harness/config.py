"""Per-repository configuration.

The harness reads ``.dev-loop/config.yaml`` from the target repository so
that the same ``dev-loop implement`` command works across repos with their
own settings (provider, sandbox dir, policy overrides, scenario dirs).

Every field has a default; the simplest config is an empty file (or no
file at all).

Lookup order:

  1. ``--config <path>`` CLI flag.
  2. ``$REPO/.dev-loop/config.yaml``.
  3. ``$REPO/.dev-loop/config.yml``.
  4. Defaults.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .policy import LoopPolicy

CONFIG_DIR_NAME = ".dev-loop"
CONFIG_FILE_NAME = "config.yaml"


@dataclass
class HarnessConfig:
    """Resolved per-repo configuration."""

    # Where to write run artifacts. Relative paths resolve from the repo
    # root. Defaults to ``.dev-loop/runs/``.
    runs_dir: str = ".dev-loop/runs"

    # Sandbox & clean workspace dirs. They live under the system temp dir
    # by default so they don't pollute the repo.
    sandbox_dir: str = ""        # empty -> derived from system tempdir
    clean_workspace_dir: str = ""  # empty -> derived from system tempdir

    # Default provider. Override via CLI ``--provider``.
    default_provider: str = "replay"

    # Replay scenarios directory (relative to repo root). Used when
    # provider is ``replay`` and ``--replay-scenario`` is just a name.
    scenarios_dir: str = "scenarios"

    # Loop policy. Overrides via top-level keys.
    max_code_iterations: int = 5
    max_validation_attempts_per_iteration: int = 2
    max_diagnostic_rounds_per_failure: int = 3
    max_total_wall_clock_minutes: int = 120

    # Free-form metadata surfaced in the harness profile.
    notes: str = ""

    def policy(self) -> LoopPolicy:
        return LoopPolicy(
            max_code_iterations=self.max_code_iterations,
            max_validation_attempts_per_iteration=self.max_validation_attempts_per_iteration,
            max_diagnostic_rounds_per_failure=self.max_diagnostic_rounds_per_failure,
            max_total_wall_clock_minutes=self.max_total_wall_clock_minutes,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HarnessConfig":
        # Don't mutate the caller's dict.
        raw = dict(raw)
        valid = {f.name for f in fields(cls)}
        # Allow ``policy:`` nested form too.
        policy_block = raw.pop("policy", None) or {}
        filtered = {k: v for k, v in raw.items() if k in valid}
        for k, v in policy_block.items():
            if k in valid:
                filtered[k] = v
        unknown = (set(raw) - valid)
        if unknown:
            # Don't crash on unknown keys; surface them via notes so users
            # see typos without losing the run.
            extra = filtered.get("notes", "")
            filtered["notes"] = (extra + f" [unknown keys ignored: {sorted(unknown)}]").strip()

        # Type-check int fields so a typo like ``max_code_iterations: many``
        # surfaces here instead of crashing later inside the orchestrator
        # loop with an opaque TypeError.
        type_errors: list[str] = []
        for fname in (
            "max_code_iterations",
            "max_validation_attempts_per_iteration",
            "max_diagnostic_rounds_per_failure",
            "max_total_wall_clock_minutes",
        ):
            if fname in filtered and not isinstance(filtered[fname], bool) and not isinstance(filtered[fname], int):
                # YAML may parse a quoted number as a string; try coercion.
                try:
                    filtered[fname] = int(filtered[fname])
                except (TypeError, ValueError):
                    type_errors.append(f"{fname} must be int, got {filtered[fname]!r}")
                    filtered.pop(fname)
        if type_errors:
            extra = filtered.get("notes", "")
            filtered["notes"] = (extra + f" [config type errors: {type_errors}]").strip()
        return cls(**filtered)

    @classmethod
    def load(cls, *, repo_root: Path, explicit_path: Path | None = None) -> "HarnessConfig":
        path = _resolve_config_path(repo_root, explicit_path)
        if path is None or not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a YAML mapping at the top level")
        return cls.from_dict(raw)

    def resolved(self, repo_root: Path) -> "ResolvedConfig":
        import tempfile

        runs_dir = (repo_root / self.runs_dir).resolve()
        sandbox_dir = (
            Path(self.sandbox_dir).expanduser().resolve()
            if self.sandbox_dir
            else Path(tempfile.gettempdir()) / "dev-loop-sandbox" / repo_root.name
        )
        clean_workspace_dir = (
            Path(self.clean_workspace_dir).expanduser().resolve()
            if self.clean_workspace_dir
            else Path(tempfile.gettempdir()) / "dev-loop-clean" / repo_root.name
        )
        scenarios_dir = (repo_root / self.scenarios_dir).resolve()
        return ResolvedConfig(
            runs_dir=runs_dir,
            sandbox_dir=sandbox_dir,
            clean_workspace_dir=clean_workspace_dir,
            scenarios_dir=scenarios_dir,
            default_provider=self.default_provider,
            policy=self.policy(),
            raw=self,
        )


@dataclass
class ResolvedConfig:
    runs_dir: Path
    sandbox_dir: Path
    clean_workspace_dir: Path
    scenarios_dir: Path
    default_provider: str
    policy: LoopPolicy
    raw: HarnessConfig


def _resolve_config_path(repo_root: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    cd = repo_root / CONFIG_DIR_NAME
    for name in (CONFIG_FILE_NAME, "config.yml"):
        p = cd / name
        if p.exists():
            return p
    return None


DEFAULT_CONFIG_YAML = """# dev-loop configuration for this repository.
#
# Every field has a default and is optional. Delete keys you don't want to
# override. Run `dev-loop config show` to see the resolved values.

# Where run artifacts go (per-task subdir).
runs_dir: .dev-loop/runs

# Provider used when --provider is not passed. One of: replay, claude, codex.
default_provider: replay

# Where replay scenarios live (relative to repo root).
scenarios_dir: scenarios

# Loop policy. Defaults shown; uncomment to override.
# policy:
#   max_code_iterations: 5
#   max_validation_attempts_per_iteration: 2
#   max_diagnostic_rounds_per_failure: 3
#   max_total_wall_clock_minutes: 120
"""

GITIGNORE_LINES = """# dev-loop
runs/
.dev-loop/runs/
"""

# A tiny self-contained replay scenario for first-run onboarding. Installed
# into ``<repo>/<scenarios_dir>/`` by ``dev-loop init --starter`` or the UI
# onboarding panel. Doesn't reference any external infrastructure so a brand
# new user can hit "Try a demo run" and see a green E2E within seconds.
STARTER_SCENARIO_NAME = "hello-dev-loop"
STARTER_SCENARIO_FILES: dict[str, str] = {
    "task_request.md": (
        "# Hello, dev-loop\n\n"
        "This is the starter scenario. The agent (in replay mode) will\n"
        "pretend to implement a tiny change and the harness will report a\n"
        "passing E2E. Use it to verify your setup before pointing dev-loop\n"
        "at a real task.\n"
    ),
    "task_contract.json": (
        '{\n'
        '  "type": "task_contract",\n'
        '  "implementation_goal": "Demonstrate a clean end-to-end run with the replay provider.",\n'
        '  "assumptions": ["no external infrastructure is needed for this demo"],\n'
        '  "success_criteria": ["hello-dev-loop-e2e reports passed"],\n'
        '  "non_goals": ["touching production code"],\n'
        '  "likely_components": [],\n'
        '  "validation_plan": ["replay-only E2E"],\n'
        '  "ambiguities": [],\n'
        '  "can_start_without_human": true\n'
        '}\n'
    ),
    "implementation_result.json": (
        '{\n'
        '  "type": "implementation_result",\n'
        '  "summary": "Demo implementation — no real code change.",\n'
        '  "hypothesis": "If the harness can replay this scenario it is wired up correctly.",\n'
        '  "confidence": "high",\n'
        '  "expected_validation": ["hello-dev-loop-e2e passes"],\n'
        '  "risk_notes": [],\n'
        '  "claimed_changed_files": []\n'
        '}\n'
    ),
    "e2e_result.json": (
        '{\n'
        '  "status": "passed",\n'
        '  "test_suite": "hello-dev-loop-e2e",\n'
        '  "duration_seconds": 1\n'
        '}\n'
    ),
}


def write_starter_scenario(scenarios_dir: Path) -> Path:
    """Write the bundled starter scenario into ``scenarios_dir``.

    Idempotent: if the directory already exists, missing files are added
    but existing files are left alone. Returns the scenario directory.
    """
    dest = scenarios_dir / STARTER_SCENARIO_NAME
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in STARTER_SCENARIO_FILES.items():
        f = dest / name
        if not f.exists():
            f.write_text(content, encoding="utf-8")
    return dest


def append_gitignore(repo_root: Path) -> bool:
    """Ensure dev-loop ledger paths are gitignored. Returns True if the
    file was created or modified."""
    gi = repo_root / ".gitignore"
    if gi.exists():
        existing = gi.read_text(encoding="utf-8")
        if ".dev-loop/runs/" in existing:
            return False
        with gi.open("a", encoding="utf-8") as f:
            if not existing.endswith("\n"):
                f.write("\n")
            f.write(GITIGNORE_LINES)
        return True
    gi.write_text(GITIGNORE_LINES, encoding="utf-8")
    return True


def write_default_config(repo_root: Path, *, force: bool = False) -> tuple[Path, bool]:
    """Scaffold ``.dev-loop/config.yaml``. Returns ``(path, created)``."""
    cd = repo_root / CONFIG_DIR_NAME
    cd.mkdir(parents=True, exist_ok=True)
    cf = cd / CONFIG_FILE_NAME
    if cf.exists() and not force:
        return cf, False
    cf.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    return cf, True
