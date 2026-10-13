# AGENTS.md

Notes for coding agents and contributors. The user-facing description is in [README.md](README.md); design rationale is in [docs/architecture.md](docs/architecture.md).

## Layout and responsibilities

- `backup.py`: the only entry point. It calls the phases in order and contains no logic of its own.
- `git_backup/shell.py`: **all** external commands go through `run()`. Don't call `subprocess` anywhere else.
- `git_backup/github.py`: the `Repo` dataclass and `gh repo list`.
- `git_backup/git.py`: `GIT_FLAGS`, `clone_or_update()`, `backup_all()` and the `Result` dataclass.
- `git_backup/report.py`: the disk check, scanning and the markdown report.
- `git_backup/config.py`: the only place that reads environment variables. Add new settings here and to `.env.example`.

## Conventions

- Each file, function and non-obvious line has a comment that explains it locally and in context, with a link to its source (git, gh or Python docs).
- Log through `logging.getLogger(__name__)`, never `print`. Use INFO for progress, WARNING for problems with a single repo, and DEBUG for command output.
- Test-driven: add or adjust a test in `tests/` first. Tests use local bare repos (`tests/conftest.py::remote`) and never touch the network or GitHub.

## Commands

```bash
uv sync
```
```bash
uv run pytest -q
```
```bash
uv run python backup.py
```

## Safety rules

- Never commit `repos/`, `logs/` or `.env` (they are gitignored). Nested clones would otherwise become gitlinks.
- Don't change the user's global git config. Pass settings with `git -c` in `GIT_FLAGS`.
- The backup drive is nearly full. Don't make a second full copy for testing: re-running updates the clones in place, and tests use temp dirs.
