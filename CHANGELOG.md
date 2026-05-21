# Changelog

All notable changes to dev-loop are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Onboarding

- Added `dev-loop config validate` lint command; pre-flight validation now runs inside `implement` and `replay`, and `config show` mirrors any issues to stderr.
- Added `dev-loop doctor` setup diagnostics with `--json` and `--strict` flags for CI gating.
- Surfaced the same `doctor` checks in the web UI's first-run onboarding panel via `GET /api/doctor` and `/api/onboarding.diagnostics`.

### CLI

- Added `dev-loop runs ls` and `dev-loop runs show <id|last>` with `--status` and `--json` filters; tolerant of corrupt manifests.
- Added `dev-loop runs diff <a> <b>` with `--json` and `last-N` aliases, mirroring the Analyze tab compare view.
- Added `dev-loop scenarios ls / show / validate` mirroring the UI scenario validator; `--strict` for CI.
- Added `dev-loop capabilities ls / show` with a new `has_impl` warning for specs without a bound implementation.
- Added `dev-loop playbooks ls / show` completing the Build-tab CLI symmetry sweep.
- `dev-loop runs ls` now supports `--limit` and `--offset` for paginating the ledger.
- `runs show` JSON output now embeds per-iteration AI-call summary and run-level rollup.

### UI

- `/api/runs/<a>/compare/<b>` now delegates to `harness.runs`, removing ~84 lines of duplicated logic.
- Analyze tab AI-call drilldown surfaces provider/returncode/stderr_tail/argv plus warning pills, sourced from per-call `metadata.json`.
- `/api/scenarios` embeds lint state; Build and Run scenario pickers display health badges.
- Analyze tab gained a "Load more" control backed by the same paginated ledger as the CLI.

### Reliability

- Orchestrator contract-phase crashes now finalize `task_manifest.json` and surface `effective_status: "aborted"` for mid-flight ghosts.
- `KeyboardInterrupt` / `SIGINT` during a run finalizes the ledger with `interrupted: true` and re-raises so the CLI exits 130; the marker is surfaced in `runs ls / show` and in the Analyze tab.
- Provider adapter refactor: shared `CliAgentRunner` base ensures symmetric error handling, enriched `AgentPhaseResult.metadata` (provider, returncode, stderr_tail, argv), and matching coverage for Claude and Codex.

### Performance

- `/api/runs/trends` now uses a stat-based mtime cache for ledger bucketing.
- Compare-summary helper (`_summarize_run_for_compare`) is mtime-cached so repeated Analyze-tab diffs are O(1) when nothing changed.

### Internal

- Promoted `diff_deltas` to `harness.runs` public API and added `tests/test_runs.py` (22 focused tests).
- Promoted `list_scenarios` / `show_scenario` and `iteration_ai_calls` / `ai_call_rollup` to public helpers shared by CLI and UI.
- Persisted per-AI-call provider metadata to `ai_calls/NNN/metadata.json`; added `/ai_calls` and `/ai_call/<id>` UI endpoints reading from the same source.
