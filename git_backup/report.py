"""Measure the backup on disk and write reports/size-report.md: what uses the most space.

Global context: report.check_disk_space runs before cloning (warns if the drive may fill up);
report.write_report runs last and answers "how big is the backup and which files are largest",
both for files you can open (working trees) and for blobs stored only in git history.
"""

import logging
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from git_backup.git import Result
from git_backup.shell import run

log = logging.getLogger(__name__)

# A normal clone ≈ packed history (GitHub's diskUsage) + checked-out files, which we measured at
# roughly the same size again; 2.5x leaves headroom for LFS files and unpacked objects.
SPACE_FACTOR = 2.5


@dataclass(frozen=True)
class FileEntry:
    """One file found under the backup folder."""

    repo: str  # "owner/name"
    path: str  # path inside the repo, forward slashes
    size: int  # bytes on disk (st_size)
    in_git: bool  # True for files under .git/ (history, packs), False for working files


@dataclass(frozen=True)
class RepoSize:
    """Disk usage of one clone, split into git history and checked-out files."""

    repo: str
    git_bytes: int
    files_bytes: int

    @property
    def total(self) -> int:
        return self.git_bytes + self.files_bytes


def human(n: float) -> str:
    """Format a byte count with binary units, e.g. 1536 -> '1.5 KB'."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def long_path(p: Path) -> Path:
    """On Windows, prefix with \\\\?\\ so paths over 260 characters can still be listed and stat'ed.

    https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation
    """
    return Path("\\\\?\\" + str(p.resolve())) if sys.platform == "win32" else p


def check_disk_space(total_kb: int, path: Path) -> tuple[int, int]:
    """Log free space vs. the estimated need for repos totalling total_kb on GitHub; return (free, needed)."""
    free = shutil.disk_usage(path).free  # https://docs.python.org/3/library/shutil.html#shutil.disk_usage
    needed = int(total_kb * 1024 * SPACE_FACTOR)
    log.info("GitHub reports %s packed; estimated need %s; free on disk %s", human(total_kb * 1024),
             human(needed), human(free))
    if free < needed:
        log.warning("Free space may not be enough for a full backup (free %s < estimated %s)",
                    human(free), human(needed))
    return free, needed


def scan(backup_dir: Path) -> list[FileEntry]:
    """List every file of every clone at backup_dir/<owner>/<name> with its size."""
    entries = []
    base = long_path(backup_dir)
    for repo_dir in sorted(d for d in base.glob("*/*") if d.is_dir()):  # one level per owner, one per repo
        label = f"{repo_dir.parent.name}/{repo_dir.name}"
        # os.walk visits every directory below repo_dir (https://docs.python.org/3/library/os.html#os.walk).
        for root, _dirs, files in os.walk(repo_dir):
            rel = Path(root).relative_to(repo_dir)
            in_git = rel.parts[:1] == (".git",)  # anything below .git/ is history/metadata
            for name in files:
                try:
                    size = os.lstat(os.path.join(root, name)).st_size  # lstat: don't follow symlinks
                except OSError as err:
                    log.warning("cannot stat %s/%s: %s", label, (rel / name).as_posix(), err)
                    continue
                entries.append(FileEntry(label, (rel / name).as_posix(), size, in_git))
    return entries


def repo_sizes(entries: list[FileEntry]) -> list[RepoSize]:
    """Sum file sizes per repo (history vs. working files), largest repo first."""
    git_b, files_b = Counter(), Counter()
    for e in entries:
        (git_b if e.in_git else files_b)[e.repo] += e.size
    repos = set(git_b) | set(files_b)
    return sorted((RepoSize(r, git_b[r], files_b[r]) for r in repos), key=lambda s: -s.total)


def largest_files(entries: list[FileEntry], top_n: int) -> list[tuple[int, str]]:
    """The top_n biggest working (checked-out) files across all repos as (size, 'owner/name/path')."""
    files = ((e.size, f"{e.repo}/{e.path}") for e in entries if not e.in_git)
    return sorted(files, reverse=True)[:top_n]


def extension_totals(entries: list[FileEntry]) -> list[tuple[str, int]]:
    """Bytes of working files per lowercase extension, largest first ('(none)' for no extension)."""
    totals = Counter()
    for e in entries:
        if not e.in_git:
            totals[os.path.splitext(e.path)[1].lower() or "(none)"] += e.size
    return totals.most_common()


def history_blobs(repo_dir: Path) -> list[tuple[int, str]]:
    """Every file version (blob) reachable from any branch/tag as (uncompressed size, path), largest first.

    Pipeline from https://stackoverflow.com/a/42544963 :
    git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectsize) %(rest)'
    rev-list lists each object once with the path it was first seen at
    (https://git-scm.com/docs/git-rev-list#Documentation/git-rev-list.txt---objects);
    cat-file adds its type and size (https://git-scm.com/docs/git-cat-file#_batch_output).
    """
    objects = run(["git", "-C", str(repo_dir), "rev-list", "--objects", "--all"])
    if objects.returncode != 0:  # e.g. an empty repo has no refs
        return []
    checked = run(["git", "-C", str(repo_dir), "cat-file",
                   "--batch-check=%(objecttype) %(objectsize) %(rest)"], stdin=objects.stdout)
    blobs = []
    for line in checked.stdout.splitlines():
        kind, size, path = (line.split(" ", 2) + [""])[:3]  # pad: %(rest) is empty for root trees
        if kind == "blob":
            blobs.append((int(size), path))
    return sorted(blobs, reverse=True)


def largest_history_blobs(backup_dir: Path, top_n: int) -> list[tuple[int, str]]:
    """The top_n biggest blobs across all clones as (size, 'owner/name/path')."""
    found = []
    for repo_dir in sorted(d for d in backup_dir.glob("*/*") if (d / ".git").is_dir()):
        label = f"{repo_dir.parent.name}/{repo_dir.name}"
        found += [(size, f"{label}/{path}") for size, path in history_blobs(repo_dir)[:top_n]]
    return sorted(found, reverse=True)[:top_n]


def table(headers: list[str], rows: list[list]) -> str:
    """Render a GitHub-flavoured markdown table (https://github.github.com/gfm/#tables-extension-)."""
    esc = lambda v: str(v).replace("|", "\\|")  # a literal | would split the cell
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(lines + ["| " + " | ".join(esc(v) for v in row) + " |" for row in rows])


def write_report(backup_dir: Path, report_dir: Path, top_n: int, results: list[Result]) -> Path:
    """Scan the backup, write report_dir/size-report.md, log a short summary, return the file path."""
    log.info("Scanning %s for the size report ...", backup_dir)
    entries = scan(backup_dir)
    sizes = repo_sizes(entries)
    git_total = sum(s.git_bytes for s in sizes)
    files_total = sum(s.files_bytes for s in sizes)
    status = Counter(r.status for r in results)
    problems = [r for r in results if r.status not in ("cloned", "updated")]
    top_files = largest_files(entries, top_n)
    top_blobs = largest_history_blobs(backup_dir, top_n)
    top_ext = extension_totals(entries)[:top_n]

    md = [
        f"# Backup size report\n\nGenerated {datetime.now():%Y-%m-%d %H:%M:%S} for `{backup_dir.name}/`.\n",
        "## Summary\n",
        table(["Measure", "Value"], [
            ["Repositories on disk", len(sizes)],
            ["Total size", human(git_total + files_total)],
            ["Git history (.git folders)", human(git_total)],
            ["Checked-out files", human(files_total)],
            ["This run", ", ".join(f"{k}: {v}" for k, v in sorted(status.items())) or "report only"],
            ["Free disk space now", human(shutil.disk_usage(backup_dir).free)],
        ]),
        "\n## Problems\n",
        table(["Repository", "Status", "Detail"], [[r.repo, r.status, r.detail] for r in problems])
        if problems else "None.",
        f"\n## Largest repositories (top {top_n})\n",
        table(["Repository", "Total", "Git history", "Files"],
              [[s.repo, human(s.total), human(s.git_bytes), human(s.files_bytes)] for s in sizes[:top_n]]),
        f"\n## Largest checked-out files (top {top_n})\n",
        table(["Size", "File"], [[human(s), p] for s, p in top_files]),
        f"\n## Largest files anywhere in git history (top {top_n})\n",
        "Uncompressed size of each file version; includes files deleted from the current branch.\n",
        table(["Size", "File (path where first seen)"], [[human(s), p] for s, p in top_blobs]),
        f"\n## Checked-out files by type (top {top_n})\n",
        table(["Extension", "Total"], [[e, human(b)] for e, b in top_ext]),
    ]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "size-report.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")

    log.info("Backup total %s (history %s + files %s) in %d repos", human(git_total + files_total),
             human(git_total), human(files_total), len(sizes))
    for s in sizes[:5]:
        log.info("  largest repo  %9s  %s", human(s.total), s.repo)
    for s, p in top_files[:5]:
        log.info("  largest file  %9s  %s", human(s), p)
    log.info("Report written to %s", path)
    return path
