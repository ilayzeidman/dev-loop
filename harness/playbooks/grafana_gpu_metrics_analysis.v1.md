# Playbook: grafana_gpu_metrics_analysis (v1)

You are analyzing Grafana metric summaries for the current run.

## Rules

- Metrics are scoped to the manifest's run window. Do not request a wider
  window without justification.
- For GPU triage, focus on: `gpu_util_pct`, `gpu_mem_used_bytes`, encoder
  throughput, queue depth.
- For non-GPU failures, fall back to `grafana_gpu_metrics_analysis` is the
  wrong playbook — recommend `stop_inconclusive` and let the harness pick a
  better playbook in the next round.
