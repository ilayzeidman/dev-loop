"""Task ledger and run manifests.

Directory layout (per design section 8):

    runs/<task-id>/
      task_manifest.json
      baseline/
        base_sha.txt
        original_prompt.md
      iterations/
        iter-001/
          manifest.json
          input_to_agent.json
          agent_response.json
          patch.diff
          changed_files.json
          ai_calls/...
          validations/
            attempt-001/
              ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import read_json, utc_now_iso, write_json, write_text


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(value: str, max_len: int = 48) -> str:
    s = value.strip().lower()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s[:max_len] or "task"


@dataclass
class TaskLedger:
    """Filesystem-backed ledger for a single ``/implement`` invocation."""

    root: Path
    task_id: str

    @classmethod
    def create(cls, runs_dir: Path, task_id: str) -> "TaskLedger":
        root = runs_dir / task_id
        root.mkdir(parents=True, exist_ok=False)
        ledger = cls(root=root, task_id=task_id)
        ledger.write_task_manifest({
            "task_id": task_id,
            "created_at_utc": utc_now_iso(),
            "status": "initialized",
            "iterations": [],
            "final_status": None,
        })
        (root / "baseline").mkdir(parents=True, exist_ok=True)
        (root / "iterations").mkdir(parents=True, exist_ok=True)
        return ledger

    # task manifest -----------------------------------------------------

    @property
    def task_manifest_path(self) -> Path:
        return self.root / "task_manifest.json"

    def read_task_manifest(self) -> dict[str, Any]:
        return read_json(self.task_manifest_path)

    def write_task_manifest(self, manifest: dict[str, Any]) -> None:
        write_json(self.task_manifest_path, manifest)

    def update_task_manifest(self, **fields: Any) -> dict[str, Any]:
        m = self.read_task_manifest()
        m.update(fields)
        m["updated_at_utc"] = utc_now_iso()
        self.write_task_manifest(m)
        return m

    # baseline ----------------------------------------------------------

    def record_baseline(self, *, base_sha: str, original_prompt: str) -> None:
        write_text(self.root / "baseline" / "base_sha.txt", base_sha + "\n")
        write_text(self.root / "baseline" / "original_prompt.md", original_prompt)

    # iterations --------------------------------------------------------

    def iteration_dir(self, iteration: int) -> Path:
        return self.root / "iterations" / f"iter-{iteration:03d}"

    def create_iteration(self, iteration: int) -> Path:
        d = self.iteration_dir(iteration)
        d.mkdir(parents=True, exist_ok=False)
        (d / "ai_calls").mkdir(parents=True, exist_ok=True)
        (d / "validations").mkdir(parents=True, exist_ok=True)
        return d

    def write_iteration_manifest(self, iteration: int, manifest: dict[str, Any]) -> None:
        write_json(self.iteration_dir(iteration) / "manifest.json", manifest)

    def read_iteration_manifest(self, iteration: int) -> dict[str, Any]:
        return read_json(self.iteration_dir(iteration) / "manifest.json")

    # validation attempts ----------------------------------------------

    def attempt_dir(self, iteration: int, attempt: int) -> Path:
        return self.iteration_dir(iteration) / "validations" / f"attempt-{attempt:03d}"

    def create_attempt(self, iteration: int, attempt: int) -> Path:
        d = self.attempt_dir(iteration, attempt)
        d.mkdir(parents=True, exist_ok=False)
        (d / "diagnostics").mkdir(parents=True, exist_ok=True)
        return d

    # ai call record ----------------------------------------------------

    def record_ai_call(
        self,
        iteration: int,
        ordinal: int,
        phase: str,
        *,
        input_obj: dict[str, Any],
        output_obj: dict[str, Any],
        raw_provider_log: str | None = None,
    ) -> Path:
        d = self.iteration_dir(iteration) / "ai_calls" / f"{ordinal:03d}_{phase}"
        d.mkdir(parents=True, exist_ok=True)
        write_json(d / "input.json", input_obj)
        write_json(d / "output.json", output_obj)
        if raw_provider_log is not None:
            write_text(d / "raw_provider_log.jsonl", raw_provider_log)
        return d
