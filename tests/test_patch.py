from pathlib import Path

from harness.patch import apply_patch_to_clean, extract_patch


def test_extract_patch_tracked_change(fresh_repo: Path):
    (fresh_repo / "a.txt").write_text("hello\n")
    import subprocess
    subprocess.run(["git", "add", "."], cwd=fresh_repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "a"], cwd=fresh_repo, check=True)
    (fresh_repo / "a.txt").write_text("hello world\n")
    info = extract_patch(fresh_repo, claimed_files=["a.txt"])
    assert "a.txt" in info.changed_files
    assert "+hello world" in info.diff
    assert info.patch_hash
    assert info.claim_mismatches == {}


def test_extract_patch_untracked_file(fresh_repo: Path):
    (fresh_repo / "new.py").write_text("print('hi')\n")
    info = extract_patch(fresh_repo, claimed_files=["new.py"])
    assert "new.py" in info.changed_files
    assert "+print" in info.diff
    # The file was untracked before extraction, so it must also be
    # recorded as untracked even though `git add -N` later staged it.
    assert "new.py" in info.untracked_files


def test_extract_patch_distinguishes_tracked_from_untracked(fresh_repo: Path):
    import subprocess
    # one tracked-and-modified, one brand-new untracked.
    (fresh_repo / "tracked.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=fresh_repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=fresh_repo, check=True)
    (fresh_repo / "tracked.txt").write_text("v2\n")
    (fresh_repo / "untracked.txt").write_text("u\n")
    info = extract_patch(fresh_repo)
    assert "tracked.txt" in info.changed_files
    assert "untracked.txt" in info.changed_files
    assert info.untracked_files == ["untracked.txt"]


def test_extract_patch_deleted_file(fresh_repo: Path):
    import subprocess
    (fresh_repo / "doomed.txt").write_text("bye\n")
    subprocess.run(["git", "add", "."], cwd=fresh_repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=fresh_repo, check=True)
    (fresh_repo / "doomed.txt").unlink()
    info = extract_patch(fresh_repo)
    assert "doomed.txt" in info.deleted_files
    assert "doomed.txt" in info.changed_files


def test_apply_patch_to_clean_deletion(fresh_repo: Path, tmp_path: Path):
    import subprocess
    (fresh_repo / "doomed.txt").write_text("bye\n")
    subprocess.run(["git", "add", "."], cwd=fresh_repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=fresh_repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fresh_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    (fresh_repo / "doomed.txt").unlink()
    info = extract_patch(fresh_repo)
    dest = tmp_path / "clean-del"
    apply_patch_to_clean(fresh_repo, base_sha, info.diff, dest)
    assert not (dest / "doomed.txt").exists()


def test_claim_mismatches_reported(fresh_repo: Path):
    (fresh_repo / "added.txt").write_text("x\n")
    (fresh_repo / "also_added.txt").write_text("y\n")
    info = extract_patch(fresh_repo, claimed_files=["added.txt", "ghost.txt"])
    assert "also_added.txt" in info.claim_mismatches.get("changed_but_not_claimed", [])
    assert "ghost.txt" in info.claim_mismatches.get("claimed_but_not_changed", [])


def test_apply_patch_to_clean_empty_diff(fresh_repo: Path, tmp_path: Path):
    import subprocess
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fresh_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    dest = tmp_path / "clean-empty"
    # Empty diff must not raise.
    apply_patch_to_clean(fresh_repo, base_sha, "", dest)
    assert (dest / "README.md").exists()


def test_apply_patch_to_clean_untracked_new_file(fresh_repo: Path, tmp_path: Path):
    import subprocess
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fresh_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    (fresh_repo / "fresh.txt").write_text("brand new\n")
    info = extract_patch(fresh_repo, claimed_files=["fresh.txt"])
    assert "fresh.txt" in info.untracked_files
    dest = tmp_path / "clean-new"
    apply_patch_to_clean(fresh_repo, base_sha, info.diff, dest)
    assert (dest / "fresh.txt").read_text() == "brand new\n"


def test_apply_patch_to_clean_conflict_raises(fresh_repo: Path, tmp_path: Path):
    """An unappliable diff must raise CalledProcessError so the
    orchestrator can synthesize a harness-issue triage."""
    import subprocess
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fresh_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    bogus = (
        "diff --git a/never.txt b/never.txt\n"
        "--- a/never.txt\n"
        "+++ b/never.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "-was here\n"
        "+now here\n"
    )
    dest = tmp_path / "clean-conflict"
    try:
        apply_patch_to_clean(fresh_repo, base_sha, bogus, dest)
    except subprocess.CalledProcessError:
        return
    raise AssertionError("expected CalledProcessError on unappliable diff")


def test_apply_patch_to_clean(fresh_repo: Path, tmp_path: Path):
    import subprocess
    (fresh_repo / "a.py").write_text("v = 1\n")
    subprocess.run(["git", "add", "."], cwd=fresh_repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "a"], cwd=fresh_repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fresh_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    (fresh_repo / "a.py").write_text("v = 2\n")
    info = extract_patch(fresh_repo, claimed_files=["a.py"])
    dest = tmp_path / "clean"
    apply_patch_to_clean(fresh_repo, base_sha, info.diff, dest)
    assert (dest / "a.py").read_text() == "v = 2\n"


def test_extract_patch_rename_marks_old_path_deleted(fresh_repo: Path):
    """A renamed file's old path is gone from the working tree, so it
    must show up in ``deleted_files`` even when the rename is also a
    modification (porcelain code 'RM')."""
    import subprocess
    (fresh_repo / "old.txt").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=fresh_repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=fresh_repo, check=True)
    subprocess.run(["git", "mv", "old.txt", "new.txt"], cwd=fresh_repo, check=True)
    (fresh_repo / "new.txt").write_text("hello world\n")
    info = extract_patch(fresh_repo)
    # Both ends are recorded as changed.
    assert "old.txt" in info.changed_files
    assert "new.txt" in info.changed_files
    # The rename source must also be flagged as deleted.
    assert "old.txt" in info.deleted_files
