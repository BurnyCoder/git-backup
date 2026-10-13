"""Load settings from .env (via python-dotenv) into a typed, read-only Config.

Global context: backup.py calls load_config() first; every later phase receives values from it
instead of reading os.environ itself. Template with all keys: .env.example.
python-dotenv usage: https://github.com/theskumar/python-dotenv#getting-started
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """All runtime settings; paths are absolute (resolved against the project root)."""

    owners: list[str]  # GitHub users/orgs to back up
    backup_dir: Path  # receives <owner>/<name> clones
    log_dir: Path
    report_dir: Path
    include_forks: bool
    parallel_jobs: int  # concurrent clone/update workers
    report_top_n: int  # rows in each "largest" table
    log_level: str
    skip_lfs_repos: set[str]  # "owner/name" repos whose Git LFS files are not downloaded


def load_config(root: Path) -> Config:
    """Read root/.env (if present) plus the process environment and build a Config."""
    # Existing environment variables win over .env (override=False is the default),
    # so a one-off `INCLUDE_FORKS=false uv run ...` works
    # (https://github.com/theskumar/python-dotenv#load-configuration-without-altering-the-environment).
    load_dotenv(root / ".env")
    env = os.environ.get  # short alias: env("KEY", "default")
    # "a, b ," -> ["a", "b"]: trim spaces and drop empty items from a comma list
    items = lambda key: [v.strip() for v in env(key, "").split(",") if v.strip()]
    return Config(
        owners=items("GITHUB_OWNERS"),
        backup_dir=root / env("BACKUP_DIR", "repos"),  # `/` joins; an absolute value replaces root
        log_dir=root / env("LOG_DIR", "logs"),
        report_dir=root / env("REPORT_DIR", "reports"),
        include_forks=env("INCLUDE_FORKS", "true").strip().lower() in ("1", "true", "yes"),
        parallel_jobs=int(env("PARALLEL_JOBS", "4")),
        report_top_n=int(env("REPORT_TOP_N", "40")),
        log_level=env("LOG_LEVEL", "INFO"),
        skip_lfs_repos=set(items("SKIP_LFS_REPOS")),
    )
