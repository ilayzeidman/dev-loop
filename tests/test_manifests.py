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
    # No metadata supplied -> no metadata.json file is created.
    assert not (d / "metadata.json").exists()


def test_ledger_persists_ai_call_metadata(tmp_path: Path):
    """Provider metadata (provider, returncode, stderr_tail, argv) is
    persisted alongside the AI call so the Analyze tab can render
    diagnostics for failed provider invocations without grepping the
    raw log."""
    import json
    ledger = TaskLedger.create(tmp_path / "runs", "task-meta")
    ledger.create_iteration(1)
    meta = {
        "provider": "claude",
        "returncode": 0,
        "stderr_tail": "warning: something",
        "argv": ["claude", "--headless"],
        "ts_utc": "2026-05-21T00:00:00Z",
    }
    d = ledger.record_ai_call(
        1, 2, "triage_attempt_1",
        input_obj={"x": 1}, output_obj={"y": 2},
        raw_provider_log=None, metadata=meta,
    )
    mf = d / "metadata.json"
    assert mf.exists()
    assert json.loads(mf.read_text()) == meta
    # Empty metadata stays out of the dir to avoid noisy artifacts.
    d2 = ledger.record_ai_call(
        1, 3, "synth",
        input_obj={}, output_obj={}, metadata={},
    )
    assert not (d2 / "metadata.json").exists()
