from pathlib import Path

from harness.playbooks import list_playbooks, show_playbook


def test_list_playbooks_returns_builtins_with_phase_bindings():
    """``list_playbooks`` is the single source of truth for the
    ``playbooks ls`` CLI and ``/api/playbooks``. It must cover every
    built-in, sort alphabetically, and annotate which agent phases bind
    to each playbook."""
    rows = list_playbooks()
    names = [r["name"] for r in rows]
    assert names == sorted(names)
    assert "implement_feature.v1.md" in names
    assert "gpu_e2e_failure_triage.v1.md" in names

    impl = next(r for r in rows if r["name"] == "implement_feature.v1.md")
    assert impl["source"] == "built-in"
    assert impl["has_builtin"] is True
    assert impl["overridden"] is False
    assert impl["line_count"] > 0
    assert impl["size_bytes"] > 0
    assert set(impl["agent_phases"]) == {"implementation", "task_contract"}

    triage = next(r for r in rows if r["name"] == "gpu_e2e_failure_triage.v1.md")
    assert triage["agent_phases"] == ["failure_triage"]


def test_list_playbooks_surfaces_repo_overrides(tmp_path: Path):
    pb_dir = tmp_path / ".dev-loop" / "playbooks"
    pb_dir.mkdir(parents=True)
    (pb_dir / "implement_feature.v1.md").write_text("# repo override\n")

    rows = list_playbooks(repo=tmp_path)
    impl = next(r for r in rows if r["name"] == "implement_feature.v1.md")
    assert impl["overridden"] is True
    assert impl["source"] == "repo-override"
    assert impl["has_builtin"] is True
    assert Path(impl["path"]).read_text(encoding="utf-8") == "# repo override\n"


def test_list_playbooks_picks_up_repo_only_playbooks(tmp_path: Path):
    """A playbook that only exists in the repo override dir (no matching
    built-in) must still appear, marked appropriately."""
    pb_dir = tmp_path / ".dev-loop" / "playbooks"
    pb_dir.mkdir(parents=True)
    (pb_dir / "custom_team_playbook.md").write_text("# custom\n")

    rows = list_playbooks(repo=tmp_path)
    custom = next(
        (r for r in rows if r["name"] == "custom_team_playbook.md"), None,
    )
    assert custom is not None
    assert custom["overridden"] is True
    assert custom["has_builtin"] is False
    assert custom["source"] == "repo-override"
    assert custom["agent_phases"] == []


def test_show_playbook_returns_text_by_default():
    detail = show_playbook("implement_feature.v1.md")
    assert detail is not None
    assert detail["name"] == "implement_feature.v1.md"
    assert "implementation_goal" in detail.get("text", "")


def test_show_playbook_metadata_only_omits_text():
    detail = show_playbook("implement_feature.v1.md", include_text=False)
    assert detail is not None
    assert "text" not in detail


def test_show_playbook_missing_returns_none():
    assert show_playbook("does_not_exist.md") is None
