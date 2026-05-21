# Playbook: pod_startup_failure (v1)

You are analyzing pod startup failures for the current run.

## Steps

1. Read pod status: was the pod `Pending`, `CrashLoopBackOff`,
   `ImagePullBackOff`, or `Error`?
2. `ImagePullBackOff` -> probably build/registry issue -> environment.
3. `CrashLoopBackOff` with stack trace in container logs -> `code_suspected`.
4. `Pending` with `Insufficient nvidia.com/gpu` -> environment, cluster has
   no available GPUs in this slot.

## Rules

- Only request `get_pod_logs_for_current_run` for pods listed in the
  manifest's `deployment.pods`. Do not request logs for arbitrary pods.
