"""Structured representation of replay scenarios.

A replay scenario directory holds the canned outputs the
``ReplayAgentRunner`` reads back (see ``agents/replay_runner.py``):

  task_request.md
  task_contract.json
  implementation_result.json
  e2e_result.json
  failure_triage.json
  expected_triage.json
  patch.diff
  files/...

For the UI's "scenario builder" we want a single round-trippable view
over the four most-edited pieces — the markdown request and the three
JSON fixtures — so the user can edit success criteria as list rows
rather than as raw JSON.

This module is the parser/serializer pair. The raw files stay
authoritative on disk; the form is a projection. Anything we don't
understand is preserved on read and written back on save under
``extras`` so a power-user's hand-edits don't get clobbered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Fields we project out of ``task_contract.json``. Anything else stays
# in ``extras_task_contract`` and is merged back on save.
_TASK_CONTRACT_FIELDS = {
    "implementation_goal",
    "assumptions",
    "success_criteria",
    "non_goals",
    "likely_components",
    "validation_plan",
    "ambiguities",
    "can_start_without_human",
}

_IMPLEMENTATION_RESULT_FIELDS = {
    "summary",
    "hypothesis",
    "confidence",
    "expected_validation",
    "risk_notes",
    "claimed_changed_files",
}

_E2E_RESULT_FIELDS = {
    "status",
    "test_suite",
    "duration_seconds",
    "first_error",
    "device_id",
}

_VALID_E2E_STATUS = {"passed", "failed"}
_VALID_CONFIDENCE = {"low", "medium", "high"}


@dataclass
class ScenarioForm:
    """Flat dict the UI binds to. One nested object per file."""

    name: str
    task_request: str = ""
    task_contract: dict[str, Any] = field(default_factory=dict)
    implementation_result: dict[str, Any] = field(default_factory=dict)
    e2e_result: dict[str, Any] = field(default_factory=dict)
    # Anything outside the projected field set, per file. Preserved on
    # round-trip so power-users can hand-edit fields the form doesn't
    # know about.
    extras: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Other files we didn't structure, just so the UI can show them.
    other_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "task_request": self.task_request,
            "task_contract": self.task_contract,
            "implementation_result": self.implementation_result,
            "e2e_result": self.e2e_result,
            "extras": self.extras,
            "other_files": self.other_files,
        }


def default_task_contract() -> dict[str, Any]:
    return {
        "implementation_goal": "",
        "assumptions": [],
        "success_criteria": ["E2E passes"],
        "non_goals": [],
        "likely_components": [],
        "validation_plan": [],
        "ambiguities": [],
        "can_start_without_human": True,
    }


def default_implementation_result() -> dict[str, Any]:
    return {
        "summary": "Replay scenario implementation.",
        "hypothesis": "Scenario-defined.",
        "confidence": "medium",
        "expected_validation": [],
        "risk_notes": [],
        "claimed_changed_files": [],
    }


def default_e2e_result() -> dict[str, Any]:
    return {
        "status": "passed",
        "test_suite": "stub-e2e",
        "duration_seconds": 1,
    }


# ---------------------------------------------------------------------------
# Parsing


def load_scenario_form(scenario_dir: Path) -> ScenarioForm:
    """Read a scenario dir into a ``ScenarioForm``.

    Missing files are populated with sensible defaults so the user lands
    in a fillable form rather than an empty void. Malformed JSON is
    surfaced as an ``extras`` ``_parse_error`` entry rather than raising
    — the UI shows the issue and offers a "raw files" fallback so the
    user can fix it.
    """
    sd = Path(scenario_dir)
    form = ScenarioForm(name=sd.name)

    req = sd / "task_request.md"
    if req.exists():
        form.task_request = req.read_text(encoding="utf-8")

    tc, tc_extras = _split_known(_read_json_or_empty(sd / "task_contract.json"),
                                 _TASK_CONTRACT_FIELDS)
    form.task_contract = {**default_task_contract(), **tc}
    if tc_extras:
        form.extras["task_contract"] = tc_extras

    ir, ir_extras = _split_known(_read_json_or_empty(sd / "implementation_result.json"),
                                 _IMPLEMENTATION_RESULT_FIELDS)
    form.implementation_result = {**default_implementation_result(), **ir}
    if ir_extras:
        form.extras["implementation_result"] = ir_extras

    er, er_extras = _split_known(_read_json_or_empty(sd / "e2e_result.json"),
                                 _E2E_RESULT_FIELDS)
    form.e2e_result = {**default_e2e_result(), **er}
    if er_extras:
        form.extras["e2e_result"] = er_extras

    known = {
        "task_request.md", "task_contract.json",
        "implementation_result.json", "e2e_result.json",
    }
    if sd.exists():
        for f in sorted(sd.iterdir()):
            if f.is_file() and f.name not in known:
                form.other_files.append(f.name)
            elif f.is_dir():
                form.other_files.append(f.name + "/")
    return form


def _read_json_or_empty(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"_parse_error": f"{type(e).__name__}: {e}"}
    if not isinstance(data, dict):
        return {"_parse_error": "top-level JSON is not an object"}
    return data


def _split_known(
    data: dict[str, Any], known: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    known_part: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    for k, v in data.items():
        if k == "type":
            # ``type`` is a constant the schema requires; the form
            # writes it explicitly so we don't ferry it through extras.
            continue
        if k in known:
            known_part[k] = v
        else:
            extras[k] = v
    return known_part, extras


# ---------------------------------------------------------------------------
# Validation


@dataclass(frozen=True)
class ScenarioIssue:
    level: str       # "error" | "warning"
    field: str       # dotted path, e.g. "task_contract.success_criteria"
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "field": self.field, "message": self.message}


def validate_scenario_form(form: dict[str, Any]) -> list[ScenarioIssue]:
    """Validate a form dict (as produced by the UI). Returns issues.

    Mirrors ``harness/schemas/task_contract.v1.json``, the implementation
    result schema, and the conventions ``replay_runner`` expects for
    ``e2e_result.json``. Type problems and missing-required fields are
    errors; soft problems (e.g. empty arrays the agent usually fills)
    are warnings.
    """
    issues: list[ScenarioIssue] = []

    if not isinstance(form, dict):
        return [ScenarioIssue("error", "_root", "form must be an object")]

    tc = form.get("task_contract") or {}
    if not isinstance(tc, dict):
        issues.append(ScenarioIssue(
            "error", "task_contract", "task_contract must be an object"))
        tc = {}

    goal = (tc.get("implementation_goal") or "").strip() if isinstance(
        tc.get("implementation_goal"), str) else ""
    if not goal:
        issues.append(ScenarioIssue(
            "error", "task_contract.implementation_goal",
            "implementation_goal is required"))

    for arr_field in ("assumptions", "success_criteria", "non_goals",
                      "likely_components", "validation_plan", "ambiguities"):
        v = tc.get(arr_field, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            issues.append(ScenarioIssue(
                "error", f"task_contract.{arr_field}",
                f"{arr_field} must be a list of strings"))

    sc = tc.get("success_criteria") or []
    if isinstance(sc, list) and not sc:
        issues.append(ScenarioIssue(
            "warning", "task_contract.success_criteria",
            "at least one success criterion is recommended"))

    csw = tc.get("can_start_without_human", True)
    if not isinstance(csw, bool):
        issues.append(ScenarioIssue(
            "error", "task_contract.can_start_without_human",
            "must be true or false"))

    ir = form.get("implementation_result") or {}
    if not isinstance(ir, dict):
        issues.append(ScenarioIssue(
            "error", "implementation_result",
            "implementation_result must be an object"))
        ir = {}

    summary = (ir.get("summary") or "").strip() if isinstance(
        ir.get("summary"), str) else ""
    if not summary:
        issues.append(ScenarioIssue(
            "error", "implementation_result.summary",
            "summary is required"))

    conf = ir.get("confidence", "medium")
    if conf not in _VALID_CONFIDENCE:
        issues.append(ScenarioIssue(
            "error", "implementation_result.confidence",
            f"must be one of {sorted(_VALID_CONFIDENCE)}"))

    for arr_field in ("expected_validation", "risk_notes", "claimed_changed_files"):
        v = ir.get(arr_field, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            issues.append(ScenarioIssue(
                "error", f"implementation_result.{arr_field}",
                f"{arr_field} must be a list of strings"))

    er = form.get("e2e_result") or {}
    if not isinstance(er, dict):
        issues.append(ScenarioIssue(
            "error", "e2e_result", "e2e_result must be an object"))
        er = {}

    status = er.get("status", "passed")
    if status not in _VALID_E2E_STATUS:
        issues.append(ScenarioIssue(
            "error", "e2e_result.status",
            f"must be one of {sorted(_VALID_E2E_STATUS)}"))

    suite = (er.get("test_suite") or "").strip() if isinstance(
        er.get("test_suite"), str) else ""
    if not suite:
        issues.append(ScenarioIssue(
            "error", "e2e_result.test_suite",
            "test_suite is required (e.g. 'gpu-streaming-e2e')"))

    dur = er.get("duration_seconds", 1)
    if not isinstance(dur, int) or isinstance(dur, bool) or dur < 0:
        issues.append(ScenarioIssue(
            "error", "e2e_result.duration_seconds",
            "duration_seconds must be a non-negative integer"))

    if status == "failed":
        if not (er.get("first_error") or "").strip() if isinstance(
                er.get("first_error"), str) else True:
            issues.append(ScenarioIssue(
                "warning", "e2e_result.first_error",
                "failed runs usually carry a first_error string for triage"))

    return issues


# ---------------------------------------------------------------------------
# Serialization


def dump_scenario_files(form: dict[str, Any]) -> dict[str, str]:
    """Render the four projected files from a form dict.

    Returns ``{filename: text}``. The caller writes them. Extras are
    merged back in for round-trip safety. JSON files always end with a
    trailing newline so they're well-behaved in diffs.
    """
    out: dict[str, str] = {}
    out["task_request.md"] = _ensure_trailing_newline(
        form.get("task_request") or "")

    tc_extras = ((form.get("extras") or {}).get("task_contract") or {})
    tc = {"type": "task_contract", **(form.get("task_contract") or {}), **tc_extras}
    out["task_contract.json"] = _json_dump(tc)

    ir_extras = ((form.get("extras") or {}).get("implementation_result") or {})
    ir = {"type": "implementation_result",
          **(form.get("implementation_result") or {}), **ir_extras}
    out["implementation_result.json"] = _json_dump(ir)

    er_extras = ((form.get("extras") or {}).get("e2e_result") or {})
    er = {**(form.get("e2e_result") or {}), **er_extras}
    # ``e2e_result.json`` has no ``type`` discriminator (it's just a
    # status payload), so we don't inject one.
    out["e2e_result.json"] = _json_dump(er)

    return out


def _json_dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2) + "\n"


def _ensure_trailing_newline(s: str) -> str:
    if not s:
        return ""
    return s if s.endswith("\n") else s + "\n"


# ---------------------------------------------------------------------------
# Directory inspection
#
# The CLI's ``scenarios`` subcommand and the web UI both want a cheap
# summary view over the scenarios dir without parsing every JSON file
# twice. ``list_scenarios`` and ``show_scenario`` are the public, tested
# entry points for that.


def list_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    """Return a sorted summary of every scenario directory.

    Each entry carries enough fields to render a one-line table row:
    name, implementation_goal, e2e status / suite, number of files, and
    whether the scenario is currently lint-clean. Malformed or missing
    JSON is tolerated — the row still appears, with ``valid=False`` and
    an issues count so the user knows which scenario to fix.
    """
    sd = Path(scenarios_dir)
    if not sd.exists():
        return []
    rows: list[dict[str, Any]] = []
    for d in sorted(sd.iterdir(), key=lambda p: p.name):
        if not d.is_dir():
            continue
        rows.append(_summarize_scenario(d))
    return rows


def show_scenario(scenarios_dir: Path, name: str) -> dict[str, Any] | None:
    """Return a detailed summary for one scenario, or ``None`` if missing.

    Includes the lint issues so callers can render them inline. The full
    structured form is reachable via ``load_scenario_form`` for callers
    that need every field; ``show_scenario`` is the read-only summary the
    CLI's ``scenarios show`` prints.
    """
    sd = Path(scenarios_dir) / name
    if not sd.is_dir():
        return None
    summary = _summarize_scenario(sd)
    form = load_scenario_form(sd)
    issues = [i.to_dict() for i in validate_scenario_form(form.to_dict())]
    summary["issues"] = issues
    summary["task_request"] = form.task_request
    summary["other_files"] = list(form.other_files)
    return summary


def _summarize_scenario(scenario_dir: Path) -> dict[str, Any]:
    form = load_scenario_form(scenario_dir)
    issues = validate_scenario_form(form.to_dict())
    n_errors = sum(1 for i in issues if i.level == "error")
    n_warnings = sum(1 for i in issues if i.level == "warning")
    file_count = 0
    if scenario_dir.exists():
        for _ in scenario_dir.rglob("*"):
            file_count += 1
    return {
        "name": scenario_dir.name,
        "path": str(scenario_dir),
        "goal": (form.task_contract.get("implementation_goal") or "").strip(),
        "e2e_status": form.e2e_result.get("status"),
        "e2e_suite": form.e2e_result.get("test_suite"),
        "duration_seconds": form.e2e_result.get("duration_seconds"),
        "file_count": file_count,
        "n_errors": n_errors,
        "n_warnings": n_warnings,
        "valid": n_errors == 0,
    }
