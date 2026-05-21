"""dev-loop CLI.

Subcommands:

  dev-loop init                           # scaffold .dev-loop/config.yaml
  dev-loop implement --request "..." ...  # run the autonomous loop
  dev-loop config show                    # print the resolved config
  dev-loop config validate                # lint .dev-loop/config.yaml
  dev-loop schema validate <file> <schema>
  dev-loop replay <scenario>              # one-liner replay
  dev-loop bundle export [--out FILE]     # pack config+scenarios+playbooks
  dev-loop bundle import FILE [--apply]   # preview / apply a bundle
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
from .capabilities import load_default_registry
from .config import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    STARTER_SCENARIO_NAME,
    HarnessConfig,
    append_gitignore,
    write_default_config,
    write_starter_scenario,
)
from .orchestrator import Orchestrator, OrchestratorConfig


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

    if args.cmd == "config":
        if args.cfg_cmd == "validate":
            return _cmd_config_validate(
                repo, explicit=args.config, strict=args.strict,
            )
        return _cmd_config_show(repo, explicit=args.config)

    if args.cmd == "schema":
        return _cmd_schema_validate(args.file, args.schema)

    cfg = HarnessConfig.load(repo_root=repo, explicit_path=args.config)
    resolved = cfg.resolved(repo)

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
    print("  dev-loop config show")
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
