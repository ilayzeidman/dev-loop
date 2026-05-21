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


def test_bad_int_type_does_not_crash_loader(tmp_path: Path):
    """``max_code_iterations: many`` must not silently produce a string in
    the policy — the orchestrator's ``range()`` call would later blow up
    with an opaque TypeError. The loader must either coerce or reject."""
    cd = tmp_path / CONFIG_DIR_NAME
    cd.mkdir()
    (cd / CONFIG_FILE_NAME).write_text(
        "policy:\n  max_code_iterations: many\n"
    )
    cfg = HarnessConfig.load(repo_root=tmp_path)
    # The bad value must have been dropped or coerced; default is kept.
    assert isinstance(cfg.max_code_iterations, int)
    assert cfg.max_code_iterations == 5  # default
    assert "type errors" in cfg.notes


def test_quoted_int_is_coerced(tmp_path: Path):
    cd = tmp_path / CONFIG_DIR_NAME
    cd.mkdir()
    (cd / CONFIG_FILE_NAME).write_text(
        "policy:\n  max_code_iterations: \"7\"\n"
    )
    cfg = HarnessConfig.load(repo_root=tmp_path)
    assert cfg.max_code_iterations == 7


def test_from_dict_does_not_mutate_caller(tmp_path: Path):
    raw = {"policy": {"max_code_iterations": 3}, "default_provider": "claude"}
    snapshot = {"policy": {"max_code_iterations": 3}, "default_provider": "claude"}
    HarnessConfig.from_dict(raw)
    assert raw == snapshot
