from pathlib import Path

from harness.config import CONFIG_DIR_NAME, CONFIG_FILE_NAME, HarnessConfig


def test_defaults_when_no_file(tmp_path: Path):
    cfg = HarnessConfig.load(repo_root=tmp_path)
    assert cfg.runs_dir == ".dev-loop/runs"
    assert cfg.default_provider == "replay"
    assert cfg.max_code_iterations == 5


def test_overrides_via_yaml(tmp_path: Path):
    cd = tmp_path / CONFIG_DIR_NAME
    cd.mkdir()
    (cd / CONFIG_FILE_NAME).write_text(
        "default_provider: claude\n"
        "policy:\n"
        "  max_code_iterations: 9\n"
    )
    cfg = HarnessConfig.load(repo_root=tmp_path)
    assert cfg.default_provider == "claude"
    assert cfg.max_code_iterations == 9


def test_unknown_keys_ignored_with_note(tmp_path: Path):
    cd = tmp_path / CONFIG_DIR_NAME
    cd.mkdir()
    (cd / CONFIG_FILE_NAME).write_text("totally_unknown_thing: 1\n")
    cfg = HarnessConfig.load(repo_root=tmp_path)
    assert "unknown keys ignored" in cfg.notes


def test_resolved_paths(tmp_path: Path):
    cfg = HarnessConfig.load(repo_root=tmp_path)
    r = cfg.resolved(tmp_path)
    assert r.runs_dir == (tmp_path / ".dev-loop" / "runs").resolve()
    assert r.scenarios_dir == (tmp_path / "scenarios").resolve()
    assert r.policy.max_code_iterations == 5
