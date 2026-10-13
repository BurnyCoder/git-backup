"""Clone each repository on the first run and fast-forward it on later runs, in parallel.

Global context: phase 2 of backup.py. A normal `git clone` copies the full history of every branch
(as origin/* remote-tracking refs) and checks out the default branch, so each clone is both a
complete backup and a browsable folder. Git LFS files are downloaded by the LFS filter during checkout.
git clone: https://git-scm.com/docs/git-clone   git pull: https://git-scm.com/docs/git-pull
"""

import logging
import shutil
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

# Sparse-checkout patterns (gitignore syntax, non-cone mode): check out everything except names
# containing characters Windows forbids (https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file),
# e.g. "file.md:Zone.Identifier" copied from WSL. Such files stay in git history, just not on disk.
# https://git-scm.com/docs/git-sparse-checkout#_internalsnon_cone_problems
WINDOWS_SAFE = ["/*", "!*:*", '!*"*', "!*<*", "!*>*", "!*|*", "!*\\?*", "!*\\**"]


def git_in(dest: Path, *args: str):
    """Run `git <GIT_FLAGS> -C dest <args>` through shell.run."""
    return run(["git", *GIT_FLAGS, "-C", str(dest), *args])


def error_line(text: str) -> str:
    """The most useful line of git output: the first error/fatal line, else the last line."""
    lines = text.strip().splitlines()
    return next((l for l in lines if l.startswith(("error:", "fatal:"))), lines[-1] if lines else "")


def skip_invalid_paths(dest: Path) -> int | None:
    """Make a checkout that failed on Windows-invalid names succeed by leaving those files out.

    Returns how many files were skipped, or None if the repair failed.
    core.protectNTFS=false (this repo only) lets the index hold such names; the sparse patterns
    then keep them off the disk, which is what protectNTFS guards against
    (https://git-scm.com/docs/git-config#Documentation/git-config.txt-coreprotectNTFS).
    Same fix as https://support.atlassian.com/bitbucket-cloud/kb/invalid-path-fatal-unable-to-checkout-working-tree-while-cloning-the-repository-in-windows/
    plus sparse-checkout so the invalid files are never written.
    """
    for args in (["config", "core.protectNTFS", "false"],
                 ["sparse-checkout", "set", "--no-cone", *WINDOWS_SAFE],
                 ["reset", "-q", "--hard", "HEAD"]):  # rebuild index + working tree from HEAD
        if git_in(dest, *args).returncode != 0:
            return None
    # `ls-files -t` tags skip-worktree (sparse-excluded) entries with "S"
    # (https://git-scm.com/docs/git-ls-files#Documentation/git-ls-files.txt--t).
    return sum(line.startswith("S ") for line in git_in(dest, "ls-files", "-t").stdout.splitlines())


def drop_lfs(dest: Path) -> int:
    """Delete a clone's downloaded Git LFS content, keep the small pointer files; return bytes freed.

    lfs.fetchexclude=* stops git-lfs from downloading this repo's LFS files again
    (https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-config.adoc). With the local
    object store gone, the smudge filter writes the pointer instead of the content on checkout.
    The real files remain in GitHub's LFS storage.
    """
    git_in(dest, "config", "lfs.fetchexclude", "*")
    store = dest / ".git" / "lfs" / "objects"
    if not any(f.is_file() for f in store.rglob("*")):  # nothing downloaded (git-lfs may leave empty dirs)
        return 0
    # `git lfs ls-files --name-only` lists the LFS-tracked paths of HEAD
    # (https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-ls-files.adoc).
    files = [dest / p for p in git_in(dest, "lfs", "ls-files", "--name-only").stdout.splitlines()]
    files = [f for f in files if f.is_file()]
    freed = sum(f.stat().st_size for f in [*files, *store.rglob("*")] if f.is_file())
    shutil.rmtree(store)  # the LFS copies under .git/
    for f in files:
        f.unlink()  # the checked-out copies...
    git_in(dest, "checkout", "--", ".")  # ...are re-created as pointer files
    return freed


def maintain(dest: Path, skip_lfs: bool = False) -> list[str]:
    """Fix an existing clone in place without network access; return notes on what was changed.

    - A clone whose checkout failed has history but no .git/index: rebuild it, skipping Windows-invalid names.
    - Repos listed in SKIP_LFS_REPOS: remove downloaded LFS content.
    """
    notes = []
    has_commits = git_in(dest, "rev-parse", "-q", "--verify", "HEAD").returncode == 0  # empty repos have none
    if has_commits and not (dest / ".git" / "index").exists():
        skipped = skip_invalid_paths(dest)
        notes.append(f"checkout rebuilt, {skipped} file(s) with Windows-invalid names skipped (kept in history)"
                     if skipped is not None else "checkout repair failed")
    if skip_lfs and (freed := drop_lfs(dest)):
        notes.append(f"LFS content removed, {freed / 2**20:.0f} MB freed")
    return notes


@dataclass(frozen=True)
class Result:
    """Outcome of one repository: status is cloned / updated / partial / failed."""

    repo: str  # "owner/name"
    status: str
    detail: str = ""  # last line of git's output, or the error text


def clone_or_update(url: str, dest: Path, empty: bool = False, skip_lfs: bool = False) -> Result:
    """Clone url into dest if dest is not a repo yet, otherwise update it from origin."""
    label = f"{dest.parent.name}/{dest.name}"  # repos/<owner>/<name> -> "owner/name"
    notes = []
    if (dest / ".git").is_dir():
        notes = maintain(dest, skip_lfs)  # repair/slim first so the pull works on a healthy clone
        # Empty repos have no branch to merge, so only fetch; otherwise fetch all branches,
        # prune deleted ones and fast-forward the checked-out branch (never creates merge commits).
        proc = git_in(dest, *(["fetch", "--prune"] if empty else ["pull", "--ff-only", "--prune"]))
        ok_status = "updated"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)  # repos/<owner>/ on first use
        # --config is stored in the new repo before checkout, so LFS content is never downloaded
        # (https://git-scm.com/docs/git-clone#Documentation/git-clone.txt---configkeyvalue).
        lfs = ["--config", "lfs.fetchexclude=*"] if skip_lfs else []
        proc = run(["git", *GIT_FLAGS, "clone", *lfs, url, str(dest)])
        ok_status = "cloned"
    output = proc.stderr or proc.stdout
    if proc.returncode == 0:
        return Result(label, ok_status, "; ".join(notes) or error_line(output))
    # git deletes the folder when a clone's download fails, but keeps it when only the checkout fails;
    # "invalid path" means a file name Windows cannot store: skip those files, keep the rest.
    if "invalid path" in output and (dest / ".git").is_dir():
        skipped = skip_invalid_paths(dest)
        # A failed pull fetched but did not move the branch yet, so retry it once after the repair.
        if skipped is not None and (ok_status == "cloned" or git_in(dest, "pull", "--ff-only").returncode == 0):
            return Result(label, "partial", f"{skipped} file(s) with Windows-invalid names skipped (kept in history)")
    return Result(label, "failed", error_line(output))


def maintain_all(backup_dir: Path, skip_lfs: set[str]) -> list[Result]:
    """Run maintain() on every existing clone (no GitHub access); return results for changed repos."""
    results = []
    clones = [d for d in backup_dir.glob("*/*") if (d / ".git").is_dir()]
    # Repos that will shed LFS content go first (False sorts before True): free space before
    # rebuilding checkouts, which needs space.
    for dest in sorted(clones, key=lambda d: (f"{d.parent.name}/{d.name}" not in skip_lfs, str(d))):
        label = f"{dest.parent.name}/{dest.name}"
        if notes := maintain(dest, label in skip_lfs):
            results.append(Result(label, "partial" if "Windows-invalid" in notes[0] else "updated", "; ".join(notes)))
            log.info("maintained %s: %s", label, results[-1].detail)
    return results


def backup_all(repos: list[Repo], backup_dir: Path, jobs: int, skip_lfs: set[str] = frozenset()) -> list[Result]:
    """Clone/update every repo into backup_dir/<owner>/<name> using `jobs` parallel workers."""
    results = []
    # Threads are enough: the work happens in git child processes, Python only waits
    # (https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor).
    with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="git") as pool:
        futures = {pool.submit(timed, r, backup_dir / r.owner / r.name, r.full_name in skip_lfs): r for r in repos}
        # as_completed yields each future as soon as it finishes, so progress is logged live.
        for i, future in enumerate(as_completed(futures), start=1):
            result, seconds = future.result()
            level = logging.INFO if result.status in ("cloned", "updated") else logging.WARNING
            log.log(level, "[%d/%d] %-7s %s (%.1fs) %s", i, len(repos), result.status,
                    result.repo, seconds, result.detail)
            results.append(result)
    return results


def timed(repo: Repo, dest: Path, skip_lfs: bool) -> tuple[Result, float]:
    """Run clone_or_update for one repo and measure how long it took."""
    log.info("start   %s", repo.full_name)
    start = time.perf_counter()  # monotonic clock for durations (https://docs.python.org/3/library/time.html#time.perf_counter)
    result = clone_or_update(repo.url, dest, empty=repo.is_empty, skip_lfs=skip_lfs)
    return result, time.perf_counter() - start
