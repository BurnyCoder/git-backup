"""Tests for git_backup.github: parsing `gh repo list --json` output and filtering forks."""

import json
import subprocess

import pytest

from git_backup import github

# Shape mirrors `gh repo list --json nameWithOwner,url,isFork,isEmpty,diskUsage`
# (https://cli.github.com/manual/gh_repo_list).
SAMPLE = json.dumps([
    {"nameWithOwner": "alice/tool", "url": "https://github.com/alice/tool", "isFork": False, "isEmpty": False, "diskUsage": 2048},
    {"nameWithOwner": "alice/fork", "url": "https://github.com/alice/fork", "isFork": True, "isEmpty": False, "diskUsage": 100},
    {"nameWithOwner": "alice/blank", "url": "https://github.com/alice/blank", "isFork": False, "isEmpty": True, "diskUsage": None},
])


def fake_run(stdout: str, returncode: int = 0):
    """Build a stand-in for shell.run that returns a canned CompletedProcess."""
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="boom")


def test_parse_repos_maps_fields():
    repos = github.parse_repos(SAMPLE)
    assert [r.full_name for r in repos] == ["alice/tool", "alice/fork", "alice/blank"]
    assert repos[0].owner == "alice" and repos[0].name == "tool"
    assert repos[0].disk_kb == 2048
    assert repos[1].is_fork and repos[2].is_empty
    assert repos[2].disk_kb == 0  # null diskUsage becomes 0


def test_list_repos_filters_forks(monkeypatch):
    monkeypatch.setattr(github, "run", fake_run(SAMPLE))
    assert len(github.list_repos("alice", include_forks=True)) == 3
    assert [r.name for r in github.list_repos("alice", include_forks=False)] == ["tool", "blank"]


def test_list_repos_raises_when_gh_fails(monkeypatch):
    monkeypatch.setattr(github, "run", fake_run("", returncode=1))
    with pytest.raises(RuntimeError, match="boom"):
        github.list_repos("alice", include_forks=True)
