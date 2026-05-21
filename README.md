# dev-loop

Autonomous AI developer harness. The harness is the orchestrator; the LLM
agent only reasons, edits code, and produces schema-validated decisions.
All external access (Jenkins, Kubernetes, Elasticsearch, Grafana, GPU
nodes) is mediated by deterministic, registered capabilities.

See `docs/design.md` for the full design summary this implementation is
based on.

## Wire a new repo in 30 seconds

```bash
# from any repo:
pip install dev-loop                  # or: pip install -e <path-to-dev-loop>
dev-loop init --starter               # scaffolds config + bundled demo scenario
dev-loop replay hello-dev-loop        # full pass-through, no LLM needed
dev-loop ui                           # opens the local web UI
```

Prefer the browser? `dev-loop ui` opens a one-click onboarding panel that
does the same scaffolding and offers a "Try the demo run" button so you
can confirm the harness end-to-end before configuring anything.

The UI lets you:

- **Build** — edit the per-repo config, view & edit capabilities,
  playbooks, schemas, and replay scenarios.
- **Run** — kick off an `/implement` loop and watch the live log.
- **Analyze** — drill into any past run: report, iterations, attempts,
  diagnostics, patches, and the full capability audit trail.

Headless usage is symmetric:

```bash
# Run the autonomous loop against a recorded scenario, no LLM needed.
dev-loop replay scenarios/gpu-init-timeout-001

# Real provider (Claude or Codex):
dev-loop implement --provider claude --request "fix gpu init timeout"
```

## Configuration

`.dev-loop/config.yaml` (created by `dev-loop init`):

```yaml
runs_dir: .dev-loop/runs
default_provider: replay
scenarios_dir: scenarios
# policy:
#   max_code_iterations: 5
#   max_validation_attempts_per_iteration: 2
#   max_diagnostic_rounds_per_failure: 3
#   max_total_wall_clock_minutes: 120
```

Every field has a default. Override only what you need. See
`dev-loop config show` for resolved values, or `dev-loop config validate`
to lint the file (catches typos, out-of-range policy values, and
unknown providers before they reach the loop).

Stuck? `dev-loop doctor` is a one-shot setup check that scans the
repo for the most common breakage classes (missing/invalid config,
missing `.gitignore` entry, unwritable `runs_dir`, empty
`scenarios_dir`, default provider whose CLI isn't on PATH) and
prints each issue with a one-line fix hint. It exits non-zero on
errors (or warnings under `--strict`), so it's safe to gate CI on.
Pair it with `--json` for machine-readable output. The same checks
are embedded in the web UI's first-run onboarding panel (and exposed
at `GET /api/doctor`) so you see identical diagnostics in either
surface.

## Run ledger

```
.dev-loop/runs/<task-id>/
  task_manifest.json
  baseline/
    base_sha.txt
    original_prompt.md
  iterations/iter-001/
    manifest.json
    patch.diff
    changed_files.json
    ai_calls/
      001_implementation/{input,output,raw_provider_log,metadata}
    validations/attempt-001/
      run_manifest.json
      local_build.json
      local_test.json
      jenkins_build.json
      deployment.json
      e2e_result.json
      diagnostics/{elastic,grafana,pod_logs,gpu_utilization}
      outcome.json
  capability_audit.jsonl
  final_review_report.json
  final_review_report.md
```

## Inspecting past runs

The same run ledger that powers the web UI's Analyze tab is queryable
from the CLI:

```bash
dev-loop runs ls                   # newest-first table of recent runs
dev-loop runs ls --status passed   # filter by final_status
dev-loop runs ls --limit 50 \
            --offset 50            # paginate a large ledger
dev-loop runs ls --json            # machine-readable output
dev-loop runs show <task-id>       # iterations, attempts, report paths
dev-loop runs show last            # same, for the most recent run
dev-loop runs diff <a> <b>         # CLI mirror of the Analyze tab compare
dev-loop runs diff last-1 last     # compare the previous run to the newest
```

`runs show` prints the iteration timeline (e2e status, attempt count,
patch hash, file count, agent summary) and the paths to the structured
and markdown final reports so you can pipe straight into `less` or open
in an editor without digging through `.dev-loop/runs/`. The output also
embeds a per-iteration AI-call summary (`provider`, `returncode`,
`stderr_tail`, `argv`) sourced from `ai_calls/NNN/metadata.json`, so the
CLI sees the same telemetry the Analyze tab's AI-call drilldown surfaces.

### Crash-safe ledger

Every run finalizes its `task_manifest.json` even when interrupted. If you
press `Ctrl+C` (or the orchestrator's contract phase dies mid-flight) the
manifest is rewritten with `status: aborted`, `final_status:
failed_inconclusive`, and `interrupted: true` before the signal is
re-raised; the CLI then exits with code 130. `runs ls` collapses the
manifest into a single `effective_status` bucket (`aborted` for a
mid-flight ghost, `final_status` when the run reached the report writer)
and flags `(interrupted)` in the status column, so a killed run is never
indistinguishable from one that's still executing.

`runs diff` is the headless analog of the Analyze tab's compare view:
side-by-side header (final status, iterations, duration, audit total,
goal), a deltas block (iteration-count delta, first diverging iteration,
status agreement, audit delta) and a files-only-in-A / only-in-B / both
listing. `last` and `last-N` resolve to the newest / N-th newest run, so
`dev-loop runs diff last-1 last --json` is the canonical "did the most
recent change help or hurt?" query.

## Authoring & linting replay scenarios

Scenarios are the headless equivalent of recorded LLM output: a small
directory of JSON + markdown that `--provider replay` reads back without
making any network calls. The `scenarios` subcommand exposes the same
structured form / lint rules the web UI's scenario builder enforces, so
you can edit them in your editor and gate CI on the result:

```bash
dev-loop scenarios ls                       # table of scenarios + lint state
dev-loop scenarios show <name>              # goal, e2e, lint, request preview
dev-loop scenarios validate                 # lint every scenario in scenarios_dir
dev-loop scenarios validate <name>          # lint just one
dev-loop scenarios validate --strict --json # CI-friendly machine-readable output
```

`validate` exits non-zero on any error (or any issue under `--strict`),
mirroring the UI's inline form validator so a scenario that's clean in
the browser is clean in CI. The schema-equivalent checks live in
`harness/scenarios.py:validate_scenario_form` — required
`implementation_goal`, list-of-strings shape on every contract array,
allowed e2e statuses (`passed` / `failed`), non-negative
`duration_seconds`, and the conventions `replay_runner` expects.

## Inspecting the capability registry

The agent's safety boundary is defined by `harness/capabilities/registry.yaml`.
Headless callers can introspect it without dropping into the web UI:

```bash
dev-loop capabilities ls                          # whole table, grouped by category
dev-loop capabilities ls --agent-requestable      # only what the agent can request
dev-loop capabilities ls --category local_only    # filter by category
dev-loop capabilities show trigger_dev_jenkins_build
dev-loop capabilities show <name> --json          # machine-readable
```

The CLI and the Build > Capabilities UI tab share a single source of
truth (`harness.capabilities.list_capabilities`), so a teammate
reviewing the agent's allowed surface from either side sees identical
data — including `has_impl`, which flags any spec declared in
`registry.yaml` without a bound implementation.

## Inspecting agent playbooks

Playbooks are the trusted prompt fragments the harness injects when it
asks the agent to produce a task contract, an implementation result, or a
failure triage. Built-ins ship inside the package; any repo can override
one by dropping `.dev-loop/playbooks/<name>.md` alongside its config.
`dev-loop playbooks` is the headless mirror of the Build > Playbooks UI
tab:

```bash
dev-loop playbooks ls                          # built-in + override table
dev-loop playbooks ls --overridden-only        # only repo overrides
dev-loop playbooks show implement_feature.v1.md
dev-loop playbooks show <name> --metadata-only # skip the body
dev-loop playbooks show <name> --json          # machine-readable
```

The table flags each row's source (`built-in` / `repo-override`), file
size, line count, and the agent phases bound to it (e.g.
`implementation,task_contract`), so it's easy to confirm a repo override
actually replaced what the loop will read on the next run.
`harness.playbooks.list_playbooks` / `show_playbook` are the public
helpers both the CLI and `/api/playbooks` consume.

## Provider model

V1 supports three providers:

- `replay` — drives the loop from recorded scenario artifacts, no LLM.
- `claude` — Claude Code adapter (uses the local `claude` CLI).
- `codex` — Codex adapter (uses the local `codex` CLI).

A single provider is used for an entire task. There is no fallback or
ensembling in v1.

## Safety boundary

The agent runs in a credential-free sandbox with no host SSH, no
kubeconfig, no Jenkins tokens, no Grafana/Elastic tokens, no Docker
socket. All external side effects flow through the harness capability
registry.

Capabilities marked `agent_requestable: false` (Jenkins triggers, deploys,
the E2E suite) can be invoked only by the harness. Agent-requestable
capabilities (diagnostics) must derive their parameters from the run
manifest; the agent cannot pass `environment`, `deploy_target`, `promote`,
`publish_release`, or `production` keys — those would be rejected and
recorded in the audit log.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```
