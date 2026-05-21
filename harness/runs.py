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
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from .util import read_json


_AI_CALL_DIR_RE = re.compile(r"^\d{3}_[A-Za-z0-9_\-]+$")


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


# Lifecycle states the orchestrator writes to ``task_manifest.status``. A
# run is "in flight" until ``status == 'completed'`` (or one of the
# pre-completion terminal states the orchestrator explicitly sets).
_LIVE_STATUSES = frozenset({
    "initialized", "contract_ready", "stopped",
})


def effective_status(manifest: dict[str, Any]) -> str:
    """Stable bucket for ``runs ls`` display.

    A run can crash (SIGINT to the provider CLI, host OOM, kernel kill)
    between writing ``task_manifest.json`` and finalizing it. The ledger
    then has ``status`` set to a mid-flight value (``contract_ready``,
    ``initialized``) with no ``final_status``. The original behaviour
    rendered that as ``-`` in the table, indistinguishable from a run
    that's currently executing.

    This helper collapses both surfaces into one bucket so the CLI and UI
    can decide what to render:

      * ``final_status`` wins when present (the run reached the report
        writer).
      * Otherwise ``aborted`` for a mid-flight status (the process died
        before the loop finished).
      * Otherwise the raw ``status`` (e.g. ``stopped``).
      * ``unknown`` when even ``status`` is missing.

    Pure — no I/O — so the helper can be unit-tested directly and reused
    from both ``harness.runs`` consumers and the UI's listing payload.
    """
    fs = manifest.get("final_status")
    if isinstance(fs, str) and fs:
        return fs
    st = manifest.get("status")
    if isinstance(st, str) and st in _LIVE_STATUSES:
        return "aborted"
    if isinstance(st, str) and st:
        return st
    return "unknown"


def count_iterations(run_dir: Path) -> int:
    """Count iteration directories under ``<run>/iterations/``."""
    iters = run_dir / "iterations"
    if not iters.exists():
        return 0
    return sum(1 for d in iters.iterdir() if d.is_dir())


def _run_dirs_newest_first(runs_dir: Path) -> Iterator[Path]:
    """Yield run directories newest-first by directory name.

    Directory names are timestamp-prefixed (``YYYYMMDD-HHMMSS-…``) so
    lexical reverse-sort matches chronological newest-first. Non-dirs
    are skipped here so callers don't repeat the filter.
    """
    if not runs_dir.exists():
        return
    for d in sorted(runs_dir.iterdir(), reverse=True):
        if d.is_dir():
            yield d


def _summarize_run_dir(d: Path) -> dict[str, Any] | None:
    """Build one ``list_runs`` entry, or ``None`` for a bad/partial dir.

    Returning ``None`` (rather than raising) lets the streaming iterator
    skip corrupt runs without aborting the scan — the same tolerance
    ``list_runs`` has always had, but pulled out so a future caller can
    process runs without ever materializing the whole list.
    """
    tm = d / "task_manifest.json"
    if not tm.exists():
        return None
    try:
        data = read_json(tm)
    except Exception:
        return None
    tc = data.get("task_contract") or {}
    return {
        "task_id": data.get("task_id", d.name),
        "status": data.get("status"),
        "final_status": data.get("final_status"),
        "effective_status": effective_status(data),
        "interrupted": bool(data.get("interrupted")),
        "selected_iteration": data.get("selected_iteration"),
        "created_at_utc": data.get("created_at_utc"),
        "updated_at_utc": data.get("updated_at_utc"),
        "iterations": count_iterations(d),
        "goal": tc.get("implementation_goal"),
        "duration_seconds": run_duration_seconds(data),
        "path": str(d),
    }


def iter_runs(runs_dir: Path) -> Iterator[dict[str, Any]]:
    """Lazily yield run summaries newest-first.

    Skips directories without a readable ``task_manifest.json`` rather
    than raising, so partially-written or corrupt runs don't break the
    scan. Pull-based so callers paginating a large ledger
    (``dev-loop runs ls --limit N``, ``GET /api/runs?limit=N``) only
    parse the manifests they actually need.
    """
    for d in _run_dirs_newest_first(runs_dir):
        entry = _summarize_run_dir(d)
        if entry is not None:
            yield entry


def count_runs(runs_dir: Path) -> int:
    """Total number of readable runs in the ledger.

    Walks the same set of directories ``iter_runs`` would yield without
    parsing each manifest — so a paginated UI can show "showing N of
    TOTAL" without re-reading every ``task_manifest.json``. Bad runs
    that ``iter_runs`` would skip are still counted here because the
    cheap check (``task_manifest.json`` exists) is what determines
    "looks like a run". This is intentionally a slight over-count
    rather than walking every JSON twice.
    """
    n = 0
    for d in _run_dirs_newest_first(runs_dir):
        if (d / "task_manifest.json").exists():
            n += 1
    return n


def list_runs(
    runs_dir: Path,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return a newest-first window of run summaries.

    With no arguments returns every readable run (back-compat for
    callers like trend computation that need the full set). ``limit``
    caps the number of entries returned; ``offset`` skips that many
    newest-first entries before collecting. Both parameters operate
    over the post-filter (readable-only) stream, so a corrupt run never
    "consumes" a slot in the window the caller asked for.

    Skips directories without a readable ``task_manifest.json`` rather
    than raising, so partially-written or corrupt runs don't break
    ``dev-loop runs ls``.
    """
    if offset < 0:
        offset = 0
    if limit is not None and limit < 0:
        limit = 0
    out: list[dict[str, Any]] = []
    seen = 0
    for entry in iter_runs(runs_dir):
        if seen < offset:
            seen += 1
            continue
        if limit is not None and len(out) >= limit:
            break
        out.append(entry)
        seen += 1
    return out


def _safe_read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def iteration_ai_calls(iter_dir: Path) -> list[dict[str, Any]]:
    """Compact per-call summary for one iteration's ``ai_calls/`` directory.

    Returns ``[]`` when the iteration predates the Iter 9 metadata layout
    (no ``ai_calls`` dir) or contains no recorded calls. Each entry is
    the same row the Analyze tab's drilldown consumes — ordinal, phase,
    provider, returncode, synthesized flag, etc. — so CLI and UI agree
    on what a "call" looks like.
    """
    out: list[dict[str, Any]] = []
    d = iter_dir / "ai_calls"
    if not d.exists() or not d.is_dir():
        return out
    for sub in sorted(d.iterdir()):
        if not sub.is_dir() or not _AI_CALL_DIR_RE.match(sub.name):
            continue
        name = sub.name
        ordinal: int | None = None
        phase = name
        head, _, tail = name.partition("_")
        if head.isdigit() and tail:
            ordinal = int(head)
            phase = tail
        meta = _safe_read_json(sub / "metadata.json")
        if not isinstance(meta, dict):
            meta = {}
        output = _safe_read_json(sub / "output.json")
        if not isinstance(output, dict):
            output = {}
        out.append({
            "name": name,
            "ordinal": ordinal,
            "phase": phase,
            "provider": meta.get("provider"),
            "returncode": meta.get("returncode"),
            "synthesized": bool(meta.get("synthesized")),
            "ts_utc": meta.get("ts_utc"),
            "has_raw_log": (sub / "raw_provider_log.jsonl").exists(),
            "has_metadata": bool(meta),
            "stderr_tail_len": len(meta.get("stderr_tail") or ""),
            "output_type": output.get("type"),
        })
    return out


def ai_call_rollup(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one iteration's ai-call list into compact totals.

    Returns ``{"total", "by_provider", "nonzero_returncodes",
    "synthesized", "phases"}``. Used by the CLI ``runs show`` rollup
    line and embedded in ``show_run`` payloads so scripts get the same
    shape without re-walking the directory.
    """
    by_provider: dict[str, int] = {}
    phases: list[str] = []
    nonzero = 0
    synthesized = 0
    for c in calls:
        prov = c.get("provider") or "?"
        by_provider[prov] = by_provider.get(prov, 0) + 1
        ph = c.get("phase")
        if isinstance(ph, str) and ph:
            phases.append(ph)
        rc = c.get("returncode")
        if isinstance(rc, int) and rc != 0:
            nonzero += 1
        if c.get("synthesized"):
            synthesized += 1
    return {
        "total": len(calls),
        "by_provider": by_provider,
        "nonzero_returncodes": nonzero,
        "synthesized": synthesized,
        "phases": phases,
    }


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
            calls = iteration_ai_calls(d)
            iterations.append({
                "iteration": n,
                "final_e2e_status": im.get("final_e2e_status"),
                "summary": summary,
                "patch_hash": code.get("patch_hash"),
                "changed_files": list(code.get("changed_files") or []),
                "attempts": attempt_count,
                "error": im.get("error"),
                "ai_calls": calls,
                "ai_call_rollup": ai_call_rollup(calls),
            })

    report_md = run_root / "final_review_report.md"
    report_json = run_root / "final_review_report.json"

    return {
        "task_id": tm.get("task_id", task_id),
        "status": tm.get("status"),
        "final_status": tm.get("final_status"),
        "effective_status": effective_status(tm),
        "interrupted": bool(tm.get("interrupted")),
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
            "deltas": diff_deltas(a_summary, b_summary)}


def diff_deltas(a: dict[str, Any] | None,
                b: dict[str, Any] | None) -> dict[str, Any]:
    """Pairwise digest of two ``show_run``-shaped summaries.

    Returns ``{"both_present": False}`` when either side is missing so
    callers can decide whether to render a partial diff or error. When
    both sides are present the result includes per-field equality flags
    (``same_goal``, ``same_scenario``, ``same_final_status``), iteration
    bookkeeping (``iteration_count_delta``, ``iteration_status_agreement``,
    ``iteration_status_compared``, ``first_diverging_iteration``),
    file-set membership (``files_only_a``, ``files_only_b``,
    ``files_both``), and audit/duration deltas. Pure — no I/O — so the
    CLI ``runs diff`` and Analyze tab compare endpoint share one
    implementation.
    """
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
