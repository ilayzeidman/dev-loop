"""Playbook loading."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..util import read_text

PLAYBOOK_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_playbook(name: str) -> str:
    return read_text(PLAYBOOK_DIR / name)
