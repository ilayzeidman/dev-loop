"""Tests for the structured scenario form module."""

from __future__ import annotations

import json
from pathlib import Path

from harness.scenarios import (
    default_e2e_result,
    default_implementation_result,
    default_task_contract,
    dump_scenario_files,
    load_scenario_form,
    validate_scenario_form,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_scenario_form


def test_load_scenario_form_synthesizes_defaults_when_dir_empty(tmp_path: Path):
    d = tmp_path / "blank"
    d.mkdir()
    form = load_scenario_form(d)
    assert form.name == "blank"
    assert form.task_request == ""
    # Defaults make the form immediately editable instead of empty fields.
    assert form.task_contract == default_task_contract()
    assert form.implementation_result == default_implementation_result()
    assert form.e2e_result == default_e2e_result()
    assert form.extras == {}
    assert form.other_files == []


def test_load_scenario_form_parses_starter_layout(tmp_path: Path):
    d = tmp_path / "demo"
    d.mkdir()
    _write(d / "task_request.md", "# demo\nFix the thing.\n")
    _write(d / "task_contract.json", json.dumps({
        "type": "task_contract",
        "implementation_goal": "Fix the thing.",
        "success_criteria": ["E2E reaches PLAYING"],
        "assumptions": ["device ready exists"],
        "non_goals": [],
        "likely_components": ["src/foo.py"],
        "validation_plan": ["run e2e"],
        "ambiguities": [],
        "can_start_without_human": True,
    }))
    _write(d / "e2e_result.json", json.dumps({
        "status": "passed", "test_suite": "demo-e2e", "duration_seconds": 7,
    }))
    form = load_scenario_form(d)
    assert form.task_contract["implementation_goal"] == "Fix the thing."
    assert form.task_contract["success_criteria"] == ["E2E reaches PLAYING"]
    assert form.task_contract["likely_components"] == ["src/foo.py"]
    assert form.e2e_result["status"] == "passed"
    assert form.e2e_result["test_suite"] == "demo-e2e"
    assert form.e2e_result["duration_seconds"] == 7


def test_load_scenario_form_preserves_extras(tmp_path: Path):
    d = tmp_path / "demo"
    d.mkdir()
    _write(d / "task_contract.json", json.dumps({
        "type": "task_contract",
        "implementation_goal": "x",
        "custom_field": "kept",
        "another": [1, 2, 3],
    }))
    form = load_scenario_form(d)
    assert form.extras["task_contract"]["custom_field"] == "kept"
    assert form.extras["task_contract"]["another"] == [1, 2, 3]


def test_load_scenario_form_reports_parse_error_in_extras(tmp_path: Path):
    d = tmp_path / "demo"
    d.mkdir()
    _write(d / "task_contract.json", "{not json")
    form = load_scenario_form(d)
    # We don't blow up; the parse error rides through extras so the UI can
    # surface it and offer the raw-files fallback.
    assert "_parse_error" in form.extras["task_contract"]


def test_load_scenario_form_lists_unknown_files(tmp_path: Path):
    d = tmp_path / "demo"
    d.mkdir()
    _write(d / "task_request.md", "x")
    _write(d / "elastic_summary.json", "{}")
    _write(d / "files" / "patch.txt", "x")
    form = load_scenario_form(d)
    assert "elastic_summary.json" in form.other_files
    assert "files/" in form.other_files


# ---------------------------------------------------------------------------
# validate_scenario_form


def _clean_form() -> dict:
    return {
        "task_request": "request",
        "task_contract": default_task_contract() | {
            "implementation_goal": "do the thing",
        },
        "implementation_result": default_implementation_result(),
        "e2e_result": default_e2e_result(),
        "extras": {},
    }


def test_validate_scenario_form_clean_has_no_errors():
    issues = validate_scenario_form(_clean_form())
    assert not any(i.level == "error" for i in issues)


def test_validate_scenario_form_missing_goal_is_error():
    f = _clean_form()
    f["task_contract"]["implementation_goal"] = ""
    issues = validate_scenario_form(f)
    err = [i for i in issues if i.level == "error"]
    assert any(i.field == "task_contract.implementation_goal" for i in err)


def test_validate_scenario_form_bad_confidence_is_error():
    f = _clean_form()
    f["implementation_result"]["confidence"] = "extremely-high"
    issues = validate_scenario_form(f)
    assert any(i.field == "implementation_result.confidence" and i.level == "error"
               for i in issues)


def test_validate_scenario_form_bad_e2e_status_is_error():
    f = _clean_form()
    f["e2e_result"]["status"] = "exploded"
    issues = validate_scenario_form(f)
    assert any(i.field == "e2e_result.status" and i.level == "error"
               for i in issues)


def test_validate_scenario_form_missing_test_suite_is_error():
    f = _clean_form()
    f["e2e_result"]["test_suite"] = ""
    issues = validate_scenario_form(f)
    assert any(i.field == "e2e_result.test_suite" and i.level == "error"
               for i in issues)


def test_validate_scenario_form_negative_duration_is_error():
    f = _clean_form()
    f["e2e_result"]["duration_seconds"] = -1
    issues = validate_scenario_form(f)
    assert any(i.field == "e2e_result.duration_seconds" and i.level == "error"
               for i in issues)


def test_validate_scenario_form_array_of_non_strings_is_error():
    f = _clean_form()
    f["task_contract"]["success_criteria"] = ["ok", 42]
    issues = validate_scenario_form(f)
    assert any(i.field == "task_contract.success_criteria" and i.level == "error"
               for i in issues)


def test_validate_scenario_form_non_object_root_is_error():
    issues = validate_scenario_form("nope")  # type: ignore[arg-type]
    assert any(i.field == "_root" and i.level == "error" for i in issues)


# ---------------------------------------------------------------------------
# dump_scenario_files


def test_dump_scenario_files_writes_four_files():
    files = dump_scenario_files(_clean_form())
    assert set(files.keys()) == {
        "task_request.md",
        "task_contract.json",
        "implementation_result.json",
        "e2e_result.json",
    }
    # JSON files end with a single trailing newline so diffs are sane.
    for fname, text in files.items():
        if fname.endswith(".json"):
            assert text.endswith("\n")
            assert not text.endswith("\n\n")


def test_dump_scenario_files_injects_type_discriminators():
    files = dump_scenario_files(_clean_form())
    tc = json.loads(files["task_contract.json"])
    ir = json.loads(files["implementation_result.json"])
    er = json.loads(files["e2e_result.json"])
    assert tc["type"] == "task_contract"
    assert ir["type"] == "implementation_result"
    # ``e2e_result.json`` intentionally has no ``type`` field.
    assert "type" not in er


def test_dump_scenario_files_round_trips_extras(tmp_path: Path):
    f = _clean_form()
    f["extras"] = {
        "task_contract": {"custom_field": "stays"},
        "implementation_result": {"vendor_hint": "x"},
    }
    out = dump_scenario_files(f)
    tc = json.loads(out["task_contract.json"])
    ir = json.loads(out["implementation_result.json"])
    assert tc["custom_field"] == "stays"
    assert ir["vendor_hint"] == "x"


def test_dump_then_load_is_lossless(tmp_path: Path):
    f = _clean_form()
    f["task_contract"]["success_criteria"] = ["A", "B", "C"]
    f["task_contract"]["likely_components"] = ["src/a.py", "src/b.py"]
    f["e2e_result"]["status"] = "failed"
    f["e2e_result"]["first_error"] = "boom"
    d = tmp_path / "rt"
    d.mkdir()
    for fname, text in dump_scenario_files(f).items():
        (d / fname).write_text(text, encoding="utf-8")
    reloaded = load_scenario_form(d)
    assert reloaded.task_contract["success_criteria"] == ["A", "B", "C"]
    assert reloaded.task_contract["likely_components"] == ["src/a.py", "src/b.py"]
    assert reloaded.e2e_result["status"] == "failed"
    assert reloaded.e2e_result["first_error"] == "boom"


def test_dump_scenario_files_empty_request_omits_trailing_newline():
    f = _clean_form()
    f["task_request"] = ""
    assert dump_scenario_files(f)["task_request.md"] == ""
