"""Smoke tests for the UI HTTP server (stdlib only)."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

from harness.ui.server import _JobRegistry, _make_handler


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _server(repo: Path):
    port = _free_port()
    jobs = _JobRegistry()
    handler = _make_handler(repo=repo, jobs=jobs)
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield port, jobs
    finally:
        srv.shutdown()


def _get(port: int, path: str) -> tuple[int, str]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, r.read().decode("utf-8")


def _post_json(port: int, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method="POST", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_index_served(tmp_path: Path):
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/")
        assert status == 200
        assert "<title>dev-loop</title>" in body


def test_config_api_returns_defaults(tmp_path: Path):
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/config")
        assert status == 200
        data = json.loads(body)
        assert data["default_provider"] == "replay"
        assert data["repo"] == str(tmp_path.resolve())


def test_save_config_round_trip(tmp_path: Path):
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/raw",
            method="POST",
            data=b"default_provider: claude\npolicy:\n  max_code_iterations: 7\n",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            assert r.status == 200
        status, body = _get(port, "/api/config")
        data = json.loads(body)
        assert data["default_provider"] == "claude"
        assert data["policy"]["max_code_iterations"] == 7


def test_scenario_create_then_listed(tmp_path: Path):
    with _server(tmp_path) as (port, _):
        status, body = _post_json(port, "/api/scenarios", {
            "name": "foo-001", "task_request": "do a thing",
        })
        assert status == 200
        status, body = _get(port, "/api/scenarios")
        names = [s["name"] for s in json.loads(body)["scenarios"]]
        assert "foo-001" in names


def test_capabilities_endpoint(tmp_path: Path):
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/capabilities")
        assert status == 200
        names = [c["name"] for c in json.loads(body)["capabilities"]]
        assert "trigger_dev_jenkins_build" in names


def test_malformed_json_body_returns_400(tmp_path: Path):
    """Malformed JSON in a POST body is a client error, not a 500."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/implement",
            method="POST", data=b"{not-json",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code
            body = json.loads(e.read().decode("utf-8"))
            assert "invalid json" in body["error"].lower()


def test_bad_config_type_still_serves_api(tmp_path: Path):
    """If the saved config has the wrong type, ``/api/config`` must still
    respond (not 500) and the policy value must be an int."""
    cd = tmp_path / ".dev-loop"
    cd.mkdir()
    (cd / "config.yaml").write_text(
        "policy:\n  max_code_iterations: many\n"
    )
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/config")
        assert status == 200
        data = json.loads(body)
        # Defaulted back to 5 (or some int) rather than the string "many".
        assert isinstance(data["policy"]["max_code_iterations"], int)


def test_scenario_file_post_rejects_dotdot_name(tmp_path: Path):
    """A scenario ``name`` of ``..`` must not let a client write files
    outside the scenarios directory."""
    import urllib.error
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenarios/../file/evil.txt",
            method="POST", data=b"pwn",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            ok = True
        except urllib.error.HTTPError as e:
            ok = False
            assert e.code in (400, 403, 404), e.code
        # The write must have been refused either way: nothing escaped.
        # Check that no file ``evil.txt`` was written into ``tmp_path``
        # (one level above ``scenarios``).
        assert not (tmp_path / "evil.txt").exists()
        # And the scenarios dir itself was not populated.
        assert list(sc_dir.iterdir()) == []
        assert ok is False, "request should not have succeeded"


def test_scenario_file_get_rejects_dotdot_name(tmp_path: Path):
    """A scenario ``name`` of ``..`` (or other traversal token) must not
    let a client read files outside the scenarios directory."""
    import urllib.error
    sc_dir = tmp_path / "scenarios"
    sc_dir.mkdir()
    # Plant a secret one level above so a successful traversal would
    # actually leak data.
    (tmp_path / "secret.txt").write_text("topsecret")
    with _server(tmp_path) as (port, _):
        for url in (
            "/api/scenarios/..",
            "/api/scenarios/../file/secret.txt",
        ):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{url}")
            try:
                with urllib.request.urlopen(req, timeout=2) as r:
                    body = r.read().decode("utf-8")
                assert "topsecret" not in body, url
            except urllib.error.HTTPError as e:
                assert e.code in (400, 403, 404), (url, e.code)


def test_scenario_create_rejects_non_object_body(tmp_path: Path):
    """A POST body that isn't a JSON object must return 400, not 500."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenarios",
            method="POST", data=b"[1,2,3]",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code


def test_implement_rejects_non_object_body(tmp_path: Path):
    """``/api/implement`` with a non-object body must return 400, not 500."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/implement",
            method="POST", data=b'"hello"',
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code


def test_job_get_returns_isolated_snapshot(tmp_path: Path):
    """``_JobRegistry.get`` must return a deep copy so that a JSON
    serializer in one thread can't observe a concurrent mutation in
    another (e.g. ``dict changed size during iteration``)."""
    from harness.ui.server import _JobRegistry
    reg = _JobRegistry()
    job_id = reg.create({"request": "x", "provider": "replay"})
    snap = reg.get(job_id)
    assert snap is not None
    # Mutating the snapshot must not affect the registry.
    snap["request"]["request"] = "tampered"
    snap["status"] = "tampered"
    again = reg.get(job_id)
    assert again["request"]["request"] == "x"
    assert again["status"] == "queued"


def test_onboarding_reports_unconfigured_state(tmp_path: Path):
    """A fresh repo should report nothing done: no config, no scenarios, no runs."""
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/onboarding")
        assert status == 200
        data = json.loads(body)
        assert data["config_exists"] is False
        assert data["scenarios"] == []
        assert data["run_count"] == 0
        assert data["starter_installed"] is False
        assert data["is_complete"] is False
        ids = [s["id"] for s in data["steps"]]
        assert ids == ["config", "gitignore", "scenarios", "first_run"]
        assert all(s["done"] is False for s in data["steps"])


def test_init_endpoint_writes_config_and_starter(tmp_path: Path):
    """POST /api/init should scaffold config, gitignore, and the starter."""
    with _server(tmp_path) as (port, _):
        status, data = _post_json(port, "/api/init", {"install_starter": True})
        assert status == 200
        assert data["ok"] is True
        assert (tmp_path / ".dev-loop" / "config.yaml").exists()
        assert (tmp_path / ".gitignore").read_text(encoding="utf-8").find(
            ".dev-loop/runs/") != -1
        starter = tmp_path / "scenarios" / "hello-dev-loop"
        assert starter.is_dir()
        assert (starter / "task_request.md").exists()
        assert (starter / "e2e_result.json").exists()
        # Onboarding now reflects three of four steps done.
        _, ob = _get(port, "/api/onboarding")
        ob_data = json.loads(ob)
        done_ids = {s["id"] for s in ob_data["steps"] if s["done"]}
        assert {"config", "gitignore", "scenarios"} <= done_ids
        assert "first_run" not in done_ids  # no runs yet
        assert ob_data["starter_installed"] is True


def test_init_endpoint_is_idempotent(tmp_path: Path):
    """Re-running /api/init must not error and must not clobber an existing
    config (unless force is passed)."""
    with _server(tmp_path) as (port, _):
        _post_json(port, "/api/init", {"install_starter": False})
        cfg_path = tmp_path / ".dev-loop" / "config.yaml"
        original = cfg_path.read_text(encoding="utf-8")
        # Tamper with the config.
        cfg_path.write_text(original + "\nnotes: keep me\n", encoding="utf-8")
        tampered = cfg_path.read_text(encoding="utf-8")
        # Second call without force keeps the user edits.
        status, data = _post_json(port, "/api/init", {"install_starter": False})
        assert status == 200
        assert cfg_path.read_text(encoding="utf-8") == tampered
        # With force=True it gets overwritten.
        _post_json(port, "/api/init", {"install_starter": False, "force": True})
        assert cfg_path.read_text(encoding="utf-8") == original


def test_init_rejects_non_object_body(tmp_path: Path):
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/init",
            method="POST", data=b'"hi"',
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code


def test_run_duration_seconds_helper():
    """``_run_duration_seconds`` should return wall-clock seconds for
    well-formed timestamps and ``None`` (not 0, not an exception) when
    either side is missing or malformed."""
    from harness.ui.server import _run_duration_seconds
    assert _run_duration_seconds({
        "created_at_utc": "2026-05-21T12:55:52Z",
        "updated_at_utc": "2026-05-21T12:57:30Z",
    }) == 98
    # Same timestamp = 0, not None.
    assert _run_duration_seconds({
        "created_at_utc": "2026-05-21T12:55:52Z",
        "updated_at_utc": "2026-05-21T12:55:52Z",
    }) == 0
    # End-before-start clamps to 0 rather than going negative.
    assert _run_duration_seconds({
        "created_at_utc": "2026-05-21T12:57:00Z",
        "updated_at_utc": "2026-05-21T12:55:00Z",
    }) == 0
    assert _run_duration_seconds({}) is None
    assert _run_duration_seconds({"created_at_utc": "junk",
                                  "updated_at_utc": "2026-05-21T12:55:52Z"}) is None


def test_runs_list_surfaces_goal_and_duration(tmp_path: Path):
    """The Analyze tab depends on goal+duration to render a scannable run
    list. Pin that ``/api/runs`` returns those fields for a recorded run."""
    runs_dir = tmp_path / ".dev-loop" / "runs" / "20260521-aaaaaa-demo"
    runs_dir.mkdir(parents=True)
    (runs_dir / "task_manifest.json").write_text(json.dumps({
        "task_id": "20260521-aaaaaa-demo",
        "status": "completed",
        "final_status": "passed",
        "selected_iteration": 1,
        "created_at_utc": "2026-05-21T12:55:52Z",
        "updated_at_utc": "2026-05-21T12:56:10Z",
        "task_contract": {"implementation_goal": "ship a delightful UI"},
    }))
    (runs_dir / "iterations").mkdir()
    (runs_dir / "iterations" / "iter-001").mkdir()
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/runs")
        assert status == 200
        runs = json.loads(body)["runs"]
        assert len(runs) == 1
        r = runs[0]
        assert r["goal"] == "ship a delightful UI"
        assert r["duration_seconds"] == 18
        assert r["iterations"] == 1


def test_scenario_create_blocks_dotdot_name(tmp_path: Path):
    """The create endpoint must also reject ``..`` (its existing check did
    via ``startswith('.')``; this pins that behaviour)."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenarios",
            method="POST",
            data=json.dumps({"name": "..", "task_request": "x"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code
