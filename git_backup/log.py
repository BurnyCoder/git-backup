"""Timestamped logging to the terminal and to logs/backup_YYYYmmdd_HHMMSS.log at the same time.

Global context: every phase logs through the standard `logging` module, so configuring the root
logger once here makes all modules' messages appear live in the terminal and in the run's log file.
Based on the "Logging to multiple destinations" recipe:
https://docs.python.org/3/howto/logging-cookbook.html#logging-to-multiple-destinations
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# asctime gives the timestamp, threadName shows which parallel worker produced the line
# (https://docs.python.org/3/library/logging.html#logrecord-attributes).
FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(log_dir: Path, level: str = "INFO") -> Path:
    """Send log records to stdout and a new timestamped file in log_dir; return that file's path."""
    log_dir.mkdir(parents=True, exist_ok=True)  # first run: create logs/
    log_file = log_dir / f"backup_{datetime.now():%Y%m%d_%H%M%S}.log"  # one file per run
    # basicConfig attaches both handlers to the root logger in one call; force=True replaces
    # handlers from an earlier call (https://docs.python.org/3/library/logging.html#logging.basicConfig).
    logging.basicConfig(
        level=level.upper(),
        format=FORMAT,
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")],
        force=True,
    )
    return log_file
