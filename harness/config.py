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
        cfg, _ = cls.from_dict_with_issues(raw)
        return cfg

    @classmethod
    def from_dict_with_issues(
        cls, raw: dict[str, Any]
    ) -> tuple["HarnessConfig", list[dict[str, str]]]:
        """Like ``from_dict`` but also returns a structured issues list.

        Each issue is ``{"level": "warning"|"error", "field": str,
        "message": str}``. ``error`` entries indicate something was
        dropped/coerced; ``warning`` entries are advisory (unknown keys,
        unusual values).
        """
        # Don't mutate the caller's dict.
        raw = dict(raw)
        valid = {f.name for f in fields(cls)}
        issues: list[dict[str, str]] = []
        # Allow ``policy:`` nested form too.
        policy_block = raw.pop("policy", None) or {}
        if policy_block and not isinstance(policy_block, dict):
            issues.append({
                "level": "error", "field": "policy",
                "message": f"policy must be a mapping, got {type(policy_block).__name__}",
            })
            policy_block = {}
        filtered = {k: v for k, v in raw.items() if k in valid}
        for k, v in policy_block.items():
            if k in valid:
                filtered[k] = v
            else:
                issues.append({
                    "level": "warning", "field": f"policy.{k}",
                    "message": f"unknown policy key '{k}' ignored",
                })
        unknown = (set(raw) - valid)
        if unknown:
            # Don't crash on unknown keys; surface them via notes so users
            # see typos without losing the run.
            extra = filtered.get("notes", "")
            filtered["notes"] = (extra + f" [unknown keys ignored: {sorted(unknown)}]").strip()
            for k in sorted(unknown):
                issues.append({
                    "level": "warning", "field": k,
                    "message": f"unknown top-level key '{k}' ignored",
                })

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
                    issues.append({
                        "level": "error", "field": fname,
                        "message": f"must be an integer, got {filtered[fname]!r}",
                    })
                    filtered.pop(fname)
        if type_errors:
            extra = filtered.get("notes", "")
            filtered["notes"] = (extra + f" [config type errors: {type_errors}]").strip()
        # Range sanity warnings — not errors, just things that suggest a typo.
        bounds = {
            "max_code_iterations": (1, 50),
            "max_validation_attempts_per_iteration": (1, 20),
            "max_diagnostic_rounds_per_failure": (1, 20),
            "max_total_wall_clock_minutes": (1, 24 * 60),
        }
        for fname, (lo, hi) in bounds.items():
            val = filtered.get(fname)
            if isinstance(val, int) and not isinstance(val, bool):
                if val < lo:
                    issues.append({
                        "level": "error", "field": fname,
                        "message": f"must be at least {lo}",
                    })
                elif val > hi:
                    issues.append({
                        "level": "warning", "field": fname,
                        "message": f"unusually large ({val} > {hi}); double-check this is intentional",
                    })
        if "default_provider" in filtered:
            prov = filtered["default_provider"]
            if not isinstance(prov, str):
                issues.append({
                    "level": "error", "field": "default_provider",
                    "message": f"must be a string, got {type(prov).__name__}",
                })
                filtered.pop("default_provider")
            elif prov not in ("replay", "claude", "claude_code", "codex"):
                issues.append({
                    "level": "warning", "field": "default_provider",
                    "message": f"'{prov}' is not a built-in provider (replay, claude, codex)",
                })
        return cls(**filtered), issues

    @classmethod
    def load(cls, *, repo_root: Path, explicit_path: Path | None = None) -> "HarnessConfig":
        path = _resolve_config_path(repo_root, explicit_path)
        if path is None or not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a YAML mapping at the top level")
        return cls.from_dict(raw)

    @classmethod
    def load_with_issues(
        cls, *, repo_root: Path, explicit_path: Path | None = None,
    ) -> tuple["HarnessConfig", Path | None, list[dict[str, str]]]:
        """Same as ``load`` but also returns the resolved config-file path
        and the structured issue list from ``from_dict_with_issues``.

        Returns ``(config, path_or_None, issues)``. ``path_or_None`` is
        the YAML file that was read, or ``None`` if defaults were used.
        ``issues`` is ``[]`` for a clean (or absent) config.
        """
        path = _resolve_config_path(repo_root, explicit_path)
        if path is None or not path.exists():
            return cls(), None, []
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return cls(), path, [{
                "level": "error", "field": "<root>",
                "message": (
                    f"{path} must contain a YAML mapping at the top level; "
                    f"got {type(raw).__name__}"
                ),
            }]
        cfg, issues = cls.from_dict_with_issues(raw)
        return cfg, path, issues

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


def dump_canonical_yaml(cfg: "HarnessConfig", *, include_defaults: bool = False) -> str:
    """Render a config as a tidy, commented, hand-editable YAML document.

    Used by the structured Config form in the UI to produce a YAML preview
    after the user has filled in the form. Output is stable so the round
    trip form -> yaml -> form is idempotent.

    When ``include_defaults`` is False (the default), fields equal to their
    dataclass defaults are commented out so the saved file stays minimal.
    """
    defaults = HarnessConfig()
    lines: list[str] = ["# dev-loop configuration for this repository."]
    lines.append("# Generated by the Build > Config form. Hand-edits are preserved.")
    lines.append("")

    def emit(field_name: str, value: Any, comment: str, *, quote_strings: bool = False) -> None:
        is_default = getattr(defaults, field_name) == value
        prefix = "# " if (is_default and not include_defaults) else ""
        rendered = _yaml_scalar(value, quote_strings=quote_strings)
        if comment:
            lines.append(f"# {comment}")
        lines.append(f"{prefix}{field_name}: {rendered}")
        lines.append("")

    emit("runs_dir", cfg.runs_dir,
         "Where run artifacts go (per-task subdir under the repo root).",
         quote_strings=True)
    emit("default_provider", cfg.default_provider,
         "Provider used when --provider is not passed. One of: replay, claude, codex.")
    emit("scenarios_dir", cfg.scenarios_dir,
         "Where replay scenarios live (relative to repo root).",
         quote_strings=True)
    emit("sandbox_dir", cfg.sandbox_dir,
         "Where the disposable sandbox lives. Empty = system tempdir.",
         quote_strings=True)
    emit("clean_workspace_dir", cfg.clean_workspace_dir,
         "Where the clean reapply workspace lives. Empty = system tempdir.",
         quote_strings=True)
    if cfg.notes and cfg.notes != defaults.notes:
        emit("notes", cfg.notes, "Free-form notes (surfaced in the harness profile).",
             quote_strings=True)

    # Always emit the policy block in nested form. Each line is commented
    # iff it matches the default — keeps the file minimal but discoverable.
    lines.append("# Loop policy. Caps on iteration depth and wall-clock time.")
    policy_keys = (
        ("max_code_iterations", "Hard cap on code-change iterations."),
        ("max_validation_attempts_per_iteration",
         "Validation retries per iteration (e.g. flaky-test reruns)."),
        ("max_diagnostic_rounds_per_failure",
         "How many diagnostic-fetch rounds before forcing progress."),
        ("max_total_wall_clock_minutes",
         "Overall wall-clock budget for the whole loop."),
    )
    all_default = all(getattr(cfg, k) == getattr(defaults, k) for k, _ in policy_keys)
    lines.append(("#" if (all_default and not include_defaults) else "") + "policy:")
    for k, comment in policy_keys:
        v = getattr(cfg, k)
        is_default = getattr(defaults, k) == v
        prefix = "  # " if (is_default and not include_defaults) else "  "
        if all_default and not include_defaults:
            prefix = "#" + prefix
        lines.append(f"#   {comment}")
        lines.append(f"{prefix}{k}: {v}")
    lines.append("")

    # Collapse runs of blank lines.
    out: list[str] = []
    prev_blank = False
    for ln in lines:
        is_blank = (ln.strip() == "")
        if is_blank and prev_blank:
            continue
        out.append(ln)
        prev_blank = is_blank
    return "\n".join(out).rstrip() + "\n"


def _yaml_scalar(v: Any, *, quote_strings: bool = False) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # Multi-line strings — delegate to PyYAML so block scalars stay legal.
    if "\n" in s:
        import yaml as _yaml
        dumped = _yaml.safe_dump(s, default_style='"').rstrip()
        # ``safe_dump`` returns ``"line1\nline2\n"`` — exactly the inline
        # form we want on the right of the ``key:``.
        return dumped
    if quote_strings or (not s) or any(c in s for c in ":#{}[],&*?|<>=!%@`\""):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


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
