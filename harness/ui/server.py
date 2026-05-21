"""HTTP server for the dev-loop UI.

Pure stdlib. Serves a single-page app plus a JSON API:

Read endpoints
  GET  /api/config                       resolved config
  GET  /api/config/raw                   raw config.yaml text
  GET  /api/onboarding                   first-run setup checklist
  GET  /api/runs                         list of task runs + active jobs
  GET  /api/runs/<task-id>               task manifest
  GET  /api/runs/<task-id>/report        rendered Markdown report
  GET  /api/runs/<task-id>/report.json   structured report
  GET  /api/runs/<task-id>/audit         capability audit JSONL
  GET  /api/runs/<task-id>/iteration/<n> iteration manifest
  GET  /api/runs/<task-id>/iteration/<n>/patch  patch diff text
  GET  /api/runs/<task-id>/iteration/<n>/attempts iteration attempts overview
  GET  /api/runs/<task-id>/iteration/<n>/attempt/<a>  full attempt artifact tree
  GET  /api/scenarios                    list of scenarios
  GET  /api/scenarios/<name>             scenario file list + previews
  GET  /api/scenarios/<name>/file/<fn>   raw scenario file
  GET  /api/scenarios/<name>/form        structured projection of a scenario
  GET  /api/capabilities                 capability specs
  GET  /api/playbooks                    list of playbooks
  GET  /api/playbooks/<name>             raw playbook
  GET  /api/schemas                      list of schema names
  GET  /api/schemas/<name>               raw schema JSON
  GET  /api/jobs/<id>                    background job status + log
  GET  /api/bundle/export                JSON bundle of this repo's config
  GET  /api/palette                      unified jump-to index (Cmd+K)

Write endpoints
  POST /api/config/raw                   replace config.yaml
  POST /api/config/validate              dry-run a YAML body (no write)
  POST /api/config/form                  render canonical YAML from a form dict
  POST /api/init                         one-click onboarding (config + starter)
  POST /api/playbooks/<name>             write playbook (repo-local override)
  POST /api/scenarios/<name>/file/<fn>   write scenario file
  POST /api/scenarios                    create new scenario dir
  POST /api/scenarios/<name>/form        save structured form (writes 4 files)
  POST /api/scenarios/<name>/validate    dry-run a structured form (no write)
  POST /api/implement                    launch loop in background
  POST /api/bundle/preview               dry-run a bundle against this repo
  POST /api/bundle/import                apply a bundle to this repo
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..bundle import (
    BundleError,
    apply_bundle,
    build_bundle,
    bundle_to_json,
    preview_apply,
)
from ..config import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    DEFAULT_CONFIG_YAML,
    STARTER_SCENARIO_NAME,
    HarnessConfig,
    append_gitignore,
    dump_canonical_yaml,
    write_default_config,
    write_starter_scenario,
)
from ..playbooks import PLAYBOOK_DIR
from ..schemas import SCHEMA_DIR
from ..scenarios import (
    default_e2e_result,
    default_implementation_result,
    default_task_contract,
    dump_scenario_files,
    load_scenario_form,
    validate_scenario_form,
)
from ..util import read_json

STATIC_DIR = Path(__file__).parent / "static"


def serve(
    *,
    repo: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    repo = Path(repo).resolve()
    jobs = _JobRegistry()
    handler = _make_handler(repo=repo, jobs=jobs)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"dev-loop UI serving at {url}")
    print(f"  repo: {repo}")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open_new(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


# ---------------------------------------------------------------------------


class _JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, request: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "request": request,
                "created_at_utc": _utc_now(),
                "updated_at_utc": _utc_now(),
                "log": "",
                "result": None,
                "error": None,
                "task_id": None,
            }
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)
            self._jobs[job_id]["updated_at_utc"] = _utc_now()

    def append_log(self, job_id: str, text: str) -> None:
        with self._lock:
            self._jobs[job_id]["log"] += text
            self._jobs[job_id]["updated_at_utc"] = _utc_now()
            # Sniff the task_id out of the CLI output as it streams.
            if "task_id:" in text and self._jobs[job_id]["task_id"] is None:
                for line in text.splitlines():
                    if line.startswith("task_id:"):
                        self._jobs[job_id]["task_id"] = line.split(":", 1)[1].strip()
                        break

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            j = self._jobs.get(job_id)
            # Return a deep copy under the lock so the caller can serialize
            # the snapshot without racing with concurrent ``append_log`` or
            # ``update`` mutations. The ``log`` string is itself immutable,
            # but the surrounding dict and the nested ``request`` dict are
            # shared.
            return copy.deepcopy(j) if j is not None else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {k: copy.deepcopy(v) for k, v in j.items() if k != "log"}
                for j in self._jobs.values()
            ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------


def _make_handler(*, repo: Path, jobs: _JobRegistry):

    def _resolved():
        cfg = HarnessConfig.load(repo_root=repo)
        return cfg, cfg.resolved(repo)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            return

        # ----- helpers -------------------------------------------------

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")
            self._send_bytes(status, body, "application/json; charset=utf-8")

        def _send_text(self, status: int, text: str,
                       ctype: str = "text/plain; charset=utf-8") -> None:
            self._send_bytes(status, text.encode("utf-8"), ctype)

        def _send_bytes(self, status: int, data: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n > 0 else b""

        def _read_json_body(self) -> Any:
            raw = self._read_body()
            return json.loads(raw.decode("utf-8")) if raw else {}

        # ----- dispatch ------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._dispatch_get(urlparse(self.path).path)
            except Exception as e:
                self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._dispatch_post(urlparse(self.path).path)
            except json.JSONDecodeError as e:
                # Malformed body is a client error, not a server error.
                self._send_json(400, {"error": f"invalid json body: {e}"})
            except Exception as e:
                self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

        def _dispatch_get(self, p: str) -> None:
            if p in ("/", "/index.html"):
                self._serve_static("index.html", "text/html; charset=utf-8"); return
            if p.startswith("/static/"):
                self._serve_static(p[len("/static/"):], _guess_type(p)); return
            if p == "/api/config": self._api_config(); return
            if p == "/api/config/raw": self._api_config_raw(); return
            if p == "/api/onboarding": self._api_onboarding(); return
            if p == "/api/runs": self._api_list_runs(); return
            if p == "/api/scenarios": self._api_list_scenarios(); return
            if p == "/api/capabilities": self._api_list_capabilities(); return
            if p == "/api/playbooks": self._api_list_playbooks(); return
            if p == "/api/schemas": self._api_list_schemas(); return
            if p == "/api/bundle/export": self._api_bundle_export(); return
            if p == "/api/palette": self._api_palette(); return
            if p.startswith("/api/runs/"):
                self._api_run_subroute(p[len("/api/runs/"):]); return
            if p.startswith("/api/scenarios/"):
                self._api_scenario_subroute(p[len("/api/scenarios/"):]); return
            if p.startswith("/api/playbooks/"):
                self._api_playbook_get(unquote(p[len("/api/playbooks/"):])); return
            if p.startswith("/api/schemas/"):
                self._api_schema_get(unquote(p[len("/api/schemas/"):])); return
            if p.startswith("/api/jobs/"):
                self._api_job(p[len("/api/jobs/"):]); return
            self._send_text(404, "not found")

        def _dispatch_post(self, p: str) -> None:
            if p == "/api/implement":
                self._api_implement(self._read_json_body()); return
            if p == "/api/config/raw":
                self._api_config_raw_post(self._read_body().decode("utf-8")); return
            if p == "/api/config/validate":
                self._api_config_validate(self._read_body().decode("utf-8")); return
            if p == "/api/config/form":
                self._api_config_form(self._read_json_body()); return
            if p == "/api/init":
                self._api_init(self._read_json_body()); return
            if p == "/api/bundle/preview":
                self._api_bundle_preview(self._read_json_body()); return
            if p == "/api/bundle/import":
                self._api_bundle_import(self._read_json_body()); return
            if p == "/api/scenarios":
                self._api_scenario_create(self._read_json_body()); return
            if p.startswith("/api/playbooks/"):
                self._api_playbook_post(
                    unquote(p[len("/api/playbooks/"):]),
                    self._read_body().decode("utf-8")); return
            if p.startswith("/api/scenarios/"):
                sub = p[len("/api/scenarios/"):]
                parts = sub.split("/", 2)
                if len(parts) == 2 and parts[1] == "form":
                    self._api_scenario_form_post(parts[0], self._read_json_body())
                    return
                if len(parts) == 2 and parts[1] == "validate":
                    self._api_scenario_form_validate(parts[0], self._read_json_body())
                    return
                self._api_scenario_file_post(sub,
                                             self._read_body().decode("utf-8"))
                return
            self._send_text(404, "not found")

        # ----- static -------------------------------------------------

        def _serve_static(self, rel: str, ctype: str) -> None:
            base = STATIC_DIR.resolve()
            f = (STATIC_DIR / rel).resolve()
            try:
                f.relative_to(base)
            except ValueError:
                self._send_text(403, "forbidden"); return
            if not f.exists():
                self._send_text(404, "not found"); return
            self._send_bytes(200, f.read_bytes(), ctype)

        # ----- config -------------------------------------------------

        def _api_config(self) -> None:
            cfg, r = _resolved()
            self._send_json(200, {
                "repo": str(repo),
                "config_file": str(repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME),
                "config_exists": (repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME).exists(),
                "runs_dir": str(r.runs_dir),
                "sandbox_dir": str(r.sandbox_dir),
                "clean_workspace_dir": str(r.clean_workspace_dir),
                "scenarios_dir": str(r.scenarios_dir),
                "default_provider": r.default_provider,
                "policy": {
                    "max_code_iterations": r.policy.max_code_iterations,
                    "max_validation_attempts_per_iteration":
                        r.policy.max_validation_attempts_per_iteration,
                    "max_diagnostic_rounds_per_failure":
                        r.policy.max_diagnostic_rounds_per_failure,
                    "max_total_wall_clock_minutes":
                        r.policy.max_total_wall_clock_minutes,
                },
                "notes": cfg.notes,
            })

        def _api_config_raw(self) -> None:
            cf = repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME
            if cf.exists():
                self._send_text(200, cf.read_text(encoding="utf-8"),
                                "text/plain; charset=utf-8")
            else:
                self._send_text(200, DEFAULT_CONFIG_YAML,
                                "text/plain; charset=utf-8")

        def _api_config_raw_post(self, text: str) -> None:
            # Validate by parsing first.
            import yaml
            try:
                yaml.safe_load(text)
            except yaml.YAMLError as e:
                self._send_json(400, {"error": f"invalid yaml: {e}"}); return
            cd = repo / CONFIG_DIR_NAME
            cd.mkdir(parents=True, exist_ok=True)
            (cd / CONFIG_FILE_NAME).write_text(text, encoding="utf-8")
            self._send_json(200, {"ok": True})

        def _api_config_validate(self, text: str) -> None:
            """Dry-run a config body. Never writes; reports issues + resolved.

            Powers the live preview underneath the Build > Config form so a
            typo lights up before the user hits Save. Always returns 200
            with ``ok`` (true iff there are no ``error``-level issues).
            """
            import yaml
            issues: list[dict[str, str]] = []
            raw: Any = {}
            try:
                raw = yaml.safe_load(text) if text.strip() else {}
            except yaml.YAMLError as e:
                self._send_json(200, {
                    "ok": False,
                    "issues": [{"level": "error", "field": "_yaml",
                                "message": f"invalid YAML: {e}"}],
                    "form": None, "resolved": None,
                })
                return
            if raw is None:
                raw = {}
            if not isinstance(raw, dict):
                self._send_json(200, {
                    "ok": False,
                    "issues": [{"level": "error", "field": "_root",
                                "message": "top-level must be a mapping"}],
                    "form": None, "resolved": None,
                })
                return
            cfg, more = HarnessConfig.from_dict_with_issues(raw)
            issues.extend(more)
            r = cfg.resolved(repo)
            self._send_json(200, {
                "ok": not any(i["level"] == "error" for i in issues),
                "issues": issues,
                "form": _config_to_form(cfg),
                "resolved": {
                    "runs_dir": str(r.runs_dir),
                    "sandbox_dir": str(r.sandbox_dir),
                    "clean_workspace_dir": str(r.clean_workspace_dir),
                    "scenarios_dir": str(r.scenarios_dir),
                    "default_provider": r.default_provider,
                    "policy": {
                        "max_code_iterations": r.policy.max_code_iterations,
                        "max_validation_attempts_per_iteration":
                            r.policy.max_validation_attempts_per_iteration,
                        "max_diagnostic_rounds_per_failure":
                            r.policy.max_diagnostic_rounds_per_failure,
                        "max_total_wall_clock_minutes":
                            r.policy.max_total_wall_clock_minutes,
                    },
                    "notes": cfg.notes,
                },
            })

        def _api_config_form(self, body: Any) -> None:
            """Convert a structured form dict to canonical YAML.

            Body matches ``_config_to_form`` output (flat scalar fields).
            Returns the YAML text and a fresh validation report so the
            client can show both panes in one call.
            """
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            # Reconstruct a HarnessConfig-shaped dict from the form fields.
            # Policy fields stay top-level (HarnessConfig.from_dict_with_issues
            # accepts that and we want stable round-tripping).
            form = dict(body)
            cfg, issues = HarnessConfig.from_dict_with_issues(form)
            yaml_text = dump_canonical_yaml(cfg)
            r = cfg.resolved(repo)
            self._send_json(200, {
                "ok": not any(i["level"] == "error" for i in issues),
                "yaml": yaml_text,
                "issues": issues,
                "form": _config_to_form(cfg),
                "resolved": {
                    "runs_dir": str(r.runs_dir),
                    "sandbox_dir": str(r.sandbox_dir),
                    "clean_workspace_dir": str(r.clean_workspace_dir),
                    "scenarios_dir": str(r.scenarios_dir),
                    "default_provider": r.default_provider,
                    "policy": {
                        "max_code_iterations": r.policy.max_code_iterations,
                        "max_validation_attempts_per_iteration":
                            r.policy.max_validation_attempts_per_iteration,
                        "max_diagnostic_rounds_per_failure":
                            r.policy.max_diagnostic_rounds_per_failure,
                        "max_total_wall_clock_minutes":
                            r.policy.max_total_wall_clock_minutes,
                    },
                    "notes": cfg.notes,
                },
            })

        # ----- onboarding --------------------------------------------

        def _api_onboarding(self) -> None:
            """Snapshot of how 'set up' this repo is.

            Drives the first-run wizard so a brand new user sees what's
            done and what's still needed without leaving the UI.
            """
            cfg, r = _resolved()
            cf = repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME
            config_exists = cf.exists()
            scenarios_dir = r.scenarios_dir
            scenario_names: list[str] = []
            if scenarios_dir.exists():
                scenario_names = sorted(
                    d.name for d in scenarios_dir.iterdir() if d.is_dir()
                )
            runs_dir = r.runs_dir
            run_count = 0
            if runs_dir.exists():
                run_count = sum(
                    1 for d in runs_dir.iterdir()
                    if d.is_dir() and (d / "task_manifest.json").exists()
                )
            gi = repo / ".gitignore"
            gitignored = (
                gi.exists() and ".dev-loop/runs/" in gi.read_text(encoding="utf-8")
            )
            steps = [
                {
                    "id": "config",
                    "title": "Create .dev-loop/config.yaml",
                    "done": config_exists,
                    "detail": str(cf),
                },
                {
                    "id": "gitignore",
                    "title": "Ignore run artifacts in git",
                    "done": gitignored,
                    "detail": str(gi),
                },
                {
                    "id": "scenarios",
                    "title": "Install a replay scenario to try",
                    "done": bool(scenario_names),
                    "detail": str(scenarios_dir),
                    "scenarios": scenario_names,
                },
                {
                    "id": "first_run",
                    "title": "Run your first /implement loop",
                    "done": run_count > 0,
                    "detail": f"{run_count} run(s) recorded",
                },
            ]
            is_complete = all(s["done"] for s in steps)
            self._send_json(200, {
                "repo": str(repo),
                "repo_name": repo.name,
                "config_exists": config_exists,
                "gitignored": gitignored,
                "scenarios": scenario_names,
                "run_count": run_count,
                "starter_scenario": STARTER_SCENARIO_NAME,
                "starter_installed": STARTER_SCENARIO_NAME in scenario_names,
                "default_provider": r.default_provider,
                "is_complete": is_complete,
                "steps": steps,
            })

        def _api_init(self, body: Any) -> None:
            """One-click setup. Idempotent.

            Body (all optional):
              install_starter: bool   default True — copy hello-dev-loop scenario
              force: bool             default False — overwrite an existing config
            """
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            install_starter = bool(body.get("install_starter", True))
            force = bool(body.get("force", False))
            actions: list[str] = []
            cf, created = write_default_config(repo, force=force)
            if created:
                actions.append(f"wrote {cf}")
            elif force:
                actions.append(f"overwrote {cf}")
            else:
                actions.append(f"kept existing {cf}")
            if append_gitignore(repo):
                actions.append("updated .gitignore")
            if install_starter:
                _, r = _resolved()
                sp = write_starter_scenario(r.scenarios_dir)
                actions.append(f"installed starter scenario at {sp}")
            self._send_json(200, {"ok": True, "actions": actions})

        # ----- bundle (share config between repos) -------------------

        def _api_bundle_export(self) -> None:
            """Return this repo's exportable bundle.

            Served as ``application/json`` with a ``Content-Disposition``
            header so a browser save-as drops it into a sensibly-named
            file. The bundle is a pure JSON object.
            """
            bundle = build_bundle(repo)
            body = bundle_to_json(bundle).encode("utf-8")
            filename = f"{repo.name}-dev-loop-bundle.json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"',
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _api_bundle_preview(self, body: Any) -> None:
            """Dry-run an incoming bundle. Returns the diff vs. this repo.

            Body: ``{"bundle": <bundle-object>}`` — the bundle as a parsed
            JSON object so the client can paste, type or upload it.
            """
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            bundle = body.get("bundle")
            if bundle is None:
                self._send_json(400, {"error": "missing 'bundle' field"}); return
            try:
                preview = preview_apply(bundle, repo)
            except BundleError as e:
                self._send_json(400, {"error": str(e)}); return
            self._send_json(200, preview)

        def _api_bundle_import(self, body: Any) -> None:
            """Apply an incoming bundle.

            Body fields:
              ``bundle``       — required, the parsed bundle object
              ``on_conflict``  — ``skip`` (default), ``overwrite`` or
                                 ``rename``
              ``include``      — optional list of display paths to limit
                                 the write to (per-item UI checkboxes)
            """
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            bundle = body.get("bundle")
            if bundle is None:
                self._send_json(400, {"error": "missing 'bundle' field"}); return
            on_conflict = body.get("on_conflict") or "skip"
            include = body.get("include")
            if include is not None and not isinstance(include, list):
                self._send_json(400, {"error": "'include' must be a list of strings"}); return
            try:
                report = apply_bundle(
                    bundle, repo,
                    on_conflict=on_conflict,
                    include=include,
                )
            except BundleError as e:
                self._send_json(400, {"error": str(e)}); return
            self._send_json(200, report)

        # ----- runs ---------------------------------------------------

        def _api_list_runs(self) -> None:
            _, r = _resolved()
            runs_dir = r.runs_dir
            out: list[dict[str, Any]] = []
            if runs_dir.exists():
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
                        "iterations": _count_iterations(d),
                        "goal": tc.get("implementation_goal"),
                        "duration_seconds": _run_duration_seconds(data),
                    })
            self._send_json(200, {"runs": out, "jobs": jobs.list()})

        def _api_run_subroute(self, sub: str) -> None:
            _, r = _resolved()
            runs_dir = r.runs_dir
            parts = sub.split("/")
            task_id = parts[0]
            run_root = runs_dir / task_id
            if not run_root.exists():
                self._send_json(404, {"error": "task not found"}); return
            if len(parts) == 1:
                self._send_json(200, read_json(run_root / "task_manifest.json")); return
            section = parts[1]
            if section == "report":
                md = run_root / "final_review_report.md"
                self._send_text(200, md.read_text(encoding="utf-8") if md.exists() else "(no report yet)",
                                "text/markdown; charset=utf-8"); return
            if section == "report.json":
                f = run_root / "final_review_report.json"
                if f.exists():
                    self._send_json(200, read_json(f))
                else:
                    self._send_json(404, {"error": "no structured report"})
                return
            if section == "audit":
                f = run_root / "capability_audit.jsonl"
                self._send_text(200, f.read_text(encoding="utf-8") if f.exists() else "")
                return
            if section == "iteration" and len(parts) >= 3:
                n = int(parts[2])
                iter_dir = run_root / "iterations" / f"iter-{n:03d}"
                if not iter_dir.exists():
                    self._send_json(404, {"error": "iteration not found"}); return
                if len(parts) == 3:
                    self._send_json(200, _read_safe_json(iter_dir / "manifest.json")); return
                if parts[3] == "patch":
                    pf = iter_dir / "patch.diff"
                    self._send_text(200, pf.read_text(encoding="utf-8") if pf.exists() else ""); return
                if parts[3] == "attempts":
                    self._send_json(200, _summarize_attempts(iter_dir)); return
                if parts[3] == "attempt" and len(parts) >= 5:
                    a = int(parts[4])
                    self._send_json(200, _attempt_dump(iter_dir / "validations" / f"attempt-{a:03d}"))
                    return
            self._send_text(404, "not found")

        # ----- scenarios ---------------------------------------------

        def _api_list_scenarios(self) -> None:
            _, r = _resolved()
            sc_dir = r.scenarios_dir
            out: list[dict[str, Any]] = []
            if sc_dir.exists():
                for d in sorted(sc_dir.iterdir()):
                    if not d.is_dir():
                        continue
                    req = d / "task_request.md"
                    out.append({
                        "name": d.name,
                        "path": str(d),
                        "files": sorted(f.name for f in d.iterdir() if f.is_file()),
                        "request_preview":
                            (req.read_text(encoding="utf-8")[:600] if req.exists() else None),
                    })
            self._send_json(200, {"scenarios": out, "scenarios_dir": str(sc_dir)})

        def _api_scenario_subroute(self, sub: str) -> None:
            _, r = _resolved()
            sc_dir = r.scenarios_dir
            parts = sub.split("/", 2)
            name = parts[0]
            if not _valid_scenario_name(name):
                self._send_json(404, {"error": "scenario not found"}); return
            d = sc_dir / name
            # Belt-and-braces: a clever name (or symlink) could still let
            # ``d`` resolve outside ``sc_dir``. Guard against that explicitly
            # so the per-file traversal check below cannot be bypassed.
            if not _is_within(d, sc_dir):
                self._send_text(403, "forbidden"); return
            if not d.exists() or not d.is_dir():
                self._send_json(404, {"error": "scenario not found"}); return
            if len(parts) == 1:
                files = []
                for f in sorted(d.iterdir()):
                    if f.is_file():
                        files.append({"name": f.name, "size": f.stat().st_size})
                    elif f.is_dir():
                        files.append({"name": f.name + "/", "size": None})
                self._send_json(200, {"name": name, "path": str(d), "files": files}); return
            if parts[1] == "form" and len(parts) == 2:
                form = load_scenario_form(d)
                issues = [i.to_dict() for i in validate_scenario_form(form.to_dict())]
                self._send_json(200, {
                    "form": form.to_dict(),
                    "path": str(d),
                    "issues": issues,
                    "ok": not any(i["level"] == "error" for i in issues),
                })
                return
            if parts[1] == "file" and len(parts) == 3:
                f = (d / parts[2]).resolve()
                if not _is_within(f, sc_dir):
                    self._send_text(403, "forbidden"); return
                if not f.exists() or not f.is_file():
                    self._send_text(404, "not found"); return
                ctype = "application/json; charset=utf-8" if f.suffix == ".json" else "text/plain; charset=utf-8"
                self._send_text(200, f.read_text(encoding="utf-8"), ctype); return
            self._send_text(404, "not found")

        def _api_scenario_file_post(self, sub: str, text: str) -> None:
            _, r = _resolved()
            sc_dir = r.scenarios_dir
            parts = sub.split("/", 2)
            if len(parts) < 3 or parts[1] != "file":
                self._send_text(404, "not found"); return
            name = parts[0]
            if not _valid_scenario_name(name):
                self._send_json(400, {"error": "invalid scenario name"}); return
            d = sc_dir / name
            if not _is_within(d, sc_dir):
                self._send_text(403, "forbidden"); return
            d.mkdir(parents=True, exist_ok=True)
            f = (d / parts[2]).resolve()
            # Use ``sc_dir`` as the boundary (not ``d``) so a malicious
            # ``name`` that already escaped can't be used to anchor a
            # downstream traversal back into the parent.
            if not _is_within(f, sc_dir):
                self._send_text(403, "forbidden"); return
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            self._send_json(200, {"ok": True, "path": str(f)})

        def _api_scenario_create(self, body: Any) -> None:
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            _, r = _resolved()
            sc_dir = r.scenarios_dir
            name = (body.get("name") or "").strip()
            if not _valid_scenario_name(name):
                self._send_json(400, {"error": "invalid scenario name"}); return
            d = sc_dir / name
            if not _is_within(d, sc_dir):
                self._send_text(403, "forbidden"); return
            if d.exists():
                self._send_json(400, {"error": "already exists"}); return
            # Build the initial form from anything the caller passed and
            # let ``dump_scenario_files`` write the canonical files. This
            # keeps "create" and "save" on the same code path.
            request = body.get("task_request") or f"# {name}\n\nDescribe the request here.\n"
            goal = (body.get("implementation_goal") or "").strip()
            if not goal:
                goal = request.strip().splitlines()[0].lstrip("# ").strip() or name
            tc = default_task_contract()
            tc["implementation_goal"] = goal
            form = {
                "task_request": request,
                "task_contract": tc,
                "implementation_result": default_implementation_result(),
                "e2e_result": default_e2e_result(),
                "extras": {},
            }
            d.mkdir(parents=True)
            for fname, text in dump_scenario_files(form).items():
                (d / fname).write_text(text, encoding="utf-8")
            self._send_json(200, {"ok": True, "name": name, "path": str(d)})

        def _api_scenario_form_post(self, name: str, body: Any) -> None:
            """Save a structured scenario form.

            Validates, then writes the four projected files atomically
            (via a temp-and-replace dance per file). Extras are merged
            back in by ``dump_scenario_files`` so power-user fields are
            preserved.
            """
            if not _valid_scenario_name(name):
                self._send_json(400, {"error": "invalid scenario name"}); return
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            form = body.get("form")
            if not isinstance(form, dict):
                self._send_json(400, {"error": "missing 'form' object"}); return
            _, r = _resolved()
            sc_dir = r.scenarios_dir
            d = sc_dir / name
            if not _is_within(d, sc_dir):
                self._send_text(403, "forbidden"); return
            if not d.exists() or not d.is_dir():
                self._send_json(404, {"error": "scenario not found"}); return
            issues = [i.to_dict() for i in validate_scenario_form(form)]
            errors = [i for i in issues if i["level"] == "error"]
            if errors:
                self._send_json(400, {
                    "ok": False, "issues": issues, "error": "validation failed",
                })
                return
            files = dump_scenario_files(form)
            for fname, text in files.items():
                _atomic_write_text(d / fname, text)
            # Echo back a fresh form so the client picks up any
            # normalization (defaults filled in, etc.).
            fresh = load_scenario_form(d)
            self._send_json(200, {
                "ok": True,
                "issues": issues,
                "written": sorted(files.keys()),
                "form": fresh.to_dict(),
            })

        def _api_scenario_form_validate(self, name: str, body: Any) -> None:
            """Dry-run validation for the form. Never writes."""
            if not _valid_scenario_name(name):
                self._send_json(400, {"error": "invalid scenario name"}); return
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            form = body.get("form")
            if not isinstance(form, dict):
                self._send_json(400, {"error": "missing 'form' object"}); return
            issues = [i.to_dict() for i in validate_scenario_form(form)]
            self._send_json(200, {
                "ok": not any(i["level"] == "error" for i in issues),
                "issues": issues,
            })

        # ----- capabilities, playbooks, schemas -----------------------

        def _api_list_capabilities(self) -> None:
            from ..capabilities import load_default_registry
            reg = load_default_registry()
            specs = []
            for name in reg.all_names():
                s = reg.spec(name)
                specs.append({
                    "name": s.name,
                    "category": s.category,
                    "agent_requestable": s.agent_requestable,
                    "timeout_seconds": s.timeout_seconds,
                    "redacts_output": s.redacts_output,
                    "audit": s.audit,
                    "forced_params": s.forced_params,
                })
            self._send_json(200, {"capabilities": specs})

        def _api_list_playbooks(self) -> None:
            files = sorted(p.name for p in PLAYBOOK_DIR.glob("*.md"))
            self._send_json(200, {"playbooks": files})

        def _api_playbook_get(self, name: str) -> None:
            f = (PLAYBOOK_DIR / name).resolve()
            try:
                f.relative_to(PLAYBOOK_DIR.resolve())
            except ValueError:
                self._send_text(403, "forbidden"); return
            if not f.exists():
                self._send_text(404, "not found"); return
            self._send_text(200, f.read_text(encoding="utf-8"), "text/markdown; charset=utf-8")

        def _api_playbook_post(self, name: str, text: str) -> None:
            # Saved into the harness package dir (the repo where dev-loop
            # lives). For per-repo overrides users would maintain their own
            # fork — that's intentional for v1.
            f = (PLAYBOOK_DIR / name).resolve()
            try:
                f.relative_to(PLAYBOOK_DIR.resolve())
            except ValueError:
                self._send_text(403, "forbidden"); return
            f.write_text(text, encoding="utf-8")
            # Bust the in-process ``lru_cache`` on ``load_playbook`` so any
            # in-process consumer (tests, future in-proc agent runner) sees
            # the new text on the next call.
            from ..playbooks import load_playbook
            load_playbook.cache_clear()
            self._send_json(200, {"ok": True})

        def _api_list_schemas(self) -> None:
            files = sorted(p.name for p in SCHEMA_DIR.glob("*.json"))
            self._send_json(200, {"schemas": files})

        def _api_schema_get(self, name: str) -> None:
            f = (SCHEMA_DIR / name).resolve()
            try:
                f.relative_to(SCHEMA_DIR.resolve())
            except ValueError:
                self._send_text(403, "forbidden"); return
            if not f.exists():
                self._send_text(404, "not found"); return
            self._send_text(200, f.read_text(encoding="utf-8"),
                            "application/json; charset=utf-8")

        # ----- command palette (Cmd+K) -------------------------------

        def _api_palette(self) -> None:
            """Unified jump-to index for the Cmd+K command palette.

            One round-trip returns every destination the user might want to
            jump to: tabs, builder sections, run-tab subviews, every
            scenario (with its goal), every playbook, every schema, every
            recent run (newest 60, with status + goal), and a small set of
            verb-style quick actions. The client fuzzy-matches client-side;
            this endpoint just gathers the corpus so we don't have to
            chase three other endpoints to populate the palette.
            """
            _, r = _resolved()
            items: list[dict[str, Any]] = []

            # 1. Tabs.
            for tab, label, hint in (
                ("build", "Build", "configure this repo"),
                ("run", "Run", "launch a /implement loop"),
                ("analyze", "Analyze", "browse past runs"),
            ):
                items.append({
                    "kind": "tab", "id": tab, "title": label,
                    "subtitle": hint, "group": "Tabs",
                    "keywords": f"go to {label.lower()}",
                })

            # 2. Builder sections.
            for sec, label, hint in (
                ("overview", "Overview", "flow + onboarding"),
                ("config", "Config", ".dev-loop/config.yaml"),
                ("capabilities", "Capabilities",
                 "registered external actions"),
                ("playbooks", "Playbooks", "agent prompts"),
                ("schemas", "Schemas", "JSON schemas"),
                ("scenarios", "Scenarios", "replay fixtures"),
                ("share", "Share & reuse", "export/import bundles"),
            ):
                items.append({
                    "kind": "builder", "id": sec,
                    "title": f"Build · {label}",
                    "subtitle": hint, "group": "Build sections",
                    "keywords": f"build {label.lower()} {sec}",
                })

            # 3. Run-tab subviews (only useful when a run is open, but
            # listing them lets the user keyboard-jump from anywhere).
            for sub, label in (
                ("report", "Report"), ("iterations", "Iterations"),
                ("audit", "Audit log"), ("raw", "Raw report"),
            ):
                items.append({
                    "kind": "subview", "id": sub,
                    "title": f"Analyze · {label}",
                    "subtitle": "open in current run",
                    "group": "Analyze subviews",
                    "keywords": f"analyze {label.lower()}",
                })

            # 4. Scenarios — read each one's goal for matching.
            sc_dir = r.scenarios_dir
            if sc_dir.exists():
                for d in sorted(sc_dir.iterdir()):
                    if not d.is_dir():
                        continue
                    goal = ""
                    tc_path = d / "task_contract.json"
                    if tc_path.exists():
                        try:
                            tc = read_json(tc_path)
                            goal = (tc.get("implementation_goal") or "")[:120]
                        except Exception:
                            goal = ""
                    items.append({
                        "kind": "scenario", "id": d.name,
                        "title": d.name,
                        "subtitle": goal or "(no goal recorded)",
                        "group": "Scenarios",
                        "keywords": f"scenario replay {d.name} {goal}",
                    })

            # 5. Playbooks + schemas (lightweight, just filenames).
            for p in sorted(PLAYBOOK_DIR.glob("*.md")):
                items.append({
                    "kind": "playbook", "id": p.name, "title": p.name,
                    "subtitle": "playbook", "group": "Playbooks",
                    "keywords": f"playbook {p.name}",
                })
            for p in sorted(SCHEMA_DIR.glob("*.json")):
                items.append({
                    "kind": "schema", "id": p.name, "title": p.name,
                    "subtitle": "schema", "group": "Schemas",
                    "keywords": f"schema {p.name}",
                })

            # 6. Recent runs — newest first, capped at 60 to keep payload tiny.
            runs_dir = r.runs_dir
            if runs_dir.exists():
                run_dirs = sorted(
                    (d for d in runs_dir.iterdir() if d.is_dir()),
                    reverse=True,
                )
                for d in run_dirs[:60]:
                    tm = d / "task_manifest.json"
                    if not tm.exists():
                        continue
                    try:
                        data = read_json(tm)
                    except Exception:
                        continue
                    tc = data.get("task_contract") or {}
                    status = (data.get("final_status")
                              or data.get("status") or "")
                    goal = (tc.get("implementation_goal") or "")[:120]
                    items.append({
                        "kind": "run", "id": data.get("task_id", d.name),
                        "title": data.get("task_id", d.name),
                        "subtitle": (
                            f"[{status}] {goal}" if status else goal
                        ) or "(no contract)",
                        "group": "Runs",
                        "status": status,
                        "keywords": f"run {data.get('task_id', d.name)} "
                                    f"{status} {goal}",
                    })

            # 7. Verb-style quick actions. These do something rather than
            # navigate, but live in the same palette to keep one keyboard
            # shortcut for "do anything".
            items.append({
                "kind": "action", "id": "scenario.new",
                "title": "New scenario…",
                "subtitle": "create a replay fixture",
                "group": "Actions",
                "keywords": "new scenario create add replay",
            })
            items.append({
                "kind": "action", "id": "shortcuts.help",
                "title": "Keyboard shortcuts",
                "subtitle": "show the cheat-sheet",
                "group": "Actions",
                "keywords": "help shortcuts keys cheat sheet",
            })

            self._send_json(200, {"items": items})

        # ----- jobs / implement --------------------------------------

        def _api_job(self, job_id: str) -> None:
            job = jobs.get(job_id)
            if job is None:
                self._send_json(404, {"error": "job not found"}); return
            self._send_json(200, job)

        def _api_implement(self, body: Any) -> None:
            if not isinstance(body, dict):
                self._send_json(400, {"error": "body must be a JSON object"}); return
            _, r = _resolved()
            request = (body.get("request") or "").strip()
            if not request:
                self._send_json(400, {"error": "missing 'request'"}); return
            provider = body.get("provider") or r.default_provider
            scenario = body.get("replay_scenario")
            max_iter = body.get("max_iterations")
            if provider == "replay" and scenario:
                # Accept a bare scenario name and resolve it.
                sp = Path(scenario)
                if not sp.is_absolute() and not (repo / scenario).exists():
                    sp = r.scenarios_dir / scenario
                    if sp.exists():
                        scenario = str(sp)
            job_id = jobs.create({
                "request": request, "provider": provider,
                "replay_scenario": scenario, "max_iterations": max_iter,
            })
            t = threading.Thread(
                target=_run_job, daemon=True,
                args=(job_id, jobs, repo, request, provider, scenario, max_iter),
            )
            t.start()
            self._send_json(202, {"job_id": job_id})

    return Handler


def _config_to_form(cfg: HarnessConfig) -> dict[str, Any]:
    """Flat dict the Build > Config form binds to. One key per input."""
    return {
        "runs_dir": cfg.runs_dir,
        "default_provider": cfg.default_provider,
        "scenarios_dir": cfg.scenarios_dir,
        "sandbox_dir": cfg.sandbox_dir,
        "clean_workspace_dir": cfg.clean_workspace_dir,
        "notes": cfg.notes,
        "max_code_iterations": cfg.max_code_iterations,
        "max_validation_attempts_per_iteration":
            cfg.max_validation_attempts_per_iteration,
        "max_diagnostic_rounds_per_failure":
            cfg.max_diagnostic_rounds_per_failure,
        "max_total_wall_clock_minutes": cfg.max_total_wall_clock_minutes,
    }


def _valid_scenario_name(name: str) -> bool:
    """A scenario name must be a single safe path segment.

    Rejects empty, hidden (leading dot, which also rejects ``..``), path
    separators on any OS, NUL bytes, and URL-encoded separators that
    survived ``urlparse`` (which intentionally does not decode ``%2F``).
    """
    if not name:
        return False
    if name.startswith("."):
        return False
    for bad in ("/", "\\", "\x00", "%2f", "%2F", "%5c", "%5C"):
        if bad in name:
            return False
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via temp-and-replace.

    Per-file atomicity matters for the scenario form save: a four-file
    write that's interrupted half-way should leave each file either
    fully written (new bytes) or fully untouched (old bytes), never
    truncated. ``os.replace`` is atomic on POSIX and Windows.
    """
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _is_within(p: Path, root: Path) -> bool:
    """True iff ``p.resolve()`` is the same as ``root.resolve()`` or
    sits inside it."""
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _read_safe_json(p: Path) -> Any:
    if not p.exists():
        return {}
    try:
        return read_json(p)
    except Exception as e:
        return {"_error": str(e)}


def _run_duration_seconds(manifest: dict[str, Any]) -> int | None:
    """Wall-clock seconds between ``created_at_utc`` and ``updated_at_utc``.

    Returns ``None`` if either timestamp is missing or unparseable so the
    UI can render an em dash rather than a misleading zero.
    """
    a = manifest.get("created_at_utc")
    b = manifest.get("updated_at_utc")
    if not a or not b:
        return None
    try:
        ta = datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
        tb = datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return max(0, int((tb - ta).total_seconds()))


def _count_iterations(run_dir: Path) -> int:
    iters = run_dir / "iterations"
    if not iters.exists():
        return 0
    # Only count iteration dirs (``iter-NNN/``); a stray file in the dir
    # (e.g. a .DS_Store from a checkout, or a tmp scratch file) must not
    # inflate the count or skew downstream summaries.
    return sum(1 for d in iters.iterdir() if d.is_dir())


def _summarize_attempts(iter_dir: Path) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    v = iter_dir / "validations"
    if v.exists():
        for d in sorted(v.iterdir()):
            if not d.is_dir():
                continue
            outcome = _read_safe_json(d / "outcome.json")
            e2e = _read_safe_json(d / "e2e_result.json")
            triage_action = (outcome.get("triage") or {}).get("next_action") if isinstance(outcome, dict) else None
            out.append({
                "name": d.name,
                "outcome": outcome.get("outcome") if isinstance(outcome, dict) else None,
                "e2e_status": (outcome.get("e2e") or {}).get("status") if isinstance(outcome, dict) else None,
                "triage_action": triage_action,
                "e2e_raw": e2e,
            })
    return {"attempts": out}


def _attempt_dump(attempt_dir: Path) -> dict[str, Any]:
    if not attempt_dir.exists():
        return {"_error": "attempt dir not found"}
    files = {}
    for f in attempt_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(attempt_dir))
        if f.suffix == ".json":
            files[rel] = _read_safe_json(f)
        else:
            try:
                files[rel] = f.read_text(encoding="utf-8")
            except Exception:
                files[rel] = "<binary>"
    return {"path": str(attempt_dir), "files": files}


def _run_job(
    job_id: str,
    jobs: _JobRegistry,
    repo: Path,
    request: str,
    provider: str,
    replay_scenario: str | None,
    max_iterations: int | None,
) -> None:
    jobs.update(job_id, status="running")
    cmd = [sys.executable, "-m", "harness.cli",
           "--repo", str(repo),
           "implement",
           "--request", request,
           "--provider", provider]
    if replay_scenario:
        cmd += ["--replay-scenario", replay_scenario]
    if max_iterations:
        cmd += ["--max-iterations", str(max_iterations)]
    jobs.append_log(job_id, "$ " + " ".join(cmd) + "\n")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            jobs.append_log(job_id, line)
        rc = proc.wait()
        jobs.update(
            job_id,
            status="completed" if rc == 0 else "failed",
            result={"returncode": rc},
        )
    except Exception as e:
        jobs.update(job_id, status="errored", error=str(e))


def _guess_type(path: str) -> str:
    if path.endswith(".html"): return "text/html; charset=utf-8"
    if path.endswith(".css"): return "text/css; charset=utf-8"
    if path.endswith(".js"): return "application/javascript; charset=utf-8"
    if path.endswith(".json"): return "application/json; charset=utf-8"
    if path.endswith(".svg"): return "image/svg+xml"
    return "application/octet-stream"
