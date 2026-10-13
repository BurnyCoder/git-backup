"""Tests for git_backup.git: clone on first run, fast-forward on later runs, failures reported."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import commit_file, git
from git_backup import git as gitops
from git_backup.github import Repo


def make_repo(url: Path, name: str = "proj", empty: bool = False) -> Repo:
    """A Repo record pointing at a local bare repository instead of GitHub."""
    return Repo(owner="alice", name=name, url=str(url), is_fork=False, is_empty=empty, disk_kb=1)


def test_first_run_clones_then_second_run_updates(remote, tmp_path):
    bare, work = remote
    dest = tmp_path / "backup" / "proj"
    assert gitops.clone_or_update(str(bare), dest).status == "cloned"
    assert (dest / "README.md").exists()

    commit_file(work, "new.txt", 5, "second")  # remote moves ahead
    result = gitops.clone_or_update(str(bare), dest)
    assert result.status == "updated"
    assert (dest / "new.txt").exists()  # fast-forwarded into the working tree


def test_clone_fetches_all_branches(remote, tmp_path):
    bare, work = remote
    git("push", "-q", "origin", "HEAD:refs/heads/feature", cwd=work)  # second branch on the remote
    dest = tmp_path / "proj"
    gitops.clone_or_update(str(bare), dest)
    assert "origin/feature" in git("branch", "-r", cwd=dest)  # full backup, not just the default branch


def test_empty_remote_clones_and_updates(tmp_path):
    bare = tmp_path / "empty.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    dest = tmp_path / "empty"
    assert gitops.clone_or_update(str(bare), dest, empty=True).status == "cloned"
    assert gitops.clone_or_update(str(bare), dest, empty=True).status == "updated"


@pytest.mark.skipif(sys.platform != "win32", reason="':' is a valid file name character outside Windows")
def test_windows_invalid_names_are_skipped_not_fatal(remote, tmp_path):
    bare, work = remote
    # Commit a path Windows cannot store, using plumbing so nothing is written to the work tree
    # (https://git-scm.com/docs/git-update-index#Documentation/git-update-index.txt---cacheinfoltmodegtltobjectgtltpathgt).
    git("config", "core.protectNTFS", "false", cwd=work)
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=work, input="ads",
                          capture_output=True, text=True, check=True).stdout.strip()  # store content, get its id
    git("update-index", "--add", "--cacheinfo", f"100644,{blob},notes.md:Zone.Identifier", cwd=work)
    git("commit", "-q", "-m", "bad name", cwd=work)
    git("push", "-q", "origin", "HEAD", cwd=work)

    dest = tmp_path / "proj"
    result = gitops.clone_or_update(str(bare), dest)
    assert result.status == "partial"
    assert "1 file" in result.detail
    assert (dest / "README.md").exists()  # the rest of the tree is checked out
    assert git("status", "--porcelain", cwd=dest) == ""  # clean: the bad file is skipped, not deleted
    commit_file(work, "later.txt", 3, "after")  # later updates keep working
    assert gitops.clone_or_update(str(bare), dest).status == "updated"
    assert (dest / "later.txt").exists()


def test_bad_url_is_reported_not_raised(tmp_path):
    result = gitops.clone_or_update(str(tmp_path / "missing.git"), tmp_path / "x")
    assert result.status == "failed"
    assert result.detail  # git's error text is kept for the log and the report


def test_backup_all_places_repos_under_owner(remote, tmp_path):
    bare, _ = remote
    results = gitops.backup_all([make_repo(bare, "a"), make_repo(bare, "b")], tmp_path / "repos", jobs=2)
    assert sorted(r.repo for r in results) == ["alice/a", "alice/b"]
    assert all(r.status == "cloned" for r in results)
    assert (tmp_path / "repos" / "alice" / "a" / "README.md").exists()


def lfs_remote(remote):
    """Add an LFS-tracked 2000-byte file to the fixture remote (git-lfs works with file:// remotes)."""
    bare, work = remote
    git("lfs", "install", "--local", cwd=work)  # hooks for this work repo only
    git("lfs", "track", "*.bin", cwd=work)  # writes the filter rule into .gitattributes
    git("add", ".gitattributes", cwd=work)
    commit_file(work, "data/big.bin", 2000, "lfs file")  # push also uploads the LFS object
    return bare


def lfs_files(dest: Path) -> list[Path]:
    """Downloaded LFS objects of a clone (git-lfs may leave empty folders behind)."""
    return [f for f in (dest / ".git" / "lfs" / "objects").rglob("*") if f.is_file()]


def is_pointer(path: Path) -> bool:
    """LFS pointer files start with this spec line (https://github.com/git-lfs/git-lfs/blob/main/docs/spec.md)."""
    return path.read_bytes().startswith(b"version https://git-lfs.github.com/spec/v1")


needs_lfs = pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs not installed")


@needs_lfs
def test_drop_lfs_keeps_pointers_and_frees_space(remote, tmp_path):
    dest = tmp_path / "proj"
    gitops.clone_or_update(str(lfs_remote(remote)), dest)
    assert not is_pointer(dest / "data" / "big.bin")  # normal clone downloads LFS content

    freed = gitops.drop_lfs(dest)
    assert freed >= 4000  # the LFS store copy + the checked-out copy
    assert lfs_files(dest) == []
    assert is_pointer(dest / "data" / "big.bin")
    assert git("status", "--porcelain", cwd=dest) == ""  # pointers match what git has committed
    assert gitops.clone_or_update(str(remote[0]), dest, skip_lfs=True).status == "updated"
    assert lfs_files(dest) == []  # updates don't download it again


@needs_lfs
def test_clone_with_skip_lfs_downloads_pointers_only(remote, tmp_path):
    dest = tmp_path / "proj"
    assert gitops.clone_or_update(str(lfs_remote(remote)), dest, skip_lfs=True).status == "cloned"
    assert is_pointer(dest / "data" / "big.bin")


def test_maintain_repairs_checkout_without_index(remote, tmp_path):
    bare, _ = remote
    dest = tmp_path / "proj"
    gitops.clone_or_update(str(bare), dest)
    (dest / ".git" / "index").unlink()  # what a clone looks like after its checkout failed
    (dest / "README.md").unlink()
    assert gitops.maintain(dest)  # returns notes about what it fixed
    assert (dest / "README.md").exists() and git("status", "--porcelain", cwd=dest) == ""
    assert gitops.maintain(dest) == []  # healthy clone: nothing to do


@needs_lfs
def test_missing_lfs_object_keeps_pointer_instead_of_failing(remote, tmp_path):
    bare, work = remote
    # Commit an LFS pointer whose object was never uploaded (like a 404 on GitHub's LFS server).
    (work / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
    (work / "gone.bin").write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 5\n")
    git("add", ".gitattributes", "gone.bin", cwd=work)
    git("commit", "-q", "-m", "dangling lfs pointer", cwd=work)
    git("push", "-q", "--no-verify", "origin", "HEAD", cwd=work)  # skip the LFS pre-push upload hook
    dest = tmp_path / "proj"
    assert gitops.clone_or_update(str(bare), dest).status == "cloned"
    assert is_pointer(dest / "gone.bin") and (dest / "README.md").exists()
