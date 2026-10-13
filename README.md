# git-backup

Makes a local backup of every GitHub repository owned by the users or organisations you list, and tells you how much space the backup takes and which files take the most.

## Methodology

1. **Ask GitHub what exists.** `gh repo list <owner> --json …` returns every repository of each owner, private ones included. It uses the token the GitHub CLI already stores.
2. **Clone each repo, or update it if it's already there.** A normal `git clone` copies the full history of every branch and checks out the default branch, so each backup is complete and you can browse it. On later runs, `git pull --ff-only --prune` brings each existing clone up to date in place; it never makes a second copy.
3. **Measure the result.** The tool walks the backup folder and splits each repo into git history (`.git/`) and checked-out files. It also lists the biggest file versions in history with `git rev-list --objects --all | git cat-file --batch-check`, and writes everything to [`reports/size-report.md`](reports/size-report.md).

## Quick start

```bash
gh auth login
```
```bash
git clone <url-of-this-repo> git-backup && cd git-backup
```
```bash
cp .env.example .env
```
Edit `GITHUB_OWNERS` in `.env`, then:
```bash
uv sync
```
```bash
uv run python backup.py
```

Running it again later updates the existing clones in place. Only new commits are downloaded.

## Features

- Backs up all repositories of several users and orgs: public and private, forks optional, empty repos included.
- Clones normally, so each backup has full history, all branches as `origin/*`, and browsable files. Git LFS files on the checked-out branch are downloaded automatically.
- Can skip large Git LFS data per repo (`SKIP_LFS_REPOS`): those repos keep small pointer files, and the data stays on GitHub. An LFS file that is missing on GitHub's server (404) also stays a pointer instead of breaking the checkout.
- Updates incrementally on re-runs, fast-forward only, and removes branches that were deleted on GitHub.
- Clones several repos at once (`PARALLEL_JOBS`).
- Checks free disk space before cloning and warns if it may run out.
- Handles file names Windows can't store (e.g. `file.md:Zone.Identifier`). Only those files are left out of the checked-out folder (they stay in history), instead of the whole checkout failing.
- Keeps going when a repo fails. Failed and partial repos are listed in the log and the report.
- The size report covers:
  - total size, split into git history and checked-out files
  - largest repositories
  - largest checked-out files
  - largest file versions anywhere in history, including files that were later deleted
  - space used by each file extension
- Every run writes a timestamped log to `logs/` and shows the same lines in the terminal.
- Leaves your git config alone: the credential and long-path settings are passed with `git -c` on each command.

## Technologies

| What | Used for |
|---|---|
| Python ≥ 3.14, standard library (`subprocess`, `concurrent.futures`, `logging`, `shutil`, `os.walk`) | orchestration, parallelism, logging, measuring |
| [uv](https://docs.astral.sh/uv/) | virtual environment and dependencies |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | `.env` configuration |
| [GitHub CLI `gh`](https://cli.github.com/manual/) | listing repos (`gh repo list`) and supplying credentials (`gh auth git-credential`) |
| [git](https://git-scm.com/docs) + [git-lfs](https://git-lfs.com/) | `clone`, `pull`, `rev-list`, `cat-file`; LFS files |
| [pytest](https://docs.pytest.org/) | tests against local throwaway git repos |

## Requirements and setup

1. Install **git** (with git-lfs; Git for Windows includes it), the **GitHub CLI** and **uv**.
2. Log in to GitHub once: `gh auth login`. The token needs the `repo` scope for private repos and `read:org` for organisation repos. Check with `gh auth status`.
3. Copy `.env.example` to `.env` and set:

| Key | Default | Meaning |
|---|---|---|
| `GITHUB_OWNERS` | none (required) | Comma-separated users/orgs, e.g. `your-user,your-org` |
| `BACKUP_DIR` | `repos` | Where clones go: `<BACKUP_DIR>/<owner>/<name>` |
| `LOG_DIR` | `logs` | Timestamped run logs |
| `REPORT_DIR` | `reports` | Size report output |
| `INCLUDE_FORKS` | `true` | Also back up forks |
| `PARALLEL_JOBS` | `4` | Repos cloned or updated at the same time |
| `REPORT_TOP_N` | `40` | Rows in each "largest" table |
| `SKIP_LFS_REPOS` | none | Comma-separated `owner/name` repos whose Git LFS files are not downloaded (pointers only) |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs every git command and its output |

4. Run `uv sync` to create `.venv` and install the dependencies.

## Usage

```bash
uv run python backup.py
```
```bash
uv run python backup.py --local
```
```bash
uv run pytest
```

`--local` works only on what's already on disk, without contacting GitHub. It repairs clones whose checkout failed, removes downloaded LFS data from `SKIP_LFS_REPOS`, and rewrites the report. The exit code is `1` if any repo failed, so the command can be scheduled.

**Disk space:**
- A full backup takes roughly GitHub's reported size plus about the same again for checked-out files. The tool warns when free space is below 2.5× GitHub's reported size. Re-runs only add new commits.
- **Git LFS data is not included in GitHub's reported size**, and each LFS file is stored twice locally: once in `.git/lfs` and once checked out. For a large LFS dataset, list the repo in `SKIP_LFS_REPOS`. Example: `big-dataset-repo` holds 2.4 GB of LFS data, which took 4.9 GB per clone.

**Restore a repo:**
- Copy or open its folder, or run `git clone repos/<owner>/<name> <new-place>`.
- To recreate it on GitHub: `git push --mirror <new-remote-url>` from the backup folder.
  - Branches other than the default one are stored as `origin/<branch>`. Run `git checkout <branch>` to get a local copy before pushing.

## What happens when you run it

```
backup.py
  ├─ config.load_config     .env → Config (owners, folders, forks on/off, jobs)
  ├─ log.setup_logging      logs/backup_YYYYmmdd_HHMMSS.log + terminal
  ├─ github.list_repos      gh repo list <owner> --json …   (once per owner)
  ├─ report.check_disk_space  free space vs. 2.5 × GitHub-reported size
  ├─ git.backup_all         N workers: git clone  (new) | maintain + git pull --ff-only --prune (existing)
  │                          → repos/<owner>/<name>/  (+ one log line per repo, live)
  │   (--local: git.maintain_all instead of the three phases above — offline repair / LFS removal)
  └─ report.write_report    walk repos/ + git rev-list|cat-file → reports/size-report.md
```

1. Settings are read from `.env`.
2. A log file is opened.
3. Each owner's repos are listed from GitHub.
4. Free space is checked.
5. Repos are cloned or updated in parallel. Each finished repo logs `[i/N] status owner/name (seconds) last git line`.
6. The backup folder is measured and the report is written. The log ends with the largest repos and files, then `Done: X ok, Y partial, Z failed`.

## Architecture

```mermaid
flowchart LR
    env[.env] --> cfg[config.py<br/>load_config]
    cfg --> main[backup.py<br/>wrapper]
    main --> logm[log.py<br/>setup_logging]
    main --> gh[github.py<br/>list_repos]
    gh -- gh repo list --> GH[(GitHub API)]
    main --> rep1[report.py<br/>check_disk_space]
    main --> git[git.py<br/>backup_all / clone_or_update]
    git -- git clone / pull<br/>creds: gh auth git-credential --> GH2[(github.com)]
    git --> repos[(repos/owner/name)]
    main -- --local --> mnt[git.py<br/>maintain_all]
    mnt --> repos
    main --> rep2[report.py<br/>write_report]
    repos --> rep2
    rep2 --> md[reports/size-report.md]
    gh & git & rep2 --> sh[shell.py<br/>run]
    logm --> logs[(logs/*.log + terminal)]
```

## Repository layout

```
backup.py            entry point: wires the phases together
git_backup/          config.py, log.py, shell.py, github.py, git.py, report.py
tests/               pytest suite (local bare repos stand in for GitHub)
docs/architecture.md design decisions, Windows notes, limitations
reports/             generated size report (committed)
repos/  logs/  .env  local only, gitignored
```

## More

- [docs/architecture.md](docs/architecture.md): how each phase works, design choices and limitations (what isn't backed up).
- [AGENTS.md](AGENTS.md): notes for coding agents and contributors.
- [reports/size-report.md](reports/size-report.md): the latest size report.

## Sources

- gh repo list: https://cli.github.com/manual/gh_repo_list
- gh auth setup-git: https://cli.github.com/manual/gh_auth_setup-git
- git clone: https://git-scm.com/docs/git-clone
- git pull: https://git-scm.com/docs/git-pull
- gitcredentials (empty helper resets the list): https://git-scm.com/docs/gitcredentials
- Largest blobs in history (`rev-list | cat-file`): https://stackoverflow.com/a/42544963 and https://git-scm.com/docs/git-cat-file
- Similar tools: [ghbu](https://github.com/kylemcc/ghbu), [ghcloneall](https://pypi.org/project/ghcloneall/), [Addy Osmani's guide](https://addyosmani.com/blog/backing-up-a-github-account/)
