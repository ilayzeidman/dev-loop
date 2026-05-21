from harness import schemas


def test_task_contract_minimal_valid():
    obj = {
        "type": "task_contract",
        "implementation_goal": "do thing",
        "assumptions": [],
        "success_criteria": ["it works"],
        "non_goals": [],
        "likely_components": [],
        "validation_plan": [],
        "ambiguities": [],
        "can_start_without_human": True,
    }
    schemas.validate("task_contract.v1.json", obj)


def test_task_contract_missing_field():
    obj = {"type": "task_contract"}
    assert not schemas.is_valid("task_contract.v1.json", obj)


def test_failure_triage_valid():
    obj = {
        "type": "failure_triage",
        "failure_class": "code_suspected",
        "confidence": "medium",
        "next_action": "modify_code",
        "hypothesis": "h",
        "expected_effect": "e",
        "evidence_refs": [],
        "requested_diagnostics": [],
        "human_reason": None,
    }
    schemas.validate("failure_triage.v1.json", obj)


def test_failure_triage_bad_enum():
    obj = {
        "type": "failure_triage",
        "failure_class": "code_suspected",
        "confidence": "medium",
        "next_action": "rm_rf_prod",
        "hypothesis": "h",
        "expected_effect": "",
        "evidence_refs": [],
        "requested_diagnostics": [],
        "human_reason": None,
    }
    assert not schemas.is_valid("failure_triage.v1.json", obj)


def test_diagnostic_request_inside_triage():
    obj = {
        "type": "failure_triage",
        "failure_class": "code_suspected",
        "confidence": "low",
        "next_action": "request_more_diagnostics",
        "hypothesis": "h",
        "expected_effect": "",
        "evidence_refs": [],
        "requested_diagnostics": [
            {
                "type": "diagnostic_request",
                "capability": "query_elastic_for_current_run",
                "reason": "need first error",
                "params": {"severity": ["error"], "max_lines": 50},
            }
        ],
        "human_reason": None,
    }
    schemas.validate("failure_triage.v1.json", obj)
