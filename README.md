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
dev-loop init                         # scaffolds .dev-loop/config.yaml
dev-loop config show                  # prints resolved paths + policy
dev-loop ui                           # opens the local web UI
```

That's all the setup. The UI lets you:

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
`dev-loop config show` for resolved values.

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
      001_implementation/{input,output,raw_provider_log}
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
