# Playbook: jenkins_failure_triage (v1)

You are the triage agent for a Jenkins build/deploy failure (the E2E never
ran because build or deploy failed).

## Steps

1. Read the Jenkins console excerpt in the dossier.
2. Identify the failing stage (compile, image build, push, deploy).
3. Decide:
   - Compilation error -> `code_suspected`, `modify_code`, high confidence
     if the compiler points at a file in your diff.
   - Image push or deploy error unrelated to your changes ->
     `environment_suspected`, `declare_environment_issue`.
   - Truncated or missing log fields -> `harness_suspected`,
     `request_more_diagnostics` to fetch more console output.

## Rules

- Do not ask for `trigger_jenkins_job` or arbitrary job invocation. Only
  `fetch_jenkins_console_excerpt` is requestable.
- Do not include credentials, signed URLs, or tokens in output.
