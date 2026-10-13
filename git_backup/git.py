"""Clone each repository on the first run and fast-forward it on later runs, in parallel.

Global context: phase 2 of backup.py. A normal `git clone` copies the full history of every branch
(as origin/* remote-tracking refs) and checks out the default branch, so each clone is both a
complete backup and a browsable folder. Git LFS files are downloaded by the LFS filter during checkout.
git clone: https://git-scm.com/docs/git-clone   git pull: https://git-scm.com/docs/git-pull
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from git_backup.github import Repo
from git_backup.shell import run

log = logging.getLogger(__name__)

# Per-command config (`git -c`), so the user's global git config is never changed
# (https://git-scm.com/docs/git#Documentation/git.txt--cltnamegtltvaluegt):
GIT_FLAGS = [
    # An empty helper resets the helper list, then gh supplies its stored token — the same pair
    # `gh auth setup-git` would write (https://cli.github.com/manual/gh_auth_setup-git,
    # https://git-scm.com/docs/gitcredentials#Documentation/gitcredentials.txt-helper).
    "-c", "credential.helper=",
    "-c", "credential.helper=!gh auth git-credential",
    # Allow paths longer than 260 characters on Windows (https://git-scm.com/docs/git-config#Documentation/git-config.txt-corelongPaths).
    "-c", "core.longpaths=true",
]


@dataclass(frozen=True)
class Result:
    """Outcome of one repository: status is cloned / updated / partial / failed."""

    repo: str  # "owner/name"
    status: str
    detail: str = ""  # last line of git's output, or the error text


def clone_or_update(url: str, dest: Path, empty: bool = False) -> Result:
    """Clone url into dest if dest is not a repo yet, otherwise update it from origin."""
    label = f"{dest.parent.name}/{dest.name}"  # repos/<owner>/<name> -> "owner/name"
    if (dest / ".git").is_dir():
        # Empty repos have no branch to merge, so only fetch; otherwise fetch all branches,
        # prune deleted ones and fast-forward the checked-out branch (never creates merge commits).
        action = ["fetch", "--prune"] if empty else ["pull", "--ff-only", "--prune"]
        proc = run(["git", *GIT_FLAGS, "-C", str(dest), *action])
        status = "updated" if proc.returncode == 0 else "failed"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)  # repos/<owner>/ on first use
        proc = run(["git", *GIT_FLAGS, "clone", url, str(dest)])
        if proc.returncode == 0:
            status = "cloned"
        else:
            # git deletes the folder when the download fails, but keeps it when only the checkout
            # fails (e.g. a file name that is invalid on Windows): history is saved, files are not.
            status = "partial" if (dest / ".git").is_dir() else "failed"
    lines = (proc.stderr or proc.stdout).strip().splitlines()
    return Result(label, status, lines[-1] if lines else "")


def backup_all(repos: list[Repo], backup_dir: Path, jobs: int) -> list[Result]:
    """Clone/update every repo into backup_dir/<owner>/<name> using `jobs` parallel workers."""
    results = []
    # Threads are enough: the work happens in git child processes, Python only waits
    # (https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor).
    with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="git") as pool:
        futures = {pool.submit(timed, r, backup_dir / r.owner / r.name): r for r in repos}
        # as_completed yields each future as soon as it finishes, so progress is logged live.
        for i, future in enumerate(as_completed(futures), start=1):
            result, seconds = future.result()
            level = logging.INFO if result.status in ("cloned", "updated") else logging.WARNING
            log.log(level, "[%d/%d] %-7s %s (%.1fs) %s", i, len(repos), result.status,
                    result.repo, seconds, result.detail)
            results.append(result)
    return results


def timed(repo: Repo, dest: Path) -> tuple[Result, float]:
    """Run clone_or_update for one repo and measure how long it took."""
    log.info("start   %s", repo.full_name)
    start = time.perf_counter()  # monotonic clock for durations (https://docs.python.org/3/library/time.html#time.perf_counter)
    result = clone_or_update(repo.url, dest, empty=repo.is_empty)
    return result, time.perf_counter() - start
