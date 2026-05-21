"""dev-loop CLI.

Subcommands:

  dev-loop init                           # scaffold .dev-loop/config.yaml
  dev-loop doctor                         # one-shot setup diagnostics
  dev-loop implement --request "..." ...  # run the autonomous loop
  dev-loop config show                    # print the resolved config
  dev-loop config validate                # lint .dev-loop/config.yaml
  dev-loop schema validate <file> <schema>
  dev-loop replay <scenario>              # one-liner replay
  dev-loop runs ls                        # list runs in the ledger
  dev-loop runs show <task-id>            # summarize one run
  dev-loop runs diff <a> <b>              # compare two runs (CLI mirror of UI)
  dev-loop scenarios ls                   # list replay scenarios (lint status)
  dev-loop scenarios show <name>          # detail view of one scenario
  dev-loop scenarios validate [name]      # lint one or all scenarios
  dev-loop capabilities ls                # list capabilities in the registry
  dev-loop capabilities show <name>       # detail view of one capability
  dev-loop playbooks ls                   # list agent playbooks (built-in + override)
  dev-loop playbooks show <name>          # print one playbook + metadata
  dev-loop bundle export [--out FILE]     # pack config+scenarios+playbooks
  dev-loop bundle import FILE [--apply]   # preview / apply a bundle
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import schemas
from .agents import create_runner
from .bundle import (
    BundleError,
    apply_bundle,
    build_bundle,
    bundle_to_json,
    preview_apply,
    validate_bundle,
)
from .capabilities import list_capabilities, load_default_registry, show_capability
from .playbooks import list_playbooks, show_playbook
from .config import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    STARTER_SCENARIO_NAME,
    HarnessConfig,
    append_gitignore,
    write_default_config,
    write_starter_scenario,
)
from .doctor import (
    doctor_exit_code,
    format_checks,
    format_summary,
    run_doctor,
    to_json as doctor_to_json,
)
from .orchestrator import Orchestrator, OrchestratorConfig
from .runs import diff_runs, list_runs, show_run
from .scenarios import list_scenarios, show_scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev-loop", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="repository root (default: cwd)")
    parser.add_argument("--config", type=Path, default=None,
                        help="explicit config file path")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="scaffold .dev-loop/config.yaml in this repo")
    p_init.add_argument("--force", action="store_true",
                        help="overwrite existing config")
    p_init.add_argument("--starter", action="store_true",
                        help="also install the 'hello-dev-loop' starter scenario "
                             "so 'dev-loop replay hello-dev-loop' just works")

    p_impl = sub.add_parser("implement", help="run the autonomous loop")
    p_impl.add_argument("--request", required=True, help="feature request prompt")
    p_impl.add_argument("--provider", default=None,
                        help="provider override (replay, claude, codex)")
    p_impl.add_argument("--replay-scenario", default=None,
                        help="scenario directory or name (for --provider replay)")
    p_impl.add_argument("--max-iterations", type=int, default=None,
                        help="override loop_policy.max_code_iterations")

    p_cfg = sub.add_parser("config", help="config inspection")
    cfg_sub = p_cfg.add_subparsers(dest="cfg_cmd", required=True)
    cfg_sub.add_parser("show", help="print resolved configuration")
    p_cfg_val = cfg_sub.add_parser(
        "validate",
        help="lint .dev-loop/config.yaml (typos, type errors, unusual values)",
    )
    p_cfg_val.add_argument(
        "--strict", action="store_true",
        help="exit non-zero on warnings as well as errors",
    )

    p_doctor = sub.add_parser(
        "doctor",
        help="one-shot setup diagnostics (config, gitignore, runs dir, "
             "scenarios, provider CLI, ledger health)",
    )
    p_doctor.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of a human-readable report",
    )
    p_doctor.add_argument(
        "--strict", action="store_true",
        help="exit non-zero on warnings as well as errors",
    )

    p_schema = sub.add_parser("schema", help="schema utilities")
    sc_sub = p_schema.add_subparsers(dest="sc_cmd", required=True)
    p_val = sc_sub.add_parser("validate", help="validate a JSON file against a schema")
    p_val.add_argument("file", type=Path)
    p_val.add_argument("schema", help="schema file name, e.g. task_contract.v1.json")

    p_replay = sub.add_parser("replay",
                              help="convenience: run implement with --provider replay")
    p_replay.add_argument("scenario", help="scenario directory or name")
    p_replay.add_argument("--request", default=None,
                          help="overrides the scenario's task_request.md")

    p_runs = sub.add_parser(
        "runs", help="inspect the run ledger (list / show past runs)",
    )
    runs_sub = p_runs.add_subparsers(dest="runs_cmd", required=True)
    p_runs_ls = runs_sub.add_parser("ls", help="list runs newest-first")
    p_runs_ls.add_argument(
        "--limit", type=int, default=20,
        help="show at most this many runs (default: 20; use 0 for all)",
    )
    p_runs_ls.add_argument(
        "--status",
        help="filter by final_status (e.g. passed, failed_inconclusive)",
    )
    p_runs_ls.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable table",
    )
    p_runs_show = runs_sub.add_parser(
        "show", help="print a detailed summary of one run",
    )
    p_runs_show.add_argument(
        "task_id",
        help="task id (directory name under runs_dir) or 'last' for the "
             "most recent run",
    )
    p_runs_show.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable summary",
    )

    p_runs_diff = runs_sub.add_parser(
        "diff",
        help="compare two runs (CLI mirror of the Analyze tab's compare view)",
    )
    p_runs_diff.add_argument(
        "a",
        help="baseline task id; 'last' = newest, 'last-N' = N-th newest "
             "(so 'diff last-1 last' compares the previous run to the newest)",
    )
    p_runs_diff.add_argument(
        "b",
        help="comparison task id; same 'last' / 'last-N' aliases as <a>",
    )
    p_runs_diff.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable diff",
    )

    p_scn = sub.add_parser(
        "scenarios",
        help="inspect or lint replay scenarios (list / show / validate)",
    )
    scn_sub = p_scn.add_subparsers(dest="scenarios_cmd", required=True)
    p_scn_ls = scn_sub.add_parser("ls", help="list scenarios in scenarios_dir")
    p_scn_ls.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable table",
    )
    p_scn_show = scn_sub.add_parser(
        "show", help="print a detailed summary of one scenario",
    )
    p_scn_show.add_argument("name", help="scenario directory name")
    p_scn_show.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable summary",
    )
    p_scn_val = scn_sub.add_parser(
        "validate",
        help="lint one or all scenarios (mirrors the UI's structured form "
             "validator; safe to gate CI on)",
    )
    p_scn_val.add_argument(
        "name", nargs="?",
        help="scenario name (omit to validate every scenario in scenarios_dir)",
    )
    p_scn_val.add_argument(
        "--strict", action="store_true",
        help="exit non-zero on warnings as well as errors",
    )
    p_scn_val.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON instead of a human-readable report",
    )

    p_caps = sub.add_parser(
        "capabilities",
        help="inspect the capability registry (ls / show) — same source of "
             "truth as the Build > Capabilities UI tab",
    )
    caps_sub = p_caps.add_subparsers(dest="capabilities_cmd", required=True)
    p_caps_ls = caps_sub.add_parser(
        "ls", help="list every capability grouped by category",
    )
    p_caps_ls.add_argument(
        "--category",
        help="filter by category (local_only, real_dev_internal, "
             "real_dev_agent_requestable)",
    )
    p_caps_ls.add_argument(
        "--agent-requestable", action="store_true",
        help="only show capabilities the agent is allowed to request",
    )
    p_caps_ls.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable table",
    )
    p_caps_show = caps_sub.add_parser(
        "show", help="print full details for one capability",
    )
    p_caps_show.add_argument("name", help="capability name (e.g. local_build)")
    p_caps_show.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable summary",
    )

    p_pb = sub.add_parser(
        "playbooks",
        help="inspect agent playbooks (ls / show) — same source of truth as "
             "the Build > Playbooks UI tab",
    )
    pb_sub = p_pb.add_subparsers(dest="playbooks_cmd", required=True)
    p_pb_ls = pb_sub.add_parser(
        "ls",
        help="list built-in playbooks and per-repo overrides",
    )
    p_pb_ls.add_argument(
        "--overridden-only", action="store_true",
        help="only show playbooks the current repo has overridden",
    )
    p_pb_ls.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable table",
    )
    p_pb_show = pb_sub.add_parser(
        "show", help="print a playbook (text + source + agent-phase bindings)",
    )
    p_pb_show.add_argument(
        "name", help="playbook filename (e.g. implement_feature.v1.md)",
    )
    p_pb_show.add_argument(
        "--metadata-only", action="store_true",
        help="omit the body and print only the summary fields",
    )
    p_pb_show.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of a human-readable summary",
    )

    p_ui = sub.add_parser("ui", help="launch the local web UI")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--no-browser", action="store_true",
                      help="don't try to open a browser window")

    p_bundle = sub.add_parser(
        "bundle",
        help="export or import a portable config bundle "
             "(config + scenarios + playbooks)",
    )
    b_sub = p_bundle.add_subparsers(dest="bundle_cmd", required=True)
    p_b_export = b_sub.add_parser("export", help="write a bundle for this repo")
    p_b_export.add_argument("--out", type=Path, default=None,
                            help="write to this file instead of stdout")
    p_b_export.add_argument("--note", default="",
                            help="optional human-readable note shipped with the bundle")
    p_b_import = b_sub.add_parser(
        "import",
        help="preview (default) or apply a bundle to this repo",
    )
    p_b_import.add_argument("file", type=Path, help="bundle JSON file")
    p_b_import.add_argument("--apply", action="store_true",
                            help="actually write files (default is dry-run preview)")
    p_b_import.add_argument(
        "--on-conflict",
        choices=("skip", "overwrite", "rename"),
        default="skip",
        help="what to do when a destination file already differs",
    )

    args = parser.parse_args(argv)
    repo = args.repo.resolve()

    if args.cmd in ("implement", "replay"):
        try:
            _, _, issues = HarnessConfig.load_with_issues(
                repo_root=repo, explicit_path=args.config,
            )
        except (ValueError, OSError) as e:
            print(f"error: failed to read config: {e}", file=sys.stderr)
            return 2
        errors = [i for i in issues if i["level"] == "error"]
        if errors:
            print(
                "error: .dev-loop/config.yaml has errors — "
                "run `dev-loop config validate` to inspect:",
                file=sys.stderr,
            )
            for it in errors:
                print(f"  - {it['field']}: {it['message']}", file=sys.stderr)
            return 2

    if args.cmd == "init":
        return _cmd_init(repo, force=args.force, starter=args.starter)

    if args.cmd == "doctor":
        return _cmd_doctor(
            repo, explicit=args.config,
            as_json=args.json, strict=args.strict,
        )

    if args.cmd == "config":
        if args.cfg_cmd == "validate":
            return _cmd_config_validate(
                repo, explicit=args.config, strict=args.strict,
            )
        return _cmd_config_show(repo, explicit=args.config)

    if args.cmd == "schema":
        return _cmd_schema_validate(args.file, args.schema)

    if args.cmd == "capabilities":
        if args.capabilities_cmd == "ls":
            return _cmd_capabilities_ls(
                category=args.category,
                agent_only=args.agent_requestable,
                as_json=args.json,
            )
        if args.capabilities_cmd == "show":
            return _cmd_capabilities_show(name=args.name, as_json=args.json)

    if args.cmd == "playbooks":
        if args.playbooks_cmd == "ls":
            return _cmd_playbooks_ls(
                repo=repo,
                overridden_only=args.overridden_only,
                as_json=args.json,
            )
        if args.playbooks_cmd == "show":
            return _cmd_playbooks_show(
                repo=repo,
                name=args.name,
                metadata_only=args.metadata_only,
                as_json=args.json,
            )

    cfg = HarnessConfig.load(repo_root=repo, explicit_path=args.config)
    resolved = cfg.resolved(repo)

    if args.cmd == "runs":
        if args.runs_cmd == "ls":
            return _cmd_runs_ls(
                resolved.runs_dir,
                limit=args.limit, status=args.status, as_json=args.json,
            )
        if args.runs_cmd == "show":
            return _cmd_runs_show(
                resolved.runs_dir, task_id=args.task_id, as_json=args.json,
            )
        if args.runs_cmd == "diff":
            return _cmd_runs_diff(
                resolved.runs_dir, a=args.a, b=args.b, as_json=args.json,
            )

    if args.cmd == "scenarios":
        if args.scenarios_cmd == "ls":
            return _cmd_scenarios_ls(
                resolved.scenarios_dir, as_json=args.json,
            )
        if args.scenarios_cmd == "show":
            return _cmd_scenarios_show(
                resolved.scenarios_dir, name=args.name, as_json=args.json,
            )
        if args.scenarios_cmd == "validate":
            return _cmd_scenarios_validate(
                resolved.scenarios_dir,
                name=args.name, strict=args.strict, as_json=args.json,
            )

    if args.cmd == "implement":
        return _cmd_implement(
            repo=repo, resolved=resolved,
            request=args.request,
            provider=args.provider or resolved.default_provider,
            replay_scenario=args.replay_scenario,
            max_iterations=args.max_iterations,
        )

    if args.cmd == "ui":
        from .ui import serve
        serve(repo=repo, host=args.host, port=args.port,
              open_browser=not args.no_browser)
        return 0

    if args.cmd == "bundle":
        if args.bundle_cmd == "export":
            return _cmd_bundle_export(repo, out=args.out, note=args.note)
        if args.bundle_cmd == "import":
            return _cmd_bundle_import(
                repo, file=args.file,
                apply=args.apply, on_conflict=args.on_conflict,
            )

    if args.cmd == "replay":
        request = args.request
        scenario_arg = args.scenario
        scenario_path = _resolve_scenario_path(repo, resolved.scenarios_dir, scenario_arg)
        if request is None:
            req_file = scenario_path / "task_request.md"
            request = req_file.read_text(encoding="utf-8") if req_file.exists() else scenario_arg
        return _cmd_implement(
            repo=repo, resolved=resolved,
            request=request, provider="replay",
            replay_scenario=str(scenario_path),
            max_iterations=None,
        )

    parser.error("unknown command")
    return 2


# subcommand handlers ---------------------------------------------------


def _cmd_init(repo: Path, *, force: bool, starter: bool = False) -> int:
    cfg, created = write_default_config(repo, force=force)
    if not created:
        print(f"already exists: {cfg} (use --force to overwrite)", file=sys.stderr)
        return 1

    append_gitignore(repo)

    starter_path: Path | None = None
    if starter:
        cfg_obj = HarnessConfig.load(repo_root=repo)
        resolved = cfg_obj.resolved(repo)
        starter_path = write_starter_scenario(resolved.scenarios_dir)

    print(f"wrote {cfg}")
    print(f"  runs will be stored under: {repo / '.dev-loop' / 'runs'}")
    if starter_path is not None:
        print(f"  starter scenario installed: {starter_path}")
    print("\nNext steps:")
    print("  dev-loop doctor                # confirm the repo is ready")
    if starter_path is not None:
        print(f"  dev-loop replay {STARTER_SCENARIO_NAME}")
    else:
        print("  dev-loop init --starter        # adds a runnable demo scenario")
        print("  dev-loop ui                    # configure & run from the browser")
    return 0


def _cmd_config_show(repo: Path, *, explicit: Path | None) -> int:
    try:
        cfg, _path, issues = HarnessConfig.load_with_issues(
            repo_root=repo, explicit_path=explicit,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    resolved = cfg.resolved(repo)
    for it in issues:
        print(
            f"{it['level']}: {it['field']}: {it['message']}",
            file=sys.stderr,
        )
    info = {
        "repo": str(repo),
        "config_file": str(_first_existing(
            explicit or repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME,
            repo / CONFIG_DIR_NAME / "config.yml",
        )),
        "runs_dir": str(resolved.runs_dir),
        "sandbox_dir": str(resolved.sandbox_dir),
        "clean_workspace_dir": str(resolved.clean_workspace_dir),
        "scenarios_dir": str(resolved.scenarios_dir),
        "default_provider": resolved.default_provider,
        "policy": {
            "max_code_iterations": resolved.policy.max_code_iterations,
            "max_validation_attempts_per_iteration":
                resolved.policy.max_validation_attempts_per_iteration,
            "max_diagnostic_rounds_per_failure":
                resolved.policy.max_diagnostic_rounds_per_failure,
            "max_total_wall_clock_minutes":
                resolved.policy.max_total_wall_clock_minutes,
        },
        "notes": cfg.notes,
    }
    print(json.dumps(info, indent=2))
    return 0


def _cmd_config_validate(
    repo: Path, *, explicit: Path | None, strict: bool,
) -> int:
    """Lint ``.dev-loop/config.yaml`` and report issues to stdout.

    Exit code: 0 if clean (or only warnings without ``--strict``);
    1 if any error-level issues are present (or any issues with
    ``--strict``); 2 if the file can't be read or parsed.
    """
    try:
        _cfg, path, issues = HarnessConfig.load_with_issues(
            repo_root=repo, explicit_path=explicit,
        )
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if path is None:
        print("no config file found — using built-in defaults.")
        print(f"  expected at: {repo / CONFIG_DIR_NAME / CONFIG_FILE_NAME}")
        print("  run `dev-loop init` to scaffold one.")
        return 0

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    print(f"config: {path}")
    if not issues:
        print("  OK — no issues.")
        return 0
    for it in errors:
        print(f"  error   {it['field']}: {it['message']}")
    for it in warnings:
        print(f"  warning {it['field']}: {it['message']}")
    summary_parts = []
    if errors:
        summary_parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
    if warnings:
        summary_parts.append(
            f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
        )
    print(f"  ({', '.join(summary_parts)})")
    if errors:
        return 1
    if strict and warnings:
        return 1
    return 0


def _cmd_doctor(
    repo: Path, *, explicit: Path | None, as_json: bool, strict: bool,
) -> int:
    """``dev-loop doctor`` — one-shot setup diagnostics.

    Designed as the very first command a user runs after ``init`` to
    confirm the harness is wired up correctly, and as the first command
    they run when something feels off ("why does ``implement`` fail?").

    Severity → exit code is symmetric with ``config validate``: errors
    always fail, warnings fail only under ``--strict``.
    """
    checks = run_doctor(repo, explicit_config=explicit)
    rc = doctor_exit_code(checks, strict=strict)

    if as_json:
        print(doctor_to_json(repo, checks, exit_code=rc))
        return rc

    print(f"dev-loop doctor — {repo}")
    print(format_checks(checks))
    print(f"\nsummary: {format_summary(checks)}")
    if rc != 0:
        print("\nfix the items above (or pass --strict to gate on warnings).")
    return rc


def _cmd_runs_ls(
    runs_dir: Path, *, limit: int, status: str | None, as_json: bool,
) -> int:
    runs = list_runs(runs_dir)
    if status:
        runs = [r for r in runs if r.get("final_status") == status]
    if limit and limit > 0:
        runs = runs[:limit]

    if as_json:
        print(json.dumps({"runs_dir": str(runs_dir), "runs": runs}, indent=2))
        return 0

    if not runs:
        if not runs_dir.exists():
            print(f"no runs yet — runs_dir does not exist: {runs_dir}")
        elif status:
            print(f"no runs with final_status={status!r} in {runs_dir}")
        else:
            print(f"no runs found in {runs_dir}")
        return 0

    headers = ("TASK_ID", "STATUS", "ITERS", "SEL", "DURATION", "GOAL")
    rows = [headers]
    for r in runs:
        rows.append((
            r["task_id"],
            r.get("final_status") or r.get("status") or "-",
            str(r.get("iterations") or 0),
            str(r.get("selected_iteration") if r.get("selected_iteration") is not None else "-"),
            _fmt_duration(r.get("duration_seconds")),
            _truncate(r.get("goal") or "", 60),
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0


def _resolve_run_alias(runs_dir: Path, ref: str) -> str | None:
    """Resolve ``last`` / ``last-N`` to a concrete task id.

    Non-alias strings are returned unchanged so callers can pass any
    user-supplied id through this filter. Returns ``None`` when an
    alias has no match (e.g. ``last`` with an empty ledger, or
    ``last-99`` when only 3 runs exist).
    """
    if ref != "last" and not ref.startswith("last-"):
        return ref
    runs = list_runs(runs_dir)
    if not runs:
        return None
    if ref == "last":
        return runs[0]["task_id"]
    try:
        offset = int(ref.split("-", 1)[1])
    except (IndexError, ValueError):
        return ref
    if offset < 0 or offset >= len(runs):
        return None
    return runs[offset]["task_id"]


def _cmd_runs_show(
    runs_dir: Path, *, task_id: str, as_json: bool,
) -> int:
    resolved_id = _resolve_run_alias(runs_dir, task_id)
    if resolved_id is None:
        print(f"error: no runs in {runs_dir}", file=sys.stderr)
        return 1

    detail = show_run(runs_dir, resolved_id)
    if detail is None:
        print(f"error: run not found: {resolved_id}", file=sys.stderr)
        print(f"  looked under: {runs_dir / resolved_id}", file=sys.stderr)
        print("  try `dev-loop runs ls` to see available runs.", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(detail, indent=2))
        return 0

    print(f"task_id:       {detail['task_id']}")
    print(f"final_status:  {detail.get('final_status') or '-'}")
    print(f"status:        {detail.get('status') or '-'}")
    if detail.get("stop_reason"):
        print(f"stop_reason:   {detail['stop_reason']}")
    sel = detail.get("selected_iteration")
    print(f"selected_iter: {sel if sel is not None else '-'}")
    print(f"duration:      {_fmt_duration(detail.get('duration_seconds'))}")
    print(f"created:       {detail.get('created_at_utc') or '-'}")
    print(f"updated:       {detail.get('updated_at_utc') or '-'}")
    if detail.get("goal"):
        print(f"goal:          {detail['goal']}")
    print(f"path:          {detail['path']}")
    if detail.get("report_md"):
        print(f"report (md):   {detail['report_md']}")
    if detail.get("report_json"):
        print(f"report (json): {detail['report_json']}")

    iterations = detail.get("iterations") or []
    if iterations:
        print(f"\niterations ({len(iterations)}):")
        for it in iterations:
            mark = "*" if sel is not None and it["iteration"] == sel else " "
            e2e = it.get("final_e2e_status") or "-"
            attempts = it.get("attempts") or 0
            phash = (it.get("patch_hash") or "")[:10] or "-"
            n_files = len(it.get("changed_files") or [])
            print(
                f"  {mark} iter-{it['iteration']:03d}  e2e={e2e:<7} "
                f"attempts={attempts}  patch={phash}  files={n_files}"
            )
            if it.get("error"):
                print(f"      error: {_truncate(it['error'], 100)}")
            if it.get("summary"):
                print(f"      {_truncate(it['summary'], 100)}")
            line = _fmt_ai_call_rollup(it.get("ai_call_rollup") or {})
            if line:
                print(f"      ai_calls: {line}")
    return 0


def _fmt_ai_call_rollup(rollup: dict[str, Any]) -> str:
    """Render the per-iteration ai_calls rollup line for ``runs show``.

    Empty string when there were no recorded calls (older runs predate
    the Iter 9 metadata layout). The line mirrors the pills the Analyze
    tab's drilldown shows so terminal users get the same at-a-glance
    signal — provider mix, any non-zero exit codes, fallback count.
    """
    total = rollup.get("total") or 0
    if total <= 0:
        return ""
    parts = [f"{total} call{'s' if total != 1 else ''}"]
    by_prov = rollup.get("by_provider") or {}
    if by_prov:
        prov_bits = ", ".join(
            f"{prov}={count}" for prov, count in sorted(by_prov.items())
        )
        parts.append(f"providers: {prov_bits}")
    nz = rollup.get("nonzero_returncodes") or 0
    if nz:
        parts.append(f"nonzero_rc={nz}")
    synth = rollup.get("synthesized") or 0
    if synth:
        parts.append(f"synthesized={synth}")
    return "  ".join(parts)


def _cmd_runs_diff(
    runs_dir: Path, *, a: str, b: str, as_json: bool,
) -> int:
    a_id = _resolve_run_alias(runs_dir, a)
    b_id = _resolve_run_alias(runs_dir, b)
    if a_id is None or b_id is None:
        unresolved = ", ".join(
            ref for ref, rid in [(a, a_id), (b, b_id)] if rid is None
        )
        print(
            f"error: cannot resolve run reference: {unresolved}",
            file=sys.stderr,
        )
        print("  try `dev-loop runs ls` to see available runs.", file=sys.stderr)
        return 1

    diff = diff_runs(runs_dir, a_id, b_id)

    if as_json:
        print(json.dumps(diff, indent=2))
        return 0 if diff["deltas"].get("both_present") else 1

    sa, sb, deltas = diff["a"], diff["b"], diff["deltas"]
    if sa is None or sb is None:
        if sa is None:
            print(f"error: run not found: {a_id}", file=sys.stderr)
        if sb is None:
            print(f"error: run not found: {b_id}", file=sys.stderr)
        return 1

    print(f"A  {sa['task_id']}")
    print(f"B  {sb['task_id']}")
    print()
    rows = [
        ("field", "A", "B"),
        ("final_status",
         sa.get("final_status") or "-", sb.get("final_status") or "-"),
        ("selected_iter",
         _fmt_optional(sa.get("selected_iteration")),
         _fmt_optional(sb.get("selected_iteration"))),
        ("iterations",
         str(len(sa.get("iterations") or [])),
         str(len(sb.get("iterations") or []))),
        ("duration",
         _fmt_duration(sa.get("duration_seconds")),
         _fmt_duration(sb.get("duration_seconds"))),
        ("audit_total",
         str((sa.get("audit") or {}).get("total") or 0),
         str((sb.get("audit") or {}).get("total") or 0)),
        ("goal",
         _truncate(sa.get("goal") or "-", 50),
         _truncate(sb.get("goal") or "-", 50)),
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())

    print()
    print("deltas:")
    print(f"  same_goal:                  {_fmt_bool(deltas.get('same_goal'))}")
    print(f"  same_scenario:              {_fmt_bool(deltas.get('same_scenario'))}")
    print(f"  same_final_status:          {_fmt_bool(deltas.get('same_final_status'))}")
    print(f"  iteration_count_delta:      "
          f"{_fmt_signed(deltas.get('iteration_count_delta'))}")
    print(f"  duration_seconds_delta:     "
          f"{_fmt_signed(deltas.get('duration_seconds_delta'))}")
    n = deltas.get("iteration_status_compared") or 0
    agree = deltas.get("iteration_status_agreement") or 0
    print(f"  iteration_status_agreement: {agree}/{n}")
    fd = deltas.get("first_diverging_iteration")
    print(f"  first_diverging_iteration:  {fd if fd is not None else '-'}")
    print(f"  audit_total_delta:          "
          f"{_fmt_signed(deltas.get('audit_total_delta'))}")

    only_a = deltas.get("files_only_a") or []
    only_b = deltas.get("files_only_b") or []
    both = deltas.get("files_both") or []
    print()
    print(f"files only in A ({len(only_a)}):")
    for f in only_a:
        print(f"  - {f}")
    print(f"files only in B ({len(only_b)}):")
    for f in only_b:
        print(f"  + {f}")
    print(f"files in both   ({len(both)}):")
    for f in both:
        print(f"  = {f}")

    return 0


def _fmt_optional(v: Any) -> str:
    return "-" if v is None else str(v)


def _fmt_bool(v: Any) -> str:
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return "-"


def _fmt_signed(v: int | None) -> str:
    if v is None:
        return "-"
    if v > 0:
        return f"+{v}"
    return str(v)


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _truncate(s: str, n: int) -> str:
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def _cmd_scenarios_ls(scenarios_dir: Path, *, as_json: bool) -> int:
    rows = list_scenarios(scenarios_dir)

    if as_json:
        print(json.dumps(
            {"scenarios_dir": str(scenarios_dir), "scenarios": rows},
            indent=2,
        ))
        return 0

    if not rows:
        if not scenarios_dir.exists():
            print(f"no scenarios — scenarios_dir does not exist: {scenarios_dir}")
            print("  run `dev-loop init --starter` to install the bundled demo.")
        else:
            print(f"no scenarios found in {scenarios_dir}")
        return 0

    headers = ("NAME", "LINT", "E2E", "DURATION", "FILES", "SUITE", "GOAL")
    table = [headers]
    for r in rows:
        if r.get("n_errors"):
            lint = f"{r['n_errors']}E"
            if r.get("n_warnings"):
                lint += f"/{r['n_warnings']}W"
        elif r.get("n_warnings"):
            lint = f"{r['n_warnings']}W"
        else:
            lint = "ok"
        table.append((
            r["name"],
            lint,
            r.get("e2e_status") or "-",
            _fmt_duration(r.get("duration_seconds")),
            str(r.get("file_count") or 0),
            _truncate(r.get("e2e_suite") or "-", 24),
            _truncate(r.get("goal") or "-", 48),
        ))
    widths = [max(len(row[i]) for row in table) for i in range(len(headers))]
    for row in table:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0


def _cmd_scenarios_show(
    scenarios_dir: Path, *, name: str, as_json: bool,
) -> int:
    detail = show_scenario(scenarios_dir, name)
    if detail is None:
        print(f"error: scenario not found: {name}", file=sys.stderr)
        print(f"  looked under: {scenarios_dir / name}", file=sys.stderr)
        print("  try `dev-loop scenarios ls` to see available scenarios.",
              file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(detail, indent=2))
        return 0

    print(f"name:          {detail['name']}")
    print(f"path:          {detail['path']}")
    print(f"goal:          {detail.get('goal') or '-'}")
    print(f"e2e_status:    {detail.get('e2e_status') or '-'}")
    print(f"e2e_suite:     {detail.get('e2e_suite') or '-'}")
    print(f"duration:      {_fmt_duration(detail.get('duration_seconds'))}")
    print(f"files:         {detail.get('file_count') or 0}")

    issues = detail.get("issues") or []
    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    if not issues:
        print("lint:          ok (no issues)")
    else:
        bits = []
        if errors:
            bits.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
        if warnings:
            bits.append(
                f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
            )
        print(f"lint:          {', '.join(bits)}")
        for it in errors:
            print(f"  error   {it['field']}: {it['message']}")
        for it in warnings:
            print(f"  warning {it['field']}: {it['message']}")

    others = detail.get("other_files") or []
    if others:
        print(f"\nother files ({len(others)}):")
        for f in others:
            print(f"  {f}")

    req = (detail.get("task_request") or "").strip()
    if req:
        print("\ntask_request.md (first 12 lines):")
        for line in req.splitlines()[:12]:
            print(f"  {line}")
    return 0


def _cmd_scenarios_validate(
    scenarios_dir: Path, *, name: str | None, strict: bool, as_json: bool,
) -> int:
    """Lint one or every scenario under ``scenarios_dir``.

    Exit code: 0 if every scenario is clean (or only warnings without
    ``--strict``); 1 if any scenario has errors (or any issues under
    ``--strict``); 2 if a named scenario is missing.
    """
    if name is not None:
        detail = show_scenario(scenarios_dir, name)
        if detail is None:
            print(f"error: scenario not found: {name}", file=sys.stderr)
            return 2
        targets = [detail]
    else:
        if not scenarios_dir.exists():
            msg = f"no scenarios — scenarios_dir does not exist: {scenarios_dir}"
            if as_json:
                print(json.dumps({
                    "scenarios_dir": str(scenarios_dir),
                    "scenarios": [],
                    "totals": {"errors": 0, "warnings": 0, "clean": 0, "scenarios": 0},
                }, indent=2))
            else:
                print(msg)
            return 0
        targets = [
            show_scenario(scenarios_dir, row["name"])
            for row in list_scenarios(scenarios_dir)
        ]
        targets = [t for t in targets if t is not None]

    total_errors = sum(
        sum(1 for i in t.get("issues") or [] if i["level"] == "error")
        for t in targets
    )
    total_warnings = sum(
        sum(1 for i in t.get("issues") or [] if i["level"] == "warning")
        for t in targets
    )
    clean = sum(
        1 for t in targets
        if not any(i["level"] == "error" for i in t.get("issues") or [])
    )

    if as_json:
        print(json.dumps({
            "scenarios_dir": str(scenarios_dir),
            "scenarios": [
                {
                    "name": t["name"],
                    "path": t["path"],
                    "issues": t.get("issues") or [],
                    "valid": not any(
                        i["level"] == "error" for i in t.get("issues") or []
                    ),
                }
                for t in targets
            ],
            "totals": {
                "errors": total_errors,
                "warnings": total_warnings,
                "clean": clean,
                "scenarios": len(targets),
            },
        }, indent=2))
    else:
        if not targets:
            print(f"no scenarios found in {scenarios_dir}")
            return 0
        for t in targets:
            issues = t.get("issues") or []
            errs = [i for i in issues if i["level"] == "error"]
            warns = [i for i in issues if i["level"] == "warning"]
            if not issues:
                print(f"{t['name']}: ok")
                continue
            bits = []
            if errs:
                bits.append(f"{len(errs)} error{'s' if len(errs) != 1 else ''}")
            if warns:
                bits.append(f"{len(warns)} warning{'s' if len(warns) != 1 else ''}")
            print(f"{t['name']}: {', '.join(bits)}")
            for it in errs:
                print(f"  error   {it['field']}: {it['message']}")
            for it in warns:
                print(f"  warning {it['field']}: {it['message']}")
        print(
            f"\n{len(targets)} scenario{'s' if len(targets) != 1 else ''}, "
            f"{clean} clean, {total_errors} error"
            f"{'s' if total_errors != 1 else ''}, "
            f"{total_warnings} warning{'s' if total_warnings != 1 else ''}."
        )

    if total_errors:
        return 1
    if strict and total_warnings:
        return 1
    return 0


def _cmd_capabilities_ls(
    *, category: str | None, agent_only: bool, as_json: bool,
) -> int:
    """``dev-loop capabilities ls`` — list the registry as a table or JSON.

    Mirrors the Build > Capabilities panel in the web UI so a user
    debugging "why can't the agent request X?" sees identical information
    in either surface. Rows are pre-sorted (category, then name) by the
    public ``list_capabilities`` helper.
    """
    rows = list_capabilities()
    if category:
        rows = [r for r in rows if r["category"] == category]
    if agent_only:
        rows = [r for r in rows if r["agent_requestable"]]

    if as_json:
        print(json.dumps({"capabilities": rows}, indent=2))
        return 0

    if not rows:
        if category or agent_only:
            print("no capabilities matched the given filters.")
        else:
            print("no capabilities registered.")
        return 0

    headers = ("NAME", "CATEGORY", "AGENT", "TIMEOUT", "MANIFEST", "IMPL", "FORCED")
    table = [headers]
    for r in rows:
        forced = ",".join(
            f"{k}={v}" for k, v in sorted((r.get("forced_params") or {}).items())
        ) or "-"
        table.append((
            r["name"],
            r["category"],
            "yes" if r["agent_requestable"] else "-",
            f"{r['timeout_seconds']}s",
            "yes" if r["uses_run_manifest"] else "-",
            "yes" if r["has_impl"] else "MISSING",
            _truncate(forced, 36),
        ))
    widths = [max(len(row[i]) for row in table) for i in range(len(headers))]
    for row in table:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())

    missing = [r["name"] for r in rows if not r["has_impl"]]
    if missing:
        print()
        print(
            f"warning: {len(missing)} capabilit"
            f"{'y' if len(missing) == 1 else 'ies'} declared in registry.yaml "
            f"without a bound implementation: {', '.join(missing)}"
        )
        return 1
    return 0


def _cmd_capabilities_show(*, name: str, as_json: bool) -> int:
    detail = show_capability(name)
    if detail is None:
        print(f"error: capability not found: {name}", file=sys.stderr)
        print(
            "  try `dev-loop capabilities ls` to see registered capabilities.",
            file=sys.stderr,
        )
        return 1

    if as_json:
        print(json.dumps(detail, indent=2))
        return 0

    print(f"name:              {detail['name']}")
    print(f"category:          {detail['category']}")
    print(f"agent_requestable: {_fmt_bool(detail['agent_requestable'])}")
    print(f"timeout_seconds:   {detail['timeout_seconds']}")
    print(f"uses_run_manifest: {_fmt_bool(detail['uses_run_manifest'])}")
    print(f"redacts_output:    {_fmt_bool(detail['redacts_output'])}")
    print(f"audit:             {_fmt_bool(detail['audit'])}")
    print(f"prod_possible:     {_fmt_bool(detail['prod_possible'])}")
    print(f"implementation:    {'bound' if detail['has_impl'] else 'MISSING'}")

    forced = detail.get("forced_params") or {}
    if forced:
        print("forced_params:")
        for k, v in sorted(forced.items()):
            print(f"  {k}: {json.dumps(v)}")
    else:
        print("forced_params:    -")

    if not detail["has_impl"]:
        print()
        print(
            "warning: this capability is declared in registry.yaml but no "
            "implementation is bound. Invoking it will return an error."
        )
        return 1
    return 0


def _cmd_playbooks_ls(
    *, repo: Path, overridden_only: bool, as_json: bool,
) -> int:
    """``dev-loop playbooks ls`` — mirror Build > Playbooks in the terminal.

    The web UI's playbook picker reads the same list. Showing the source
    ("built-in" vs "repo-override"), file size and the agent phases bound
    to each playbook makes it easy to answer "did my repo's override
    actually take effect?" without dropping into the browser.
    """
    rows = list_playbooks(repo=repo)
    if overridden_only:
        rows = [r for r in rows if r["overridden"]]

    if as_json:
        print(json.dumps(
            {"repo": str(repo), "playbooks": rows}, indent=2,
        ))
        return 0

    if not rows:
        if overridden_only:
            print("no per-repo playbook overrides found.")
            print(
                "  drop a file into .dev-loop/playbooks/<name>.md to override "
                "a built-in.",
            )
        else:
            print("no playbooks found.")
        return 0

    headers = ("NAME", "SOURCE", "SIZE", "LINES", "PHASES")
    table = [headers]
    for r in rows:
        phases = ",".join(r.get("agent_phases") or []) or "-"
        table.append((
            r["name"],
            r["source"],
            _fmt_bytes(r["size_bytes"]),
            str(r["line_count"]),
            _truncate(phases, 36),
        ))
    widths = [max(len(row[i]) for row in table) for i in range(len(headers))]
    for row in table:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())

    overrides = [r["name"] for r in rows if r["overridden"]]
    if overrides and not overridden_only:
        print()
        print(
            f"{len(overrides)} repo override"
            f"{'s' if len(overrides) != 1 else ''}: {', '.join(overrides)}",
        )
    return 0


def _cmd_playbooks_show(
    *, repo: Path, name: str, metadata_only: bool, as_json: bool,
) -> int:
    detail = show_playbook(
        name, repo=repo, include_text=not metadata_only,
    )
    if detail is None:
        print(f"error: playbook not found: {name}", file=sys.stderr)
        print(
            "  try `dev-loop playbooks ls` to see available playbooks.",
            file=sys.stderr,
        )
        return 1

    if as_json:
        print(json.dumps(detail, indent=2))
        return 0

    print(f"name:        {detail['name']}")
    print(f"source:      {detail['source']}")
    print(f"path:        {detail['path']}")
    print(f"size:        {_fmt_bytes(detail['size_bytes'])}")
    print(f"lines:       {detail['line_count']}")
    phases = detail.get("agent_phases") or []
    if phases:
        print(f"agent phases: {', '.join(phases)}")
    else:
        print("agent phases: - (not bound to a built-in phase)")
    if detail["overridden"] and detail["has_builtin"]:
        print(
            "note:         this repo overrides the built-in copy. "
            "Edits flow through .dev-loop/playbooks/.",
        )
    elif detail["overridden"] and not detail["has_builtin"]:
        print(
            "note:         repo-only playbook (no built-in of this name "
            "ships with the harness).",
        )

    if metadata_only:
        return 0
    text = detail.get("text") or ""
    if text:
        print()
        print("--- begin playbook ---")
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        print("--- end playbook ---")
    if detail.get("read_error"):
        print(f"\nwarning: failed to read playbook body: {detail['read_error']}",
              file=sys.stderr)
        return 1
    return 0


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    kb = n / 1024
    if kb < 1024:
        return f"{kb:.1f}KB"
    return f"{kb/1024:.1f}MB"


def _cmd_schema_validate(file: Path, schema_name: str) -> int:
    obj = json.loads(file.read_text(encoding="utf-8"))
    try:
        schemas.validate(schema_name, obj)
    except Exception as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def _cmd_implement(
    *,
    repo: Path,
    resolved,
    request: str,
    provider: str,
    replay_scenario: str | None,
    max_iterations: int | None,
) -> int:
    policy = resolved.policy
    if max_iterations is not None:
        policy.max_code_iterations = max_iterations

    replay_path: Path | None = None
    if provider == "replay":
        if not replay_scenario:
            print("--provider replay requires --replay-scenario", file=sys.stderr)
            return 2
        replay_path = _resolve_scenario_path(repo, resolved.scenarios_dir, replay_scenario)
        if not replay_path.exists():
            print(f"replay scenario not found: {replay_path}", file=sys.stderr)
            return 2

    try:
        runner = create_runner(provider, replay_scenario=replay_path)
    except ValueError as e:
        # e.g. unknown provider, or missing --replay-scenario for replay.
        # Print a clean message instead of a stack trace.
        print(f"error: {e}", file=sys.stderr)
        return 2
    registry = load_default_registry()

    config = OrchestratorConfig(
        repo_root=repo,
        runs_dir=resolved.runs_dir,
        sandbox_dir=resolved.sandbox_dir,
        clean_workspace_dir=resolved.clean_workspace_dir,
        request=request,
        provider=provider,
        replay_scenario=replay_path,
        policy=policy,
    )
    orch = Orchestrator(config=config, runner=runner, registry=registry)
    result = orch.run()

    print(f"task_id:           {result.task_id}")
    print(f"final_status:      {result.final_status}")
    print(f"selected_iter:     {result.selected_iteration}")
    print(f"ledger:            {result.ledger_dir}")
    print(f"report:            {result.report_path}")
    return 0 if result.final_status == "passed" else 1


def _cmd_bundle_export(repo: Path, *, out: Path | None, note: str) -> int:
    bundle = build_bundle(repo, note=note)
    text = bundle_to_json(bundle)
    if out is None:
        sys.stdout.write(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        n_sc = len(bundle["scenarios"])
        n_pb = len(bundle["playbooks"])
        has_cfg = bool(bundle["config"]["yaml"])
        print(f"wrote {out}")
        print(f"  config:    {'yes' if has_cfg else 'no'}")
        print(f"  scenarios: {n_sc}")
        print(f"  playbooks: {n_pb}")
        print("\nShare it with a teammate or import on a fresh clone:")
        print(f"  dev-loop bundle import {out.name}")
    return 0


def _cmd_bundle_import(
    repo: Path, *, file: Path, apply: bool, on_conflict: str,
) -> int:
    try:
        raw = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: bundle file not found: {file}", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(raw)
        validate_bundle(bundle)
    except (json.JSONDecodeError, BundleError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if not apply:
        preview = preview_apply(bundle, repo)
        print(f"Preview of {file} → {repo}:")
        src = preview.get("source") or {}
        if src.get("repo_name"):
            print(f"  source: {src.get('repo_name')}")
        if preview.get("note"):
            print(f"  note:   {preview['note']}")
        t = preview["totals"]
        print(f"  {t['new']} new · {t['conflict']} conflict · {t['identical']} identical")
        for c in preview["changes"]:
            marker = {"new": "+", "conflict": "!", "identical": "="}.get(c["status"], "?")
            print(f"  {marker} [{c['kind']}] {c['path']}")
        print("\nNo files were written. Re-run with --apply to write.")
        print(f"  dev-loop bundle import {file} --apply --on-conflict {on_conflict}")
        return 0

    report = apply_bundle(bundle, repo, on_conflict=on_conflict)
    totals = report["totals"]
    print(f"applied bundle to {repo}")
    for action, count in sorted(totals.items()):
        print(f"  {count} {action}")
    for a in report["actions"]:
        if a["action"] not in ("identical",):
            print(f"  {a['action']:>10}  {a['path']}")
    return 0


# helpers ---------------------------------------------------------------


def _resolve_scenario_path(repo: Path, scenarios_dir: Path, arg: str) -> Path:
    candidate = Path(arg)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    direct = (repo / arg).resolve()
    if direct.exists():
        return direct
    named = (scenarios_dir / arg).resolve()
    return named


def _first_existing(*paths: Path) -> Path | str:
    for p in paths:
        if p.exists():
            return p
    return "<defaults — no config file>"


if __name__ == "__main__":
    sys.exit(main())
