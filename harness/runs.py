"""Read-only helpers for the run ledger.

The orchestrator writes runs under ``<runs_dir>/<task_id>/`` (see
``manifests.TaskLedger`` and ``docs/design.md`` §8). This module provides
small, dependency-free readers used by the CLI ``runs`` subcommands and
by other surfaces that want a quick at-a-glance summary without pulling
in the whole UI server.

Nothing here writes to the ledger. Bad files are tolerated: a corrupt
``task_manifest.json`` for one run must not prevent ``runs ls`` from
showing the others.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .util import read_json


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def run_duration_seconds(manifest: dict[str, Any]) -> int | None:
    """Wall-clock seconds between ``created_at_utc`` and ``updated_at_utc``.

    Returns ``None`` if either timestamp is missing or unparseable so the
    caller can render an em dash rather than a misleading zero.
    """
    a = _parse_iso(manifest.get("created_at_utc"))
    b = _parse_iso(manifest.get("updated_at_utc"))
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds()))


def count_iterations(run_dir: Path) -> int:
    """Count iteration directories under ``<run>/iterations/``."""
    iters = run_dir / "iterations"
    if not iters.exists():
        return 0
    return sum(1 for d in iters.iterdir() if d.is_dir())


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Return a newest-first list of run summaries.

    Skips directories without a readable ``task_manifest.json`` rather
    than raising, so partially-written or corrupt runs don't break
    ``dev-loop runs ls``.
    """
    out: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return out
    for d in sorted(runs_dir.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        tm = d / "task_manifest.json"
        if not tm.exists():
            continue
        try:
            data = read_json(tm)
        except Exception:
            continue
        tc = data.get("task_contract") or {}
        out.append({
            "task_id": data.get("task_id", d.name),
            "status": data.get("status"),
            "final_status": data.get("final_status"),
            "selected_iteration": data.get("selected_iteration"),
            "created_at_utc": data.get("created_at_utc"),
            "updated_at_utc": data.get("updated_at_utc"),
            "iterations": count_iterations(d),
            "goal": tc.get("implementation_goal"),
            "duration_seconds": run_duration_seconds(data),
            "path": str(d),
        })
    return out


def show_run(runs_dir: Path, task_id: str) -> dict[str, Any] | None:
    """Return a detailed summary for one run, or ``None`` if it doesn't exist.

    The shape is a superset of ``list_runs`` entries — it adds per-iteration
    rollup (patch hash, changed files, attempts, final_e2e_status) and the
    paths to the structured + markdown reports when present.
    """
    run_root = runs_dir / task_id
    tm_path = run_root / "task_manifest.json"
    if not run_root.exists() or not tm_path.exists():
        return None
    try:
        tm = read_json(tm_path)
    except Exception:
        return None
    tc = tm.get("task_contract") or {}

    iterations: list[dict[str, Any]] = []
    iters_dir = run_root / "iterations"
    if iters_dir.exists():
        for d in sorted(x for x in iters_dir.iterdir() if x.is_dir()):
            try:
                n = int(d.name.rsplit("-", 1)[1])
            except (IndexError, ValueError):
                n = len(iterations) + 1
            im: dict[str, Any] = {}
            mp = d / "manifest.json"
            if mp.exists():
                try:
                    im = read_json(mp)
                except Exception:
                    im = {}
            code = im.get("code") or {}
            agent_out = im.get("agent_output") or {}
            summary = ""
            if isinstance(agent_out, dict):
                s = agent_out.get("summary")
                if isinstance(s, str):
                    summary = s
            v = d / "validations"
            attempt_count = (
                sum(1 for x in v.iterdir() if x.is_dir())
                if v.exists() else 0
            )
            iterations.append({
                "iteration": n,
                "final_e2e_status": im.get("final_e2e_status"),
                "summary": summary,
                "patch_hash": code.get("patch_hash"),
                "changed_files": list(code.get("changed_files") or []),
                "attempts": attempt_count,
                "error": im.get("error"),
            })

    report_md = run_root / "final_review_report.md"
    report_json = run_root / "final_review_report.json"

    return {
        "task_id": tm.get("task_id", task_id),
        "status": tm.get("status"),
        "final_status": tm.get("final_status"),
        "selected_iteration": tm.get("selected_iteration"),
        "stop_reason": tm.get("stop_reason"),
        "created_at_utc": tm.get("created_at_utc"),
        "updated_at_utc": tm.get("updated_at_utc"),
        "duration_seconds": run_duration_seconds(tm),
        "goal": tc.get("implementation_goal"),
        "scenario": tc.get("scenario") or tm.get("scenario"),
        "iterations": iterations,
        "path": str(run_root),
        "report_md": str(report_md) if report_md.exists() else None,
        "report_json": str(report_json) if report_json.exists() else None,
    }


def audit_rollup(run_root: Path) -> dict[str, Any]:
    """Aggregate ``capability_audit.jsonl`` into counts.

    Mirrors the rollup the UI compare view shows so headless and web
    surfaces agree on totals. Malformed lines are skipped.
    """
    path = run_root / "capability_audit.jsonl"
    by_status: dict[str, int] = {}
    by_capability: dict[str, int] = {}
    total = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            total += 1
            status = entry.get("status") or "?"
            by_status[status] = by_status.get(status, 0) + 1
            cap = entry.get("capability") or "?"
            by_capability[cap] = by_capability.get(cap, 0) + 1
    return {"total": total, "by_status": by_status, "by_capability": by_capability}


def diff_runs(runs_dir: Path, a: str, b: str) -> dict[str, Any]:
    """Compute the headless analog of the UI's run-compare view.

    Returns ``{"a": summary_or_None, "b": summary_or_None, "deltas": {...}}``
    where each side summary is the ``show_run`` shape plus an ``audit``
    rollup. When either run is missing, that side is ``None`` and
    ``deltas['both_present']`` is ``False`` — callers decide whether to
    error or render a partial diff. Pure so tests don't depend on stdout.
    """
    a_summary = show_run(runs_dir, a)
    b_summary = show_run(runs_dir, b)
    if a_summary is not None:
        a_summary = {**a_summary, "audit": audit_rollup(Path(a_summary["path"]))}
    if b_summary is not None:
        b_summary = {**b_summary, "audit": audit_rollup(Path(b_summary["path"]))}
    return {"a": a_summary, "b": b_summary,
            "deltas": _diff_deltas(a_summary, b_summary)}


def _diff_deltas(a: dict[str, Any] | None,
                 b: dict[str, Any] | None) -> dict[str, Any]:
    if a is None or b is None:
        return {"both_present": False}
    iters_a = a.get("iterations") or []
    iters_b = b.get("iterations") or []
    da_s = a.get("duration_seconds")
    db_s = b.get("duration_seconds")
    dur_delta = (
        (db_s or 0) - (da_s or 0)
        if (da_s is not None and db_s is not None) else None
    )
    n = min(len(iters_a), len(iters_b))
    same_status = sum(
        1 for i in range(n)
        if iters_a[i].get("final_e2e_status") == iters_b[i].get("final_e2e_status")
    )
    first_diverge: int | None = None
    for i in range(n):
        if (iters_a[i].get("final_e2e_status") != iters_b[i].get("final_e2e_status")
                or iters_a[i].get("patch_hash") != iters_b[i].get("patch_hash")):
            first_diverge = i + 1
            break
    files_a = {f for it in iters_a for f in (it.get("changed_files") or [])}
    files_b = {f for it in iters_b for f in (it.get("changed_files") or [])}
    audit_a = (a.get("audit") or {}).get("total") or 0
    audit_b = (b.get("audit") or {}).get("total") or 0
    return {
        "both_present": True,
        "same_goal": (a.get("goal") or "") == (b.get("goal") or ""),
        "same_scenario": (a.get("scenario") or "") == (b.get("scenario") or ""),
        "same_final_status": a.get("final_status") == b.get("final_status"),
        "iteration_count_delta": len(iters_b) - len(iters_a),
        "duration_seconds_delta": dur_delta,
        "iteration_status_agreement": same_status,
        "iteration_status_compared": n,
        "first_diverging_iteration": first_diverge,
        "files_only_a": sorted(files_a - files_b),
        "files_only_b": sorted(files_b - files_a),
        "files_both": sorted(files_a & files_b),
        "audit_total_delta": audit_b - audit_a,
    }
