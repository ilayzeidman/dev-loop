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
