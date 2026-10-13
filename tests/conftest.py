"""Shared pytest fixtures: throwaway local git "remotes" that stand in for GitHub.

Global context: the real tool clones https://github.com/<owner>/<name>; tests point it at
bare repositories on disk instead, so they run offline and fast.
Fixtures docs: https://docs.pytest.org/en/stable/how-to/fixtures.html
"""

import subprocess
from pathlib import Path

import pytest


def git(*args: str, cwd: Path) -> str:
    """Run a git command in cwd for test setup and return its stdout (raises on failure)."""
    # check=True raises CalledProcessError so a broken fixture fails loudly
    # (https://docs.python.org/3/library/subprocess.html#subprocess.run).
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def commit_file(work: Path, name: str, size: int, message: str) -> None:
    """Write a file of exactly `size` bytes into the work tree, commit it and push to origin."""
    (work / name).parent.mkdir(parents=True, exist_ok=True)  # allow nested paths like data/x.bin
    (work / name).write_bytes(b"x" * size)  # known size lets tests assert exact byte counts
    git("add", name, cwd=work)
    git("commit", "-q", "-m", message, cwd=work)
    git("push", "-q", "origin", "HEAD", cwd=work)  # publish the commit to the bare "remote"


@pytest.fixture
def remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare repo (the fake GitHub remote) plus a work clone used to push commits to it.

    Returns (bare_repo_path, work_tree_path).
    """
    bare = tmp_path / "remote.git"
    # --initial-branch keeps the branch name stable regardless of the machine's init.defaultBranch
    # (https://git-scm.com/docs/git-init#Documentation/git-init.txt---initial-branchbranch-name).
    git("init", "-q", "--bare", "--initial-branch=main", str(bare), cwd=tmp_path)
    work = tmp_path / "work"
    git("clone", "-q", str(bare), str(work), cwd=tmp_path)
    # Local identity so commits work on CI machines without a global git config.
    git("config", "user.email", "test@example.com", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    # Cloning an empty repo leaves HEAD unborn; point it at main explicitly
    # (https://git-scm.com/docs/git-symbolic-ref).
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=work)
    commit_file(work, "README.md", 10, "initial")
    return bare, work
