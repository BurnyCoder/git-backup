"""Back up every GitHub repository of the configured owners, then report what uses the most space.

Usage:  uv run python backup.py            (settings come from .env, see .env.example)

Each phase is a function from the git_backup package; this file only wires them together:
  1. load settings      config.load_config
  2. start logging      log.setup_logging
  3. list repos         github.list_repos      (gh repo list, all owners)
  4. check disk space   report.check_disk_space
  5. clone / update     git.backup_all         (parallel git clone / git pull)
  6. size report        report.write_report    (reports/size-report.md)
"""

import logging
import sys
from pathlib import Path

from git_backup.config import load_config
from git_backup.git import backup_all
from git_backup.github import list_repos
from git_backup.log import setup_logging
from git_backup.report import check_disk_space, write_report

log = logging.getLogger("backup")


def main() -> int:
    """Run all phases; return a process exit code (1 if any repository failed)."""
    cfg = load_config(Path(__file__).resolve().parent)  # .env sits next to this file
    log_file = setup_logging(cfg.log_dir, cfg.log_level)
    log.info("Logging to %s", log_file)
    if not cfg.owners:
        log.error("GITHUB_OWNERS is empty; set it in .env (see .env.example)")
        return 1

    repos = [repo for owner in cfg.owners for repo in list_repos(owner, cfg.include_forks)]
    log.info("%d repositories to back up into %s", len(repos), cfg.backup_dir)

    cfg.backup_dir.mkdir(parents=True, exist_ok=True)  # disk_usage needs an existing path
    check_disk_space(sum(r.disk_kb for r in repos), cfg.backup_dir)

    results = backup_all(repos, cfg.backup_dir, cfg.parallel_jobs)
    write_report(cfg.backup_dir, cfg.report_dir, cfg.report_top_n, results)

    failed = [r for r in results if r.status == "failed"]
    log.info("Done: %d ok, %d partial, %d failed", sum(r.status in ("cloned", "updated") for r in results),
             sum(r.status == "partial" for r in results), len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())  # non-zero exit lets schedulers/CI notice failures
