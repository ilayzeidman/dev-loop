"""Playbook loading and introspection.

Playbooks ship inside the harness package (``PLAYBOOK_DIR``). A repository
may override any built-in by dropping a file with the same name into
``$REPO/.dev-loop/playbooks/``. Lookup prefers the repo override and falls
back to the package copy, so editing in the UI never touches the installed
harness directory.

:func:`list_playbooks` and :func:`show_playbook` are the public summary
helpers used by both the ``dev-loop playbooks`` CLI subcommand and the
``/api/playbooks`` UI endpoint — single source of truth for "which
playbooks exist, where do they come from, and which agent phase uses
which".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from ..util import read_text

PLAYBOOK_DIR = Path(__file__).parent
REPO_PLAYBOOK_SUBDIR = ".dev-loop/playbooks"


def repo_playbook_dir(repo: Path) -> Path:
    return Path(repo) / REPO_PLAYBOOK_SUBDIR


def resolve_playbook_path(name: str, *, repo: Path | None = None) -> Path:
    """Return the path the harness would read for ``name``.

    Prefers ``$REPO/.dev-loop/playbooks/<name>`` when ``repo`` is set and
    that file exists; otherwise falls back to the package default.
    """
    if repo is not None:
        override = repo_playbook_dir(repo) / name
        if override.exists():
            return override
    return PLAYBOOK_DIR / name


@lru_cache(maxsize=None)
def load_playbook(name: str, *, repo: Path | None = None) -> str:
    return read_text(resolve_playbook_path(name, repo=repo))


# ---------------------------------------------------------------------------
# Introspection
#
# Public summary helpers consumed by the ``dev-loop playbooks`` CLI and the
# ``/api/playbooks`` UI endpoint. Kept here (not in ``cli.py``) so both
# surfaces share one source of truth, mirroring ``capabilities`` and
# ``scenarios``.


def _phase_bindings() -> dict[str, list[str]]:
    """Return ``{playbook_name: [agent_phase, ...]}`` for built-in bindings.

    Imported lazily to avoid a hard dependency on ``harness.agents`` at
    package import time (the CLI surface for inspecting playbooks should
    still work in environments where the agent layer can't import — e.g.
    a stripped-down packaging).
    """
    try:
        from ..agents.cli_base import PHASE_PLAYBOOK
    except Exception:
        return {}
    bindings: dict[str, list[str]] = {}
    for phase, pb_name in PHASE_PLAYBOOK.items():
        bindings.setdefault(pb_name, []).append(phase.name.lower())
    for v in bindings.values():
        v.sort()
    return bindings


def _all_playbook_names(repo: Path | None) -> list[str]:
    names: set[str] = {p.name for p in PLAYBOOK_DIR.glob("*.md")}
    if repo is not None:
        override_dir = repo_playbook_dir(repo)
        if override_dir.exists():
            names.update(p.name for p in override_dir.glob("*.md"))
    return sorted(names)


def _summarize_playbook(
    name: str, *, repo: Path | None, bindings: dict[str, list[str]],
) -> dict[str, Any]:
    pkg_path = PLAYBOOK_DIR / name
    has_pkg = pkg_path.exists()
    override_path = (repo_playbook_dir(repo) / name) if repo is not None else None
    has_override = bool(override_path and override_path.exists())
    resolved = override_path if has_override else pkg_path

    size = resolved.stat().st_size if resolved and resolved.exists() else 0
    line_count = 0
    if resolved and resolved.exists():
        try:
            line_count = sum(
                1 for _ in resolved.open("r", encoding="utf-8")
            )
        except OSError:
            line_count = 0

    source = "repo-override" if has_override else ("built-in" if has_pkg else "missing")
    return {
        "name": name,
        "source": source,
        "overridden": has_override,
        "has_builtin": has_pkg,
        "path": str(resolved) if resolved else "",
        "size_bytes": size,
        "line_count": line_count,
        "agent_phases": list(bindings.get(name, [])),
    }


def list_playbooks(repo: Path | None = None) -> list[dict[str, Any]]:
    """Return a sorted summary of every visible playbook.

    "Visible" means built-in (shipped with the package) or repo-overridden.
    Pass ``repo`` to include the per-repo override directory; without it,
    only built-ins are listed. Each row carries the fields the CLI table
    and the UI picker both need: name, source, size, line count, and the
    agent phases (if any) that bind to it.
    """
    bindings = _phase_bindings()
    return [
        _summarize_playbook(n, repo=repo, bindings=bindings)
        for n in _all_playbook_names(repo)
    ]


def show_playbook(
    name: str, *, repo: Path | None = None, include_text: bool = True,
) -> dict[str, Any] | None:
    """Return the detailed summary for one playbook, or ``None`` if missing.

    When ``include_text`` is true (default), the resolved markdown body is
    included under ``text`` so callers don't need a second read. The
    summary itself carries the same fields as :func:`list_playbooks` rows.
    """
    if name not in _all_playbook_names(repo):
        return None
    summary = _summarize_playbook(
        name, repo=repo, bindings=_phase_bindings(),
    )
    if include_text and summary["path"]:
        try:
            summary["text"] = Path(summary["path"]).read_text(encoding="utf-8")
        except OSError as e:
            summary["text"] = ""
            summary["read_error"] = f"{type(e).__name__}: {e}"
    return summary
