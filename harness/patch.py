"""Patch extraction and clean reapply.

The agent's explanation is not the implementation artifact; the actual
``git diff`` is. This module:

  - Extracts a unified diff from a sandbox workspace (tracked + untracked).
  - Computes a patch hash.
  - Compares agent-claimed changed files against the real diff.
  - Applies the patch to a clean workspace.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .util import sha256_text


@dataclass
class PatchInfo:
    diff: str
    patch_hash: str
    changed_files: list[str]
    untracked_files: list[str]
    deleted_files: list[str]
    binary_files: list[str]
    claim_mismatches: dict[str, list[str]] = field(default_factory=dict)


def _run(args: list[str], cwd: Path) -> str:
    res = subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True,
    )
    return res.stdout


def _run_allow_empty(args: list[str], cwd: Path) -> str:
    res = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            res.returncode, args, res.stdout, res.stderr,
        )
    return res.stdout


def extract_patch(workspace: Path, claimed_files: list[str] | None = None) -> PatchInfo:
    """Extract the real diff in ``workspace`` against HEAD.

    Includes untracked files (via intent-to-add).
    """
    _run(["git", "add", "-N", "."], cwd=workspace)

    status = _run(["git", "status", "--porcelain=v1"], cwd=workspace)
    untracked: list[str] = []
    deleted: list[str] = []
    changed: list[str] = []
    # Porcelain v1 format: "XY<space>path[ -> rename]"
    for line in status.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        rest = line[3:]
        # Renames show up as "path -> newpath"; take both ends as changed.
        if " -> " in rest:
            old_p, _, new_p = rest.partition(" -> ")
            changed.append(old_p)
            changed.append(new_p)
            continue
        path = rest
        if code == "??":
            untracked.append(path)
        elif "D" in code:
            deleted.append(path)
            changed.append(path)
        else:
            changed.append(path)

    diff = _run_allow_empty(["git", "diff", "HEAD", "--no-color"], cwd=workspace)

    binary_files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("Binary files "):
            binary_files.append(line)

    patch_hash = sha256_text(diff)

    claim_mismatches: dict[str, list[str]] = {}
    if claimed_files is not None:
        actual_set = set(changed)
        claimed_set = set(claimed_files)
        missing_from_claim = sorted(actual_set - claimed_set)
        claimed_but_unchanged = sorted(claimed_set - actual_set)
        if missing_from_claim:
            claim_mismatches["changed_but_not_claimed"] = missing_from_claim
        if claimed_but_unchanged:
            claim_mismatches["claimed_but_not_changed"] = claimed_but_unchanged

    return PatchInfo(
        diff=diff,
        patch_hash=patch_hash,
        changed_files=sorted(set(changed)),
        untracked_files=sorted(set(untracked)),
        deleted_files=sorted(set(deleted)),
        binary_files=binary_files,
        claim_mismatches=claim_mismatches,
    )


def apply_patch_to_clean(
    base_repo: Path,
    base_sha: str,
    patch_diff: str,
    dest: Path,
) -> None:
    """Apply ``patch_diff`` on top of ``base_sha`` into a clean ``dest``.

    Clones ``base_repo`` to ``dest``, checks out ``base_sha``, applies the
    patch. Raises ``subprocess.CalledProcessError`` on conflict.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--quiet", str(base_repo), str(dest)], cwd=base_repo.parent)
    _run(["git", "checkout", "--quiet", base_sha], cwd=dest)

    if not patch_diff.strip():
        return

    patch_file = dest / ".harness-patch.diff"
    patch_file.write_text(patch_diff, encoding="utf-8")
    try:
        _run(
            ["git", "apply", "--whitespace=nowarn", "--index", str(patch_file)],
            cwd=dest,
        )
    finally:
        patch_file.unlink(missing_ok=True)
