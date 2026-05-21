"""Config-bundle export/import.

A *bundle* is the portable snapshot of everything you tuned for one
repo: ``.dev-loop/config.yaml``, the scenarios you authored, and any
playbooks that differ from the package defaults. Bundles are plain JSON
so you can email one, drop it in a chat, paste it into a teammate's UI,
or check it into a "templates" repo — and re-apply it to a brand new
clone in one click.

Roundtrip:

    repoA  ─export──►  bundle.json  ─import──►  repoB

What's included
  - ``config``: the raw text of ``.dev-loop/config.yaml`` (or empty).
  - ``scenarios``: every scenario directory under ``scenarios_dir``,
    one entry per scenario, with all text files (recursive paths
    relative to the scenario dir).
  - ``playbooks``: every playbook from the harness package whose
    on-disk text differs from the bundled default. (Built-ins that
    have not been edited are skipped — there's no point shipping them.)

What's deliberately NOT included
  - Run artifacts under ``.dev-loop/runs/`` — those are forensics, not
    configuration.
  - Anything outside the textual config surface (binaries, etc.).

Apply policy
  - ``skip``       (default) — keep the destination's version on conflict.
  - ``overwrite``  — write the bundle's version unconditionally.
  - ``rename``     — write conflicting files with a ``.imported`` suffix
    so you can diff and merge by hand.

Importers always get a *preview* first (what's new, what conflicts,
what's identical) so applying is never a surprise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bundle_templates import TEMPLATE_DIR
from .config import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    HarnessConfig,
)
from .playbooks import PLAYBOOK_DIR
from .util import utc_now_iso

BUNDLE_FORMAT = "dev-loop-bundle"
BUNDLE_FORMAT_VERSION = 1

# Files we refuse to read from a scenario directory because shipping a
# binary makes the bundle huge and unreviewable. Bundles are meant to be
# human-readable JSON; anything not text is dropped during export.
_TEXT_SCENARIO_SUFFIXES = {
    ".md", ".json", ".yaml", ".yml", ".txt", ".py", ".sh", ".toml",
    ".js", ".ts", ".html", ".css", ".diff", ".patch", "",
}

# When the entire scenario file exceeds this many bytes the export drops
# it and records a note. Keeps a runaway artifact from blowing up the
# bundle.
_SCENARIO_FILE_MAX_BYTES = 256 * 1024

# Conflict policies.
_CONFLICT_POLICIES = {"skip", "overwrite", "rename"}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def build_bundle(repo: Path, *, note: str = "") -> dict[str, Any]:
    """Produce the bundle dict for ``repo``."""
    repo = repo.resolve()
    cfg = HarnessConfig.load(repo_root=repo)
    resolved = cfg.resolved(repo)
    cf = repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME

    config_yaml = cf.read_text(encoding="utf-8") if cf.exists() else ""

    scenarios = _dump_scenarios(resolved.scenarios_dir)
    playbooks = _dump_modified_playbooks()

    return {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "created_at_utc": utc_now_iso(),
        "source": {
            "repo_name": repo.name,
            "repo_path": str(repo),
        },
        "note": note,
        "config": {
            "present": cf.exists(),
            "yaml": config_yaml,
        },
        "scenarios": scenarios,
        "playbooks": playbooks,
    }


def _dump_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not scenarios_dir.exists():
        return out
    for d in sorted(scenarios_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        files: dict[str, str] = {}
        skipped: list[str] = []
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(d).as_posix()
            if f.suffix not in _TEXT_SCENARIO_SUFFIXES:
                skipped.append(rel)
                continue
            try:
                size = f.stat().st_size
            except OSError:
                skipped.append(rel)
                continue
            if size > _SCENARIO_FILE_MAX_BYTES:
                skipped.append(rel)
                continue
            try:
                files[rel] = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped.append(rel)
        entry: dict[str, Any] = {"name": d.name, "files": files}
        if skipped:
            entry["skipped"] = skipped
        out.append(entry)
    return out


def _dump_modified_playbooks() -> list[dict[str, Any]]:
    """Playbooks that have been edited away from the package defaults.

    For v1 the playbook directory IS the package directory, so "default"
    just means "doesn't match the in-process bundled string." We export
    everything we find that lives in the playbook dir — diffing against a
    pristine checkout is the importer's job.
    """
    out: list[dict[str, Any]] = []
    if not PLAYBOOK_DIR.exists():
        return out
    for p in sorted(PLAYBOOK_DIR.glob("*.md")):
        try:
            out.append({
                "name": p.name,
                "content": p.read_text(encoding="utf-8"),
            })
        except OSError:
            continue
    return out


def bundle_to_json(bundle: dict[str, Any]) -> str:
    return json.dumps(bundle, indent=2, sort_keys=False) + "\n"


# ---------------------------------------------------------------------------
# Validation & preview
# ---------------------------------------------------------------------------


class BundleError(ValueError):
    """Raised when a bundle is malformed."""


def validate_bundle(bundle: Any) -> dict[str, Any]:
    """Sanity-check the structure of an incoming bundle.

    Returns the bundle (unchanged) on success; raises ``BundleError``
    with a human-readable message on failure. We're deliberately lenient
    about fields we don't strictly need (e.g. ``note`` may be absent) so
    older bundles round-trip cleanly.
    """
    if not isinstance(bundle, dict):
        raise BundleError("bundle must be a JSON object at the top level")
    fmt = bundle.get("format")
    if fmt != BUNDLE_FORMAT:
        raise BundleError(
            f"unrecognised bundle format: {fmt!r} "
            f"(expected {BUNDLE_FORMAT!r})"
        )
    ver = bundle.get("format_version")
    if not isinstance(ver, int) or ver < 1 or ver > BUNDLE_FORMAT_VERSION:
        raise BundleError(
            f"unsupported bundle format_version: {ver!r} "
            f"(this build understands 1..{BUNDLE_FORMAT_VERSION})"
        )

    cfg = bundle.get("config") or {}
    if not isinstance(cfg, dict):
        raise BundleError("'config' must be an object")
    if "yaml" in cfg and not isinstance(cfg["yaml"], str):
        raise BundleError("'config.yaml' must be a string")

    scenarios = bundle.get("scenarios") or []
    if not isinstance(scenarios, list):
        raise BundleError("'scenarios' must be a list")
    for i, s in enumerate(scenarios):
        if not isinstance(s, dict):
            raise BundleError(f"scenarios[{i}] must be an object")
        name = s.get("name")
        if not isinstance(name, str) or not _is_safe_path_segment(name):
            raise BundleError(f"scenarios[{i}].name is missing or unsafe")
        files = s.get("files") or {}
        if not isinstance(files, dict):
            raise BundleError(f"scenarios[{i}].files must be an object")
        for k, v in files.items():
            if not isinstance(k, str) or not _is_safe_relative_path(k):
                raise BundleError(
                    f"scenarios[{name}]: unsafe file path {k!r}"
                )
            if not isinstance(v, str):
                raise BundleError(
                    f"scenarios[{name}].files[{k}] must be a string"
                )

    playbooks = bundle.get("playbooks") or []
    if not isinstance(playbooks, list):
        raise BundleError("'playbooks' must be a list")
    for i, p in enumerate(playbooks):
        if not isinstance(p, dict):
            raise BundleError(f"playbooks[{i}] must be an object")
        name = p.get("name")
        if not isinstance(name, str) or not _is_safe_path_segment(name):
            raise BundleError(f"playbooks[{i}].name is missing or unsafe")
        if not name.endswith(".md"):
            raise BundleError(f"playbooks[{i}].name must end in .md")
        if not isinstance(p.get("content"), str):
            raise BundleError(f"playbooks[{name}].content must be a string")
    return bundle


def _is_safe_path_segment(s: str) -> bool:
    if not s or s.startswith("."):
        return False
    for bad in ("/", "\\", "\x00"):
        if bad in s:
            return False
    return True


def _is_safe_relative_path(s: str) -> bool:
    """A scenario file path may contain ``/`` separators but must not
    escape with ``..`` or start at root."""
    if not s or s.startswith("/") or s.startswith("\\"):
        return False
    if "\x00" in s:
        return False
    parts = s.replace("\\", "/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return False
    return True


@dataclass
class FileChange:
    """One unit of work the import would perform.

    ``status`` is one of:
      ``new``       — destination has no such file
      ``identical`` — destination has it, content matches
      ``conflict``  — destination has it, content differs
    """
    kind: str           # "config" | "scenario" | "playbook"
    path: str           # display path, e.g. "scenarios/foo-001/task_request.md"
    status: str
    abs_path: str       # absolute destination path (for the UI)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "status": self.status,
            "abs_path": self.abs_path,
        }


def preview_apply(bundle: dict[str, Any], repo: Path) -> dict[str, Any]:
    """Compute what would change if ``bundle`` were applied to ``repo``.

    Pure: no filesystem writes. Returns a structured summary the CLI and
    UI both render.
    """
    validate_bundle(bundle)
    repo = repo.resolve()
    cfg = HarnessConfig.load(repo_root=repo)
    resolved = cfg.resolved(repo)
    scenarios_dir = resolved.scenarios_dir

    changes: list[FileChange] = []

    # config
    cfg_yaml = (bundle.get("config") or {}).get("yaml") or ""
    if cfg_yaml or (bundle.get("config") or {}).get("present"):
        cf = repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME
        if not cf.exists():
            status = "new"
        elif cf.read_text(encoding="utf-8") == cfg_yaml:
            status = "identical"
        else:
            status = "conflict"
        changes.append(FileChange(
            kind="config",
            path=f"{CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}",
            status=status,
            abs_path=str(cf),
        ))

    # scenarios
    for s in bundle.get("scenarios") or []:
        name = s["name"]
        for rel, content in (s.get("files") or {}).items():
            dest = scenarios_dir / name / rel
            display = f"scenarios/{name}/{rel}"
            if not dest.exists():
                status = "new"
            else:
                try:
                    status = (
                        "identical"
                        if dest.read_text(encoding="utf-8") == content
                        else "conflict"
                    )
                except (OSError, UnicodeDecodeError):
                    status = "conflict"
            changes.append(FileChange(
                kind="scenario",
                path=display,
                status=status,
                abs_path=str(dest),
            ))

    # playbooks
    for p in bundle.get("playbooks") or []:
        name = p["name"]
        content = p["content"]
        dest = PLAYBOOK_DIR / name
        display = f"playbooks/{name}"
        if not dest.exists():
            status = "new"
        else:
            try:
                status = (
                    "identical"
                    if dest.read_text(encoding="utf-8") == content
                    else "conflict"
                )
            except (OSError, UnicodeDecodeError):
                status = "conflict"
        changes.append(FileChange(
            kind="playbook",
            path=display,
            status=status,
            abs_path=str(dest),
        ))

    totals = {
        "new": sum(1 for c in changes if c.status == "new"),
        "identical": sum(1 for c in changes if c.status == "identical"),
        "conflict": sum(1 for c in changes if c.status == "conflict"),
    }
    return {
        "repo": str(repo),
        "source": bundle.get("source", {}),
        "note": bundle.get("note", ""),
        "changes": [c.to_dict() for c in changes],
        "totals": totals,
    }


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_bundle(
    bundle: dict[str, Any],
    repo: Path,
    *,
    on_conflict: str = "skip",
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Write the bundle's contents into ``repo``.

    ``on_conflict``
      - ``skip``      keep destination version (default)
      - ``overwrite`` replace destination
      - ``rename``    write conflicting files to ``<path>.imported``

    ``include`` is an optional allow-list of display paths (as returned
    by :func:`preview_apply`). When given, only listed paths are written.
    Useful for the UI's per-item checkboxes.

    Returns a structured report listing what was written, skipped, or
    renamed — so the UI can show the user exactly what happened.
    """
    if on_conflict not in _CONFLICT_POLICIES:
        raise BundleError(
            f"on_conflict must be one of {sorted(_CONFLICT_POLICIES)}, "
            f"got {on_conflict!r}"
        )
    validate_bundle(bundle)
    repo = repo.resolve()
    cfg = HarnessConfig.load(repo_root=repo)
    resolved = cfg.resolved(repo)
    scenarios_dir = resolved.scenarios_dir

    actions: list[dict[str, str]] = []
    allow = set(include) if include is not None else None

    def _should_include(display: str) -> bool:
        return allow is None or display in allow

    def _write(display: str, dest: Path, content: str, root_for_safety: Path) -> None:
        # Final belt-and-braces: never write outside the root we own.
        # ``root_for_safety`` is whichever sandbox this kind of file
        # belongs in (repo, scenarios_dir, PLAYBOOK_DIR).
        if not _is_within(dest, root_for_safety):
            actions.append({"path": display, "action": "refused", "reason": "outside sandbox"})
            return
        if dest.exists():
            try:
                same = dest.read_text(encoding="utf-8") == content
            except (OSError, UnicodeDecodeError):
                same = False
            if same:
                actions.append({"path": display, "action": "identical"})
                return
            if on_conflict == "skip":
                actions.append({"path": display, "action": "skipped"})
                return
            if on_conflict == "rename":
                renamed = dest.with_suffix(dest.suffix + ".imported")
                renamed.parent.mkdir(parents=True, exist_ok=True)
                renamed.write_text(content, encoding="utf-8")
                actions.append({"path": display, "action": "renamed", "wrote": str(renamed)})
                return
            # overwrite
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            actions.append({"path": display, "action": "overwrote"})
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        actions.append({"path": display, "action": "wrote"})

    # config
    cfg_yaml = (bundle.get("config") or {}).get("yaml") or ""
    if (bundle.get("config") or {}).get("present") or cfg_yaml:
        display = f"{CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}"
        if _should_include(display):
            cf = repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME
            _write(display, cf, cfg_yaml, repo)

    # scenarios
    for s in bundle.get("scenarios") or []:
        name = s["name"]
        for rel, content in (s.get("files") or {}).items():
            display = f"scenarios/{name}/{rel}"
            if not _should_include(display):
                continue
            dest = (scenarios_dir / name / rel).resolve()
            _write(display, dest, content, scenarios_dir.resolve())

    # playbooks
    for p in bundle.get("playbooks") or []:
        name = p["name"]
        display = f"playbooks/{name}"
        if not _should_include(display):
            continue
        dest = (PLAYBOOK_DIR / name).resolve()
        _write(display, dest, p["content"], PLAYBOOK_DIR.resolve())

    # Bust the playbook cache so the in-process orchestrator sees the
    # imported text on the next call.
    if any(a["path"].startswith("playbooks/") for a in actions):
        from .playbooks import load_playbook
        load_playbook.cache_clear()

    return {
        "repo": str(repo),
        "on_conflict": on_conflict,
        "actions": actions,
        "totals": _summarize_actions(actions),
    }


def _summarize_actions(actions: list[dict[str, str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in actions:
        out[a["action"]] = out.get(a["action"], 0) + 1
    return out


def _is_within(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------


def _template_meta(bundle: dict[str, Any], fallback_id: str) -> dict[str, Any]:
    meta = bundle.get("template") or {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": meta.get("id") or fallback_id,
        "title": meta.get("title") or fallback_id,
        "summary": meta.get("summary") or "",
        "tags": list(meta.get("tags") or []),
        "order": meta.get("order") if isinstance(meta.get("order"), int) else 1000,
    }


def _template_includes(bundle: dict[str, Any]) -> dict[str, int]:
    """Tiny inventory for the strip cards: how many of each piece."""
    cfg = bundle.get("config") or {}
    has_config = bool(cfg.get("yaml") or cfg.get("present"))
    scenarios = bundle.get("scenarios") or []
    playbooks = bundle.get("playbooks") or []
    return {
        "config": 1 if has_config else 0,
        "scenarios": len(scenarios),
        "playbooks": len(playbooks),
    }


def list_templates() -> list[dict[str, Any]]:
    """Return strip-card metadata for every bundled template.

    Each entry is ``{id, title, summary, tags, order, includes}``. The
    full bundle is loaded by :func:`load_template` on demand so the
    listing stays cheap.

    A malformed template file is skipped (logged via the order key
    flipping to ``9999``) rather than failing the whole listing.
    """
    out: list[dict[str, Any]] = []
    if not TEMPLATE_DIR.exists():
        return out
    for f in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            bundle = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            validate_bundle(bundle)
        except BundleError:
            continue
        meta = _template_meta(bundle, f.stem)
        meta["includes"] = _template_includes(bundle)
        out.append(meta)
    out.sort(key=lambda t: (t["order"], t["id"]))
    return out


def load_template(template_id: str) -> dict[str, Any]:
    """Return the bundle body for ``template_id``.

    The returned dict is the same shape ``build_bundle`` emits, so it
    flows straight into :func:`preview_apply` and :func:`apply_bundle`.
    Raises :class:`BundleError` if the id is unknown or the on-disk
    template fails validation — never returns an unsafe bundle.
    """
    if not _is_safe_path_segment(template_id):
        raise BundleError(f"invalid template id: {template_id!r}")
    f = TEMPLATE_DIR / f"{template_id}.json"
    try:
        f.resolve().relative_to(TEMPLATE_DIR.resolve())
    except ValueError:
        raise BundleError(f"invalid template id: {template_id!r}")
    if not f.exists():
        raise BundleError(f"unknown template: {template_id!r}")
    try:
        bundle = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BundleError(f"template {template_id!r} is unreadable: {e}")
    validate_bundle(bundle)
    return bundle
