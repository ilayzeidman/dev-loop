from pathlib import Path

from harness.manifests import TaskLedger, slugify


def test_slugify_basic():
    assert slugify("Fix GPU init timeout") == "fix-gpu-init-timeout"
    assert slugify("") == "task"


def test_ledger_creation_layout(tmp_path: Path):
    ledger = TaskLedger.create(tmp_path / "runs", "task-1")
    assert ledger.task_manifest_path.exists()
    iter_dir = ledger.create_iteration(1)
    assert iter_dir.exists()
    attempt_dir = ledger.create_attempt(1, 1)
    assert attempt_dir.exists()
    assert (attempt_dir / "diagnostics").exists()


def test_ledger_records_ai_call(tmp_path: Path):
    ledger = TaskLedger.create(tmp_path / "runs", "task-2")
    ledger.create_iteration(1)
    d = ledger.record_ai_call(
        1, 1, "implementation",
        input_obj={"x": 1}, output_obj={"y": 2}, raw_provider_log="raw",
    )
    assert (d / "input.json").exists()
    assert (d / "output.json").exists()
    assert (d / "raw_provider_log.jsonl").exists()
