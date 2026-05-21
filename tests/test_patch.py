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


def test_claim_mismatches_reported(fresh_repo: Path):
    (fresh_repo / "added.txt").write_text("x\n")
    (fresh_repo / "also_added.txt").write_text("y\n")
    info = extract_patch(fresh_repo, claimed_files=["added.txt", "ghost.txt"])
    assert "also_added.txt" in info.claim_mismatches.get("changed_but_not_claimed", [])
    assert "ghost.txt" in info.claim_mismatches.get("claimed_but_not_changed", [])


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
