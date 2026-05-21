from harness.policy import (
    LoopPolicy,
    LoopState,
    check_stop_conditions,
    code_change_allowed,
    fingerprint_failure,
)


def test_code_change_allowed_medium_high():
    p = LoopPolicy()
    assert code_change_allowed(p, {"next_action": "modify_code", "confidence": "medium"})
    assert code_change_allowed(p, {"next_action": "modify_code", "confidence": "high"})
    assert not code_change_allowed(p, {"next_action": "modify_code", "confidence": "low"})
    assert not code_change_allowed(p, {"next_action": "ask_human", "confidence": "high"})


def test_fingerprint_same_for_same_failure():
    a = {"status": "failed", "failure_class": "code_suspected", "first_error": "x", "failed_test": "t"}
    b = dict(a)
    assert fingerprint_failure(a) == fingerprint_failure(b)


def test_same_failure_twice_stops_loop():
    p = LoopPolicy()
    s = LoopState()
    s.failure_fingerprints = ["x", "x"]
    assert check_stop_conditions(policy=p, state=s, last_triage=None) == \
        "same_failure_fingerprint_after_2_code_iterations"


def test_max_iterations_stops_loop():
    p = LoopPolicy(max_code_iterations=2)
    s = LoopState()
    s.code_iterations_done = 2
    assert check_stop_conditions(policy=p, state=s, last_triage=None) == \
        "max_code_iterations_reached"


def test_triage_ask_human_stops():
    p = LoopPolicy()
    s = LoopState()
    triage = {"next_action": "ask_human", "confidence": "low"}
    assert check_stop_conditions(policy=p, state=s, last_triage=triage) == "agent_requested_human"
