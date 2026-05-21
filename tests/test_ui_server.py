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


def test_bundle_export_endpoint_returns_valid_bundle(tmp_path: Path):
    """``GET /api/bundle/export`` returns a parseable bundle whose shape
    matches what the importer expects."""
    (tmp_path / ".dev-loop").mkdir()
    (tmp_path / ".dev-loop" / "config.yaml").write_text(
        "default_provider: claude\n", encoding="utf-8")
    (tmp_path / "scenarios" / "foo-001").mkdir(parents=True)
    (tmp_path / "scenarios" / "foo-001" / "task_request.md").write_text(
        "do x\n", encoding="utf-8")
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/bundle/export")
        assert status == 200
        bundle = json.loads(body)
        assert bundle["format"] == "dev-loop-bundle"
        assert bundle["format_version"] >= 1
        assert bundle["config"]["yaml"].startswith("default_provider")
        names = [s["name"] for s in bundle["scenarios"]]
        assert "foo-001" in names


def test_bundle_preview_then_import_roundtrip(tmp_path: Path):
    """End-to-end: build a bundle from repo A, then preview+apply it
    against repo B through the HTTP endpoints."""
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir(); repo_b.mkdir()
    (repo_a / ".dev-loop").mkdir()
    (repo_a / ".dev-loop" / "config.yaml").write_text(
        "default_provider: claude\n", encoding="utf-8")
    (repo_a / "scenarios" / "foo").mkdir(parents=True)
    (repo_a / "scenarios" / "foo" / "task_request.md").write_text(
        "hi\n", encoding="utf-8")

    # Export via repo A's UI server.
    with _server(repo_a) as (port_a, _):
        _, body = _get(port_a, "/api/bundle/export")
        bundle = json.loads(body)

    # Preview + import against repo B.
    with _server(repo_b) as (port_b, _):
        status, preview = _post_json(port_b, "/api/bundle/preview",
                                     {"bundle": bundle})
        assert status == 200
        # Every change is new in the empty repo B.
        statuses = {c["status"] for c in preview["changes"]}
        assert "new" in statuses

        status, report = _post_json(port_b, "/api/bundle/import",
                                    {"bundle": bundle, "on_conflict": "skip"})
        assert status == 200
        # At least one file got written.
        assert any(a["action"] == "wrote" for a in report["actions"])
        assert (repo_b / "scenarios" / "foo" / "task_request.md").read_text(
            encoding="utf-8") == "hi\n"
        assert (repo_b / ".dev-loop" / "config.yaml").read_text(
            encoding="utf-8") == "default_provider: claude\n"


def test_bundle_preview_rejects_malformed_bundle(tmp_path: Path):
    """A garbage bundle returns 400, not 500."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/bundle/preview",
            method="POST",
            data=json.dumps({"bundle": {"format": "nope"}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code
            err = json.loads(e.read().decode("utf-8"))
            assert "format" in err["error"].lower()


def test_bundle_import_respects_include_filter(tmp_path: Path):
    """An ``include`` list narrows the write to listed display paths."""
    with _server(tmp_path) as (port, _):
        bundle = {
            "format": "dev-loop-bundle", "format_version": 1,
            "config": {"present": True, "yaml": "default_provider: claude\n"},
            "scenarios": [{
                "name": "foo",
                "files": {"a.txt": "A\n", "b.txt": "B\n"},
            }],
            "playbooks": [],
        }
        status, report = _post_json(port, "/api/bundle/import", {
            "bundle": bundle,
            "include": ["scenarios/foo/a.txt"],
        })
        assert status == 200
        actions = {a["path"]: a["action"] for a in report["actions"]}
        assert actions == {"scenarios/foo/a.txt": "wrote"}
        assert not (tmp_path / "scenarios" / "foo" / "b.txt").exists()
        assert not (tmp_path / ".dev-loop" / "config.yaml").exists()


def test_config_validate_endpoint_resolves_paths(tmp_path: Path):
    """The validate endpoint should resolve relative paths against the repo
    root so the Build > Config preview shows the real on-disk targets."""
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/validate",
            method="POST", data=b"runs_dir: my-runs\n",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            body = json.loads(r.read().decode("utf-8"))
        # ``my-runs`` should be resolved to an absolute path under tmp_path.
        assert body["resolved"]["runs_dir"].endswith("my-runs")
        assert str(tmp_path) in body["resolved"]["runs_dir"]


def test_config_validate_endpoint_round_trip(tmp_path: Path):
    """Posting a clean YAML body returns ``ok: True`` and a populated form."""
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/validate",
            method="POST",
            data=b"default_provider: claude\npolicy:\n  max_code_iterations: 7\n",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["issues"] == []
        assert body["form"]["default_provider"] == "claude"
        assert body["form"]["max_code_iterations"] == 7
        assert body["resolved"]["policy"]["max_code_iterations"] == 7
        # Nothing was written.
        assert not (tmp_path / ".dev-loop" / "config.yaml").exists()


def test_config_validate_endpoint_surfaces_yaml_error(tmp_path: Path):
    """A YAML parse error returns 200 with a structured issue, not 500."""
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/validate",
            method="POST", data=b": :\n  bogus: [unterminated",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert body["ok"] is False
        assert any(i["level"] == "error" for i in body["issues"])
        assert any("YAML" in i["message"] or "yaml" in i["message"]
                   for i in body["issues"])


def test_config_validate_endpoint_surfaces_field_errors(tmp_path: Path):
    """A bad scalar (e.g. negative iterations) shows up as a field-level error."""
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/validate",
            method="POST",
            data=b"policy:\n  max_code_iterations: 0\n",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert body["ok"] is False
        errs = [i for i in body["issues"] if i["level"] == "error"]
        assert any(i["field"] == "max_code_iterations" for i in errs)


def test_config_validate_endpoint_warns_on_unknown_provider(tmp_path: Path):
    """A typo in default_provider is a warning, not an error — the loop
    will still try to dispatch and surface the real error there."""
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/validate",
            method="POST", data=b"default_provider: glaude\n",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            body = json.loads(r.read().decode("utf-8"))
        # Still ok=True (no errors), but a warning.
        assert body["ok"] is True
        warns = [i for i in body["issues"] if i["level"] == "warning"]
        assert any(i["field"] == "default_provider" for i in warns)


def test_config_form_endpoint_renders_canonical_yaml(tmp_path: Path):
    """Posting the form dict produces YAML that, parsed, equals the form."""
    with _server(tmp_path) as (port, _):
        status, body = _post_json(port, "/api/config/form", {
            "default_provider": "claude",
            "runs_dir": ".dev-loop/runs",
            "max_code_iterations": 9,
        })
        assert status == 200
        assert body["ok"] is True
        assert "default_provider: claude" in body["yaml"]
        # And the round-trip works: validate the YAML and the form matches.
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/validate",
            method="POST", data=body["yaml"].encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            v = json.loads(r.read().decode("utf-8"))
        assert v["ok"] is True
        assert v["form"]["default_provider"] == "claude"
        assert v["form"]["max_code_iterations"] == 9


def test_config_form_endpoint_does_not_write(tmp_path: Path):
    """The form endpoint is pure — generating YAML must not touch disk."""
    with _server(tmp_path) as (port, _):
        _post_json(port, "/api/config/form", {"default_provider": "codex"})
        assert not (tmp_path / ".dev-loop" / "config.yaml").exists()


def test_config_form_endpoint_rejects_non_object(tmp_path: Path):
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/form",
            method="POST", data=b'"hi"',
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400


def test_config_validate_then_save_round_trip(tmp_path: Path):
    """The form -> YAML -> save -> reload chain matches what the form sent."""
    with _server(tmp_path) as (port, _):
        # Render canonical YAML from a form dict.
        _, form_resp = _post_json(port, "/api/config/form", {
            "default_provider": "claude",
            "max_code_iterations": 11,
            "max_total_wall_clock_minutes": 30,
        })
        yaml_text = form_resp["yaml"]
        # Save that YAML to disk via the existing POST /api/config/raw.
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config/raw",
            method="POST", data=yaml_text.encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            assert r.status == 200
        # Re-read /api/config and check the values stuck.
        _, body = _get(port, "/api/config")
        data = json.loads(body)
        assert data["default_provider"] == "claude"
        assert data["policy"]["max_code_iterations"] == 11
        assert data["policy"]["max_total_wall_clock_minutes"] == 30


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


# ---------------------------------------------------------------------------
# Build > Scenarios (structured form) endpoints


def _create_scenario(port: int, name: str = "demo-001", goal: str = "do the thing") -> dict:
    status, body = _post_json(port, "/api/scenarios", {
        "name": name,
        "implementation_goal": goal,
        "task_request": f"# {name}\n\n{goal}\n",
    })
    assert status == 200
    return body


def test_scenario_form_get_returns_default_shape(tmp_path: Path):
    """After create, GET /api/scenarios/<name>/form returns a fillable form."""
    with _server(tmp_path) as (port, _):
        _create_scenario(port)
        status, body = _get(port, "/api/scenarios/demo-001/form")
        assert status == 200
        data = json.loads(body)
        form = data["form"]
        assert form["name"] == "demo-001"
        assert form["task_contract"]["implementation_goal"] == "do the thing"
        assert form["e2e_result"]["status"] == "passed"
        assert form["implementation_result"]["confidence"] == "medium"
        # No errors against a clean default form.
        errs = [i for i in data["issues"] if i["level"] == "error"]
        assert errs == [], data["issues"]


def test_scenario_form_post_writes_canonical_files(tmp_path: Path):
    """POST /api/scenarios/<name>/form rewrites the four projected files."""
    with _server(tmp_path) as (port, _):
        _create_scenario(port)
        # Pull the form, change a field, save it back.
        _, body = _get(port, "/api/scenarios/demo-001/form")
        form = json.loads(body)["form"]
        form["task_contract"]["success_criteria"] = ["A", "B"]
        form["e2e_result"]["status"] = "failed"
        form["e2e_result"]["first_error"] = "boom"
        status, resp = _post_json(port, "/api/scenarios/demo-001/form", {
            "form": form,
        })
        assert status == 200
        assert resp["ok"] is True
        assert set(resp["written"]) == {
            "task_request.md", "task_contract.json",
            "implementation_result.json", "e2e_result.json",
        }
        # The files on disk match.
        scen_dir = tmp_path / "scenarios" / "demo-001"
        tc = json.loads((scen_dir / "task_contract.json").read_text())
        er = json.loads((scen_dir / "e2e_result.json").read_text())
        assert tc["success_criteria"] == ["A", "B"]
        assert er["status"] == "failed"
        assert er["first_error"] == "boom"


def test_scenario_form_post_rejects_validation_errors(tmp_path: Path):
    """Errors -> 400 with per-field issues; nothing is written."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        _create_scenario(port)
        scen_dir = tmp_path / "scenarios" / "demo-001"
        before = (scen_dir / "task_contract.json").read_text()
        bad = {
            "task_request": "x",
            "task_contract": {
                "implementation_goal": "",  # required, empty -> error
                "success_criteria": [], "assumptions": [], "non_goals": [],
                "likely_components": [], "validation_plan": [],
                "ambiguities": [], "can_start_without_human": True,
            },
            "implementation_result": {
                "summary": "x", "hypothesis": "x", "confidence": "medium",
                "expected_validation": [], "risk_notes": [],
                "claimed_changed_files": [],
            },
            "e2e_result": {"status": "passed", "test_suite": "x", "duration_seconds": 1},
            "extras": {},
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenarios/demo-001/form",
            method="POST",
            data=json.dumps({"form": bad}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400
            body = json.loads(e.read().decode("utf-8"))
            assert body["ok"] is False
            fields = {i["field"] for i in body["issues"]}
            assert "task_contract.implementation_goal" in fields
        # File on disk is unchanged.
        after = (scen_dir / "task_contract.json").read_text()
        assert before == after


def test_scenario_form_validate_never_writes(tmp_path: Path):
    """The /validate endpoint reports issues but doesn't touch disk."""
    with _server(tmp_path) as (port, _):
        _create_scenario(port)
        scen_dir = tmp_path / "scenarios" / "demo-001"
        before = (scen_dir / "task_contract.json").read_text()
        bad_form = {
            "task_contract": {"implementation_goal": ""},
            "implementation_result": {"summary": "x", "confidence": "medium"},
            "e2e_result": {"status": "passed", "test_suite": "x",
                           "duration_seconds": 1},
        }
        status, resp = _post_json(port,
            "/api/scenarios/demo-001/validate", {"form": bad_form})
        assert status == 200
        assert resp["ok"] is False
        assert any(i["field"] == "task_contract.implementation_goal"
                   for i in resp["issues"])
        after = (scen_dir / "task_contract.json").read_text()
        assert before == after


def test_scenario_form_post_preserves_extras(tmp_path: Path):
    """Power-user fields outside the projected schema round-trip through save."""
    with _server(tmp_path) as (port, _):
        _create_scenario(port)
        # Hand-edit a field the form doesn't know about.
        scen_dir = tmp_path / "scenarios" / "demo-001"
        tc = json.loads((scen_dir / "task_contract.json").read_text())
        tc["custom_priority"] = "p1"
        (scen_dir / "task_contract.json").write_text(json.dumps(tc, indent=2))
        # Pull the form, edit a known field, save it back.
        _, body = _get(port, "/api/scenarios/demo-001/form")
        form = json.loads(body)["form"]
        assert form["extras"]["task_contract"]["custom_priority"] == "p1"
        form["task_contract"]["success_criteria"] = ["new criterion"]
        status, _ = _post_json(port, "/api/scenarios/demo-001/form", {"form": form})
        assert status == 200
        # The custom field is still there.
        tc2 = json.loads((scen_dir / "task_contract.json").read_text())
        assert tc2["custom_priority"] == "p1"
        assert tc2["success_criteria"] == ["new criterion"]


def test_scenario_form_post_unknown_scenario_404(tmp_path: Path):
    """Saving against a name that doesn't exist returns 404, not a crash."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        (tmp_path / "scenarios").mkdir()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenarios/nope/form",
            method="POST",
            data=json.dumps({"form": {
                "task_contract": {"implementation_goal": "x"},
                "implementation_result": {"summary": "x", "confidence": "medium"},
                "e2e_result": {"status": "passed", "test_suite": "x",
                               "duration_seconds": 1},
            }}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_scenario_form_post_blocks_traversal_in_name(tmp_path: Path):
    """A traversal-y name in the form POST URL is rejected outright."""
    import urllib.error
    with _server(tmp_path) as (port, _):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/scenarios/..%2Fevil/form",
            method="POST",
            data=json.dumps({"form": {}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code in (400, 403, 404)


def test_scenario_create_writes_implementation_result_too(tmp_path: Path):
    """Newly-created scenarios are immediately runnable — all four
    projected files are present so the replay agent has no fallbacks
    to lean on."""
    with _server(tmp_path) as (port, _):
        _create_scenario(port, name="created-001", goal="my goal")
        d = tmp_path / "scenarios" / "created-001"
        for f in ("task_request.md", "task_contract.json",
                  "implementation_result.json", "e2e_result.json"):
            assert (d / f).exists(), f"{f} should be written on create"
        ir = json.loads((d / "implementation_result.json").read_text())
        assert ir["type"] == "implementation_result"
        assert ir["confidence"] in ("low", "medium", "high")
        tc = json.loads((d / "task_contract.json").read_text())
        assert tc["implementation_goal"] == "my goal"


# ----- /api/palette (Cmd+K) ------------------------------------------------


def test_palette_returns_baseline_tabs_and_actions(tmp_path: Path):
    """An empty repo still has tabs, builder sections, subviews, playbooks,
    schemas and the verb-style actions to offer the palette."""
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/palette")
        assert status == 200
        data = json.loads(body)
        items = data["items"]
        kinds = {it["kind"] for it in items}
        # Every category that doesn't need user content should be present.
        assert {"tab", "builder", "subview", "action"}.issubset(kinds)
        # Tabs cover all three top-level surfaces.
        tab_ids = {it["id"] for it in items if it["kind"] == "tab"}
        assert tab_ids == {"build", "run", "analyze"}
        # Built-in playbooks + schemas are listed.
        assert any(it["kind"] == "playbook" for it in items)
        assert any(it["kind"] == "schema" for it in items)
        # Quick actions are listed by id, not just title.
        action_ids = {it["id"] for it in items if it["kind"] == "action"}
        assert "scenario.new" in action_ids
        assert "shortcuts.help" in action_ids


def test_palette_includes_scenarios_with_goal_for_matching(tmp_path: Path):
    """A scenario's implementation_goal rides along as a subtitle so the
    user can find it by typing what they asked the agent to do, not just
    the slug they happened to pick."""
    with _server(tmp_path) as (port, _):
        _create_scenario(
            port, name="encoder-oom-001",
            goal="Fix encoder OOM at startup",
        )
        status, body = _get(port, "/api/palette")
        assert status == 200
        items = json.loads(body)["items"]
        scenarios = [it for it in items if it["kind"] == "scenario"]
        assert any(it["id"] == "encoder-oom-001" for it in scenarios)
        sc = next(it for it in scenarios if it["id"] == "encoder-oom-001")
        # The goal is exposed somewhere matchable (subtitle or keywords).
        haystack = (sc.get("subtitle", "") + " "
                    + sc.get("keywords", "")).lower()
        assert "encoder oom" in haystack


def test_palette_includes_recent_runs_with_status(tmp_path: Path):
    """Past runs surface in the palette so a user can jump from any tab
    straight to a task they ran yesterday."""
    runs = tmp_path / ".dev-loop" / "runs" / "task-abc"
    runs.mkdir(parents=True)
    (runs / "task_manifest.json").write_text(json.dumps({
        "task_id": "task-abc",
        "final_status": "passed",
        "task_contract": {"implementation_goal": "Wire up streaming"},
        "created_at_utc": "2026-05-21T12:00:00Z",
    }))
    with _server(tmp_path) as (port, _):
        items = json.loads(_get(port, "/api/palette")[1])["items"]
        runs_items = [it for it in items if it["kind"] == "run"]
        assert any(it["id"] == "task-abc" for it in runs_items)
        run = next(it for it in runs_items if it["id"] == "task-abc")
        assert run["status"] == "passed"
        assert "streaming" in (run["subtitle"] + " " + run["keywords"]).lower()


def test_palette_survives_a_corrupt_task_manifest(tmp_path: Path):
    """One bad manifest must not nuke the palette for the other runs."""
    runs = tmp_path / ".dev-loop" / "runs"
    (runs / "task-ok").mkdir(parents=True)
    (runs / "task-ok" / "task_manifest.json").write_text(json.dumps({
        "task_id": "task-ok", "final_status": "passed",
    }))
    (runs / "task-broken").mkdir(parents=True)
    (runs / "task-broken" / "task_manifest.json").write_text("{ not json")
    with _server(tmp_path) as (port, _):
        items = json.loads(_get(port, "/api/palette")[1])["items"]
        run_ids = {it["id"] for it in items if it["kind"] == "run"}
        assert "task-ok" in run_ids
        assert "task-broken" not in run_ids


def test_palette_caps_runs_to_avoid_huge_payload(tmp_path: Path):
    """The palette is a fast index, not a full archive — old runs are
    dropped server-side rather than sent to the client just to be
    filtered out client-side."""
    runs = tmp_path / ".dev-loop" / "runs"
    runs.mkdir(parents=True)
    for i in range(80):
        d = runs / f"task-{i:03d}"
        d.mkdir()
        (d / "task_manifest.json").write_text(json.dumps({
            "task_id": f"task-{i:03d}", "final_status": "passed",
        }))
    with _server(tmp_path) as (port, _):
        items = json.loads(_get(port, "/api/palette")[1])["items"]
        runs_items = [it for it in items if it["kind"] == "run"]
        assert len(runs_items) <= 60


# ----- /api/runs/<a>/compare/<b> (cross-run diff) -------------------------


def _write_run(
    tmp_path: Path,
    task_id: str,
    *,
    final_status: str = "passed",
    goal: str = "do x",
    iterations: list[dict] | None = None,
    audit_entries: list[dict] | None = None,
    selected: int | None = None,
    stop_reason: str | None = None,
    created: str = "2026-05-21T12:00:00Z",
    updated: str = "2026-05-21T12:01:00Z",
) -> Path:
    """Build a minimal but realistic run on disk for compare-endpoint tests."""
    run_dir = tmp_path / ".dev-loop" / "runs" / task_id
    run_dir.mkdir(parents=True)
    manifest = {
        "task_id": task_id,
        "status": "completed",
        "final_status": final_status,
        "created_at_utc": created,
        "updated_at_utc": updated,
        "task_contract": {"implementation_goal": goal},
    }
    if selected is not None:
        manifest["selected_iteration"] = selected
    if stop_reason is not None:
        manifest["stop_reason"] = stop_reason
    (run_dir / "task_manifest.json").write_text(json.dumps(manifest))
    iters_root = run_dir / "iterations"
    iters_root.mkdir()
    for it in iterations or []:
        n = it["i"]
        d = iters_root / f"iter-{n:03d}"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "iteration": n,
            "final_e2e_status": it.get("final_e2e_status"),
            "agent_output": {"summary": it.get("summary", "")},
            "code": {
                "patch_hash": it.get("patch_hash"),
                "changed_files": it.get("changed_files", []),
            },
        }))
        v = d / "validations"
        v.mkdir()
        for k in range(it.get("attempts", 0)):
            (v / f"attempt-{k + 1:03d}").mkdir()
    if audit_entries:
        (run_dir / "capability_audit.jsonl").write_text(
            "\n".join(json.dumps(e) for e in audit_entries) + "\n",
        )
    return run_dir


def test_compare_endpoint_two_passing_runs(tmp_path: Path):
    """Two healthy runs of the same scenario — same goal, same verdict,
    iteration count delta is zero."""
    _write_run(tmp_path, "run-a", iterations=[
        {"i": 1, "final_e2e_status": "passed", "summary": "fix",
         "patch_hash": "aaaa", "changed_files": ["src/x.py"], "attempts": 1},
    ])
    _write_run(tmp_path, "run-b", iterations=[
        {"i": 1, "final_e2e_status": "passed", "summary": "fix",
         "patch_hash": "aaaa", "changed_files": ["src/x.py"], "attempts": 1},
    ])
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/runs/run-a/compare/run-b")
        assert status == 200
        data = json.loads(body)
        assert data["a"]["task_id"] == "run-a"
        assert data["b"]["task_id"] == "run-b"
        d = data["deltas"]
        assert d["both_present"] is True
        assert d["same_goal"] is True
        assert d["same_final_status"] is True
        assert d["iteration_count_delta"] == 0
        assert d["first_diverging_iteration"] is None
        assert d["files_only_a"] == []
        assert d["files_only_b"] == []
        assert d["files_both"] == ["src/x.py"]


def test_compare_endpoint_shows_iteration_divergence(tmp_path: Path):
    """When the runs differ on a specific iteration, the endpoint pins
    where they diverge so the UI can land the user on that row."""
    _write_run(tmp_path, "run-a", final_status="passed", iterations=[
        {"i": 1, "final_e2e_status": "failed", "patch_hash": "a1",
         "changed_files": ["a.py"]},
        {"i": 2, "final_e2e_status": "passed", "patch_hash": "a2",
         "changed_files": ["b.py"]},
    ])
    _write_run(tmp_path, "run-b", final_status="failed_e2e", iterations=[
        {"i": 1, "final_e2e_status": "failed", "patch_hash": "a1",
         "changed_files": ["a.py"]},
        {"i": 2, "final_e2e_status": "failed", "patch_hash": "b2",
         "changed_files": ["c.py"]},
    ])
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/runs/run-a/compare/run-b")
        assert status == 200
        d = json.loads(body)["deltas"]
        assert d["first_diverging_iteration"] == 2
        assert d["same_final_status"] is False
        # Iter 1 matches on both fields.
        assert d["iteration_status_agreement"] >= 1
        assert sorted(d["files_only_a"]) == ["b.py"]
        assert sorted(d["files_only_b"]) == ["c.py"]
        assert d["files_both"] == ["a.py"]


def test_compare_endpoint_handles_missing_run(tmp_path: Path):
    """A stale share link with one missing run still returns a 200 so
    the user sees which side broke. 404 only when both are gone."""
    _write_run(tmp_path, "run-a", iterations=[
        {"i": 1, "final_e2e_status": "passed", "patch_hash": "x"},
    ])
    with _server(tmp_path) as (port, _):
        # One missing -> 200 with that side null.
        status, body = _get(port, "/api/runs/run-a/compare/does-not-exist")
        assert status == 200
        data = json.loads(body)
        assert data["a"] is not None
        assert data["b"] is None
        assert data["deltas"]["both_present"] is False
        # Both missing -> 404.
        import urllib.error
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/runs/nope/compare/also-nope")
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_compare_endpoint_rolls_up_audit_counts(tmp_path: Path):
    """Audit JSONL is summarised server-side so the UI can render the
    side-by-side counts without parsing JSONL in the browser."""
    _write_run(tmp_path, "run-a", audit_entries=[
        {"capability": "trigger_dev_jenkins_build", "status": "ok"},
        {"capability": "trigger_dev_jenkins_build", "status": "ok"},
        {"capability": "fetch_logs", "status": "ok"},
    ])
    _write_run(tmp_path, "run-b", audit_entries=[
        {"capability": "trigger_dev_jenkins_build", "status": "ok"},
        {"capability": "trigger_dev_jenkins_build",
         "status": "error", "error": "boom"},
    ])
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/runs/run-a/compare/run-b")
        assert status == 200
        data = json.loads(body)
        assert data["a"]["audit"]["total"] == 3
        assert data["b"]["audit"]["total"] == 2
        assert data["a"]["audit"]["by_capability"][
            "trigger_dev_jenkins_build"] == 2
        assert data["b"]["audit"]["by_status"]["error"] == 1
        assert data["deltas"]["audit_total_delta"] == -1


def test_compare_endpoint_survives_corrupt_iteration_manifest(tmp_path: Path):
    """A garbage iteration manifest must not break the whole compare."""
    run = _write_run(tmp_path, "run-a", iterations=[
        {"i": 1, "final_e2e_status": "passed", "patch_hash": "x"},
    ])
    (run / "iterations" / "iter-002").mkdir()
    (run / "iterations" / "iter-002" / "manifest.json").write_text("{not json")
    _write_run(tmp_path, "run-b", iterations=[
        {"i": 1, "final_e2e_status": "passed", "patch_hash": "x"},
    ])
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/api/runs/run-a/compare/run-b")
        assert status == 200
        data = json.loads(body)
        # Both iter dirs are accounted for; the corrupt one shows null
        # fields rather than dropping the row.
        assert data["a"]["iteration_count"] == 2
        statuses = [it["final_e2e_status"] for it in data["a"]["iterations"]]
        assert statuses[0] == "passed"
        assert statuses[1] is None


def test_compare_deltas_helper_pure():
    """``_compare_deltas`` is pure over the summary shape; test it in
    isolation so the UI layer can call it confidently from anywhere."""
    from harness.ui.server import _compare_deltas
    a = {
        "task_id": "a", "final_status": "passed", "goal": "g",
        "scenario": None, "duration_seconds": 60, "iteration_count": 2,
        "iterations": [
            {"i": 1, "final_e2e_status": "failed", "patch_hash": "p1",
             "changed_files": ["a.py"]},
            {"i": 2, "final_e2e_status": "passed", "patch_hash": "p2",
             "changed_files": ["b.py"]},
        ],
        "audit": {"total": 4, "by_status": {}, "by_capability": {}},
    }
    b = {
        "task_id": "b", "final_status": "passed", "goal": "g",
        "scenario": None, "duration_seconds": 30, "iteration_count": 1,
        "iterations": [
            {"i": 1, "final_e2e_status": "passed", "patch_hash": "pp",
             "changed_files": ["a.py", "c.py"]},
        ],
        "audit": {"total": 1, "by_status": {}, "by_capability": {}},
    }
    d = _compare_deltas(a, b)
    assert d["both_present"] is True
    assert d["same_final_status"] is True
    assert d["iteration_count_delta"] == -1   # b had one fewer
    assert d["duration_seconds_delta"] == -30  # b was 30s faster
    assert d["first_diverging_iteration"] == 1  # they differ at iter 1
    assert d["files_only_a"] == ["b.py"]
    assert d["files_only_b"] == ["c.py"]
    assert d["files_both"] == ["a.py"]
    assert d["audit_total_delta"] == -3
    # Either side missing → both_present False.
    assert _compare_deltas(None, b)["both_present"] is False
    assert _compare_deltas(a, None)["both_present"] is False


def test_palette_exposes_compare_action(tmp_path: Path):
    """Cmd+K -> "compare two runs" must be reachable so users discover
    the cross-run diff without hunting through the sidebar."""
    with _server(tmp_path) as (port, _):
        items = json.loads(_get(port, "/api/palette")[1])["items"]
        action_ids = {it["id"] for it in items if it["kind"] == "action"}
        assert "compare.runs" in action_ids


def test_compare_view_listed_in_index_doc(tmp_path: Path):
    """index.html ships the compare panel + the run-list compare toggle
    so the feature is reachable from the static document."""
    with _server(tmp_path) as (port, _):
        _, body = _get(port, "/")
        assert 'id="analyze-compare"' in body
        assert 'id="compare-mode-toggle"' in body


def test_palette_listed_in_index_doc(tmp_path: Path):
    """Sanity: index.html includes the palette trigger so the user can
    find the keyboard shortcut even before discovering Cmd+K."""
    with _server(tmp_path) as (port, _):
        status, body = _get(port, "/")
        assert status == 200
        assert 'id="palette-input"' in body
        assert "⌘K" in body
