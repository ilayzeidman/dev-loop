"""Repository setup diagnostics for ``dev-loop doctor``.

A single command that scans the active repo for the most common
onboarding/setup issues and reports them with actionable hints. Designed
to be the first command a confused user runs ("why doesn't this work?")
and to fail loudly with a clear next step rather than fail silently
deep inside the orchestrator.

The output mirrors the existing ``config validate`` convention — one
issue per line, with a ``level`` (``ok``, ``warning``, ``error``), a
short ``label`` for the check, and a ``message`` containing the
suggested fix. JSON output is symmetric so CI scripts can gate on it.

Severity policy
  - ``error``   the harness will fail or be useless until this is fixed.
  - ``warning`` the harness will probably still run but quality / safety
    is degraded (missing gitignore, no scenarios, provider CLI absent).
  - ``ok``      passing checks are always emitted so the user can see
    what *did* work, not just what's broken.

Exit codes
  - 0 on a clean bill of health (warnings allowed).
  - 1 if any error-level checks fired.
  - With ``--strict``, warnings also exit 1.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    STARTER_SCENARIO_NAME,
    HarnessConfig,
)
from .runs import list_runs

# Built-in providers and which environment variable / executable name to
# probe when the user picks one as their default. ``replay`` needs no
# external binary so it's intentionally absent from this table.
_PROVIDER_PROBES: dict[str, dict[str, str]] = {
    "claude": {"env": "CLAUDE_CLI_PATH", "exe": "claude"},
    "claude_code": {"env": "CLAUDE_CLI_PATH", "exe": "claude"},
    "codex": {"env": "CODEX_CLI_PATH", "exe": "codex"},
}


@dataclass(frozen=True)
class DoctorCheck:
    level: str       # "ok" | "warning" | "error"
    label: str       # short stable identifier, e.g. "config_file"
    message: str     # human-readable summary
    hint: str = ""   # optional next-step / fix suggestion

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {
            "level": self.level,
            "label": self.label,
            "message": self.message,
        }
        if self.hint:
            d["hint"] = self.hint
        return d


def run_doctor(
    repo: Path,
    *,
    explicit_config: Path | None = None,
) -> list[DoctorCheck]:
    """Run every check and return the ordered list.

    The result is deterministic and order-stable so snapshots and CI
    pipelines can pin against it.
    """
    repo = repo.resolve()
    checks: list[DoctorCheck] = []

    checks.append(_check_repo_dir(repo))
    cfg, cfg_path, cfg_issues = _load_config(repo, explicit_config)
    checks.extend(_check_config(repo, cfg_path, cfg_issues))
    checks.extend(_check_gitignore(repo))
    checks.extend(_check_runs_dir(repo, cfg))
    checks.extend(_check_scenarios_dir(repo, cfg))
    checks.append(_check_provider(cfg))
    checks.append(_check_runs_ledger(repo, cfg))
    return checks


def doctor_summary(checks: list[DoctorCheck]) -> dict[str, int]:
    """Tally ``checks`` by level. Used by the CLI for the trailing line."""
    out: dict[str, int] = {"ok": 0, "warning": 0, "error": 0}
    for c in checks:
        out[c.level] = out.get(c.level, 0) + 1
    return out


def doctor_exit_code(checks: list[DoctorCheck], *, strict: bool) -> int:
    """0 on clean, 1 if any errors (or warnings under ``--strict``)."""
    summary = doctor_summary(checks)
    if summary.get("error"):
        return 1
    if strict and summary.get("warning"):
        return 1
    return 0


def to_json(
    repo: Path, checks: list[DoctorCheck], *, exit_code: int,
) -> str:
    payload = {
        "repo": str(repo),
        "checks": [c.to_dict() for c in checks],
        "summary": doctor_summary(checks),
        "exit_code": exit_code,
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_repo_dir(repo: Path) -> DoctorCheck:
    if not repo.exists():
        return DoctorCheck(
            "error", "repo_dir",
            f"repo path does not exist: {repo}",
            hint="pass --repo <path> or run from inside the target repo",
        )
    if not repo.is_dir():
        return DoctorCheck(
            "error", "repo_dir",
            f"repo path is not a directory: {repo}",
        )
    return DoctorCheck("ok", "repo_dir", f"repo: {repo}")


def _load_config(
    repo: Path, explicit: Path | None,
) -> tuple[HarnessConfig, Path | None, list[dict[str, str]]]:
    try:
        cfg, path, issues = HarnessConfig.load_with_issues(
            repo_root=repo, explicit_path=explicit,
        )
    except (ValueError, OSError):
        # Surface as a check below — don't crash the whole doctor run.
        return HarnessConfig(), None, [{
            "level": "error", "field": "<root>",
            "message": "failed to read config (invalid YAML?)",
        }]
    return cfg, path, issues


def _check_config(
    repo: Path, cfg_path: Path | None, issues: list[dict[str, str]],
) -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    if cfg_path is None:
        out.append(DoctorCheck(
            "warning", "config_file",
            f"no config file — using built-in defaults",
            hint=f"run `dev-loop init` to scaffold "
                 f"{CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}",
        ))
        return out

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]

    if errors:
        first = errors[0]
        out.append(DoctorCheck(
            "error", "config_file",
            f"{cfg_path}: {first['field']}: {first['message']}"
            + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""),
            hint="run `dev-loop config validate` for the full list",
        ))
        return out

    if warnings:
        first = warnings[0]
        out.append(DoctorCheck(
            "warning", "config_file",
            f"{cfg_path}: {first['field']}: {first['message']}"
            + (f" (+{len(warnings) - 1} more)" if len(warnings) > 1 else ""),
            hint="run `dev-loop config validate` for the full list",
        ))
        return out

    out.append(DoctorCheck("ok", "config_file", f"config valid: {cfg_path}"))
    return out


def _check_gitignore(repo: Path) -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    gi = repo / ".gitignore"
    git_dir = repo / ".git"
    if not git_dir.exists():
        out.append(DoctorCheck(
            "warning", "git_repo",
            "no .git directory — patch extraction relies on git",
            hint="run `git init` (and at least one commit) before "
                 "`dev-loop implement`",
        ))
    else:
        out.append(DoctorCheck("ok", "git_repo", "git repo detected"))

    if not gi.exists():
        out.append(DoctorCheck(
            "warning", "gitignore",
            "no .gitignore — run artifacts under .dev-loop/runs/ may "
            "leak into git",
            hint="run `dev-loop init` or add `.dev-loop/runs/` manually",
        ))
        return out

    content = gi.read_text(encoding="utf-8", errors="replace")
    if ".dev-loop/runs" in content or "/.dev-loop/runs" in content:
        out.append(DoctorCheck("ok", "gitignore", ".dev-loop/runs/ is gitignored"))
    else:
        out.append(DoctorCheck(
            "warning", "gitignore",
            ".gitignore does not exclude .dev-loop/runs/",
            hint="re-run `dev-loop init` (idempotent) to append the "
                 "standard ignore lines",
        ))
    return out


def _check_runs_dir(repo: Path, cfg: HarnessConfig) -> list[DoctorCheck]:
    runs_dir = (repo / cfg.runs_dir).resolve()
    if runs_dir.exists():
        if not runs_dir.is_dir():
            return [DoctorCheck(
                "error", "runs_dir",
                f"{runs_dir} exists but is not a directory",
            )]
        writable = os.access(runs_dir, os.W_OK)
        if not writable:
            return [DoctorCheck(
                "error", "runs_dir",
                f"{runs_dir} is not writable",
                hint="check filesystem permissions or set runs_dir to "
                     "a path you own",
            )]
        return [DoctorCheck("ok", "runs_dir", f"runs_dir writable: {runs_dir}")]

    parent = runs_dir.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not os.access(parent, os.W_OK):
        return [DoctorCheck(
            "error", "runs_dir",
            f"cannot create {runs_dir} (parent not writable)",
            hint="set runs_dir to a path you own or fix permissions on "
                 f"{parent}",
        )]
    return [DoctorCheck(
        "ok", "runs_dir",
        f"runs_dir does not exist yet but is creatable: {runs_dir}",
    )]


def _check_scenarios_dir(repo: Path, cfg: HarnessConfig) -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    scenarios_dir = (repo / cfg.scenarios_dir).resolve()
    if not scenarios_dir.exists():
        out.append(DoctorCheck(
            "warning", "scenarios_dir",
            f"no scenarios directory at {scenarios_dir}",
            hint="run `dev-loop init --starter` to install the "
                 f"'{STARTER_SCENARIO_NAME}' demo, or author your own "
                 "under scenarios_dir",
        ))
        return out

    scenarios = sorted(
        d.name for d in scenarios_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    if not scenarios:
        out.append(DoctorCheck(
            "warning", "scenarios_dir",
            f"scenarios directory is empty: {scenarios_dir}",
            hint="run `dev-loop init --starter` to install the bundled demo",
        ))
        return out

    has_starter = STARTER_SCENARIO_NAME in scenarios
    summary = f"{len(scenarios)} scenario(s)"
    if has_starter:
        summary += f" (incl. {STARTER_SCENARIO_NAME})"
    out.append(DoctorCheck("ok", "scenarios_dir", summary))
    return out


def _check_provider(cfg: HarnessConfig) -> DoctorCheck:
    prov = (cfg.default_provider or "").lower()
    if prov == "replay":
        return DoctorCheck(
            "ok", "provider",
            "default_provider=replay (no external CLI needed)",
        )
    probe = _PROVIDER_PROBES.get(prov)
    if probe is None:
        return DoctorCheck(
            "warning", "provider",
            f"default_provider={prov!r} is not a built-in (replay, "
            "claude, codex)",
            hint="set default_provider to one of: replay, claude, codex",
        )
    env_var = probe["env"]
    exe_name = probe["exe"]
    env_path = os.environ.get(env_var)
    if env_path and Path(env_path).exists():
        return DoctorCheck(
            "ok", "provider",
            f"default_provider={prov}; CLI at ${env_var}={env_path}",
        )
    discovered = shutil.which(exe_name)
    if discovered:
        return DoctorCheck(
            "ok", "provider",
            f"default_provider={prov}; CLI on PATH: {discovered}",
        )
    return DoctorCheck(
        "warning", "provider",
        f"default_provider={prov} but no `{exe_name}` CLI on PATH",
        hint=f"install the {exe_name} CLI or set {env_var} to its path; "
             "until then real runs will fail (replay still works)",
    )


def _check_runs_ledger(repo: Path, cfg: HarnessConfig) -> DoctorCheck:
    runs_dir = (repo / cfg.runs_dir).resolve()
    if not runs_dir.exists():
        return DoctorCheck("ok", "runs_ledger", "no runs yet")
    runs = list_runs(runs_dir)
    if not runs:
        return DoctorCheck("ok", "runs_ledger", "no runs yet")
    last = runs[0]
    return DoctorCheck(
        "ok", "runs_ledger",
        f"{len(runs)} run(s); latest: {last['task_id']} "
        f"({last.get('final_status') or last.get('status') or '-'})",
    )


# ---------------------------------------------------------------------------
# Pretty-printer (for the CLI; the UI uses ``to_json``)
# ---------------------------------------------------------------------------


_GLYPH = {"ok": "ok   ", "warning": "warn ", "error": "ERROR"}


def format_checks(checks: list[DoctorCheck]) -> str:
    """Render ``checks`` for the terminal. Stable, alignment-friendly."""
    lines: list[str] = []
    label_w = max((len(c.label) for c in checks), default=0)
    for c in checks:
        lines.append(
            f"  {_GLYPH.get(c.level, c.level):<5}  "
            f"{c.label:<{label_w}}  {c.message}"
        )
        if c.hint:
            lines.append(f"         {' ' * label_w}  hint: {c.hint}")
    return "\n".join(lines)


def format_summary(checks: list[DoctorCheck]) -> str:
    s = doctor_summary(checks)
    parts: list[str] = []
    for k in ("ok", "warning", "error"):
        n = s.get(k, 0)
        if n:
            parts.append(f"{n} {k}{'s' if n != 1 else ''}")
    return ", ".join(parts) if parts else "no checks ran"
