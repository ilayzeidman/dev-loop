"""Playbook loading.

Playbooks ship inside the harness package (``PLAYBOOK_DIR``). A repository
may override any built-in by dropping a file with the same name into
``$REPO/.dev-loop/playbooks/``. Lookup prefers the repo override and falls
back to the package copy, so editing in the UI never touches the installed
harness directory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
