"""One place to run external commands (gh, git) with logging and safe text decoding.

Global context: github.py and git.py both shell out; routing every call through run() keeps the
command line and its output in the log and avoids Windows cp1252 decode errors on UTF-8 output.
subprocess.run docs: https://docs.python.org/3/library/subprocess.html#subprocess.run
"""

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Never block on an interactive username/password prompt: fail instead and log the error
# (https://git-scm.com/docs/git#Documentation/git.txt-codeGITTERMINALPROMPTcode).
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def run(cmd: list[str], cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run cmd (optionally feeding `stdin` text), capture stdout/stderr as UTF-8, log at DEBUG, return it."""
    log.debug("$ %s", " ".join(cmd))
    # encoding="utf-8", errors="replace": git/gh print UTF-8; the Windows default (cp1252) can crash
    # on bytes like 0x90, so undecodable bytes become U+FFFD instead of raising.
    # input= pipes text to the child's stdin, replacing a shell `a | b` pipeline.
    proc = subprocess.run(cmd, cwd=cwd, env=ENV, input=stdin, capture_output=True,
                          encoding="utf-8", errors="replace")
    for line in (proc.stdout + proc.stderr).splitlines()[:50]:  # cap: rev-list etc. can be huge
        log.debug("  | %s", line)
    return proc
