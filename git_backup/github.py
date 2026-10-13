"""List an owner's GitHub repositories through the already-authenticated GitHub CLI (`gh`).

Global context: phase 1 of backup.py. Using `gh` means private repos are included with the token
gh already stores; no token handling lives in this project.
`gh repo list` manual (JSON fields, --limit): https://cli.github.com/manual/gh_repo_list
"""

import json
import logging
from dataclasses import dataclass

from git_backup.shell import run

log = logging.getLogger(__name__)

# JSON fields requested from gh; diskUsage is in kilobytes (GitHub's repository `size`,
# https://docs.github.com/en/graphql/reference/objects#repository).
FIELDS = "nameWithOwner,url,isFork,isEmpty,diskUsage"


@dataclass(frozen=True)
class Repo:
    """One GitHub repository as needed by the clone and report phases."""

    owner: str
    name: str
    url: str  # https clone URL
    is_fork: bool
    is_empty: bool  # no commits yet: updates only fetch
    disk_kb: int  # GitHub's packed size estimate, used for the disk-space check

    @property
    def full_name(self) -> str:
        """'owner/name', the label used in logs and the report."""
        return f"{self.owner}/{self.name}"


def parse_repos(json_text: str) -> list[Repo]:
    """Turn `gh repo list --json ...` output into Repo records."""
    repos = []
    for item in json.loads(json_text):
        owner, name = item["nameWithOwner"].split("/", 1)  # "alice/tool" -> ("alice", "tool")
        repos.append(Repo(owner, name, item["url"], item["isFork"], item["isEmpty"], item["diskUsage"] or 0))
    return repos


def list_repos(owner: str, include_forks: bool) -> list[Repo]:
    """Return every repo owned by `owner` (user or org), optionally without forks."""
    # --limit default is 30; 5000 covers any realistic account in one call.
    proc = run(["gh", "repo", "list", owner, "--limit", "5000", "--json", FIELDS])
    if proc.returncode != 0:
        raise RuntimeError(f"gh repo list {owner} failed: {proc.stderr.strip()}")
    repos = parse_repos(proc.stdout)
    kept = [r for r in repos if include_forks or not r.is_fork]
    log.info("%s: %d repos listed, %d kept (forks %s)", owner, len(repos), len(kept),
             "included" if include_forks else "skipped")
    return kept
