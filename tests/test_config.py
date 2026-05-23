from pathlib import Path

import yaml

from harness.config import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    HarnessConfig,
    dump_canonical_yaml,
)


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


def test_from_dict_with_issues_flags_bad_int():
    cfg, issues = HarnessConfig.from_dict_with_issues(
        {"max_code_iterations": "many"}
    )
    errors = [i for i in issues if i["level"] == "error"]
    assert any(i["field"] == "max_code_iterations" for i in errors)
    assert cfg.max_code_iterations == 5  # default kept


def test_from_dict_with_issues_warns_on_unknown_keys():
    _, issues = HarnessConfig.from_dict_with_issues({"foo": 1, "policy": {"bar": 2}})
    fields = {i["field"] for i in issues if i["level"] == "warning"}
    assert "foo" in fields
    assert "policy.bar" in fields


def test_from_dict_with_issues_warns_on_unknown_provider():
    _, issues = HarnessConfig.from_dict_with_issues({"default_provider": "vinyl"})
    assert any(
        i["level"] == "warning" and i["field"] == "default_provider"
        for i in issues
    )


def test_from_dict_with_issues_flags_zero_iterations():
    _, issues = HarnessConfig.from_dict_with_issues(
        {"max_code_iterations": 0}
    )
    assert any(
        i["level"] == "error" and i["field"] == "max_code_iterations"
        for i in issues
    )


def test_load_with_issues_returns_path_and_issues(tmp_path: Path):
    cd = tmp_path / CONFIG_DIR_NAME
    cd.mkdir()
    (cd / CONFIG_FILE_NAME).write_text("totally_unknown_thing: 1\n")
    cfg, path, issues = HarnessConfig.load_with_issues(repo_root=tmp_path)
    assert path == cd / CONFIG_FILE_NAME
    assert any(i["field"] == "totally_unknown_thing" for i in issues)
    assert cfg.default_provider == "replay"


def test_load_with_issues_returns_none_path_when_no_file(tmp_path: Path):
    cfg, path, issues = HarnessConfig.load_with_issues(repo_root=tmp_path)
    assert path is None
    assert issues == []
    assert cfg.default_provider == "replay"


def test_load_with_issues_non_mapping_top_level(tmp_path: Path):
    cd = tmp_path / CONFIG_DIR_NAME
    cd.mkdir()
    (cd / CONFIG_FILE_NAME).write_text("- just a list\n- not a mapping\n")
    cfg, path, issues = HarnessConfig.load_with_issues(repo_root=tmp_path)
    assert path == cd / CONFIG_FILE_NAME
    assert any(i["level"] == "error" and i["field"] == "<root>" for i in issues)
    assert cfg.default_provider == "replay"  # defaults preserved


def test_from_dict_with_issues_clean_config():
    _, issues = HarnessConfig.from_dict_with_issues({
        "default_provider": "claude",
        "policy": {"max_code_iterations": 7},
    })
    # No errors, no warnings on a perfectly valid config.
    assert issues == []


def test_dump_canonical_yaml_roundtrips_defaults():
    cfg = HarnessConfig()
    text = dump_canonical_yaml(cfg)
    parsed = yaml.safe_load(text) or {}
    cfg2 = HarnessConfig.from_dict(parsed)
    # Pure-default config loads back as defaults — every override line in
    # the canonical YAML is commented out so ``yaml.safe_load`` sees nothing.
    assert cfg2.max_code_iterations == cfg.max_code_iterations
    assert cfg2.default_provider == cfg.default_provider


def test_dump_canonical_yaml_roundtrips_overrides():
    cfg = HarnessConfig(
        default_provider="claude",
        runs_dir=".runs",
        max_code_iterations=12,
        max_total_wall_clock_minutes=60,
    )
    text = dump_canonical_yaml(cfg)
    cfg2 = HarnessConfig.from_dict(yaml.safe_load(text))
    assert cfg2.default_provider == "claude"
    assert cfg2.runs_dir == ".runs"
    assert cfg2.max_code_iterations == 12
    assert cfg2.max_total_wall_clock_minutes == 60


def test_dump_canonical_yaml_quotes_strings_with_colons():
    """``runs_dir: .dev-loop/runs`` is a YAML string but ``a: b: c`` would
    look like two mappings — the dumper must quote strings safely."""
    cfg = HarnessConfig(notes="see config: yaml{}")
    text = dump_canonical_yaml(cfg)
    parsed = yaml.safe_load(text)
    assert parsed["notes"] == "see config: yaml{}"
