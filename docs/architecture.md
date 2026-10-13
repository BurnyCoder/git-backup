# Architecture and design notes

The overview, configuration and usage are in the [README](../README.md). This page explains *why* each phase works the way it does.

## Phases

| Phase | Module | Key call | Notes |
|---|---|---|---|
| Settings | `git_backup/config.py` | `load_dotenv(root/.env)` | Variables already set in the environment win over `.env`, so `INCLUDE_FORKS=false uv run python backup.py` works for one run. |
| Logging | `git_backup/log.py` | `logging.basicConfig(handlers=[StreamHandler, FileHandler])` | One file per run, `logs/backup_YYYYmmdd_HHMMSS.log`. Each line shows the thread name (`git_0`…) so parallel output can be told apart. |
| Commands | `git_backup/shell.py` | `subprocess.run(..., encoding="utf-8", errors="replace")` | Every `gh`/`git` call goes through here and is logged at DEBUG. UTF-8 with `replace` avoids Windows cp1252 decode crashes on file names. `GIT_TERMINAL_PROMPT=0` makes git fail instead of waiting for a password. |
| List | `git_backup/github.py` | `gh repo list OWNER --limit 5000 --json nameWithOwner,url,isFork,isEmpty,diskUsage` | `diskUsage` is GitHub's packed size in KB. It is used only for the disk-space estimate. |
| Disk check | `git_backup/report.py` | `shutil.disk_usage` | Warns, but doesn't stop, when free space is below `SPACE_FACTOR` (2.5) × the GitHub-reported total. |
| Clone/update | `git_backup/git.py` | `git -c … clone URL DEST` / `git -c … -C DEST pull --ff-only --prune` | Runs in a `ThreadPoolExecutor`; results are logged as they finish (`as_completed`). |
| Report | `git_backup/report.py` | `os.walk` + `git rev-list --objects --all \| git cat-file --batch-check` | Writes `reports/size-report.md`. |

## Design decisions

- **Plain `git clone`, not `gh repo clone`.** For a fork, `gh repo clone` adds the parent repo as an `upstream` remote and makes it the default remote ([manual](https://cli.github.com/manual/gh_repo_clone)). A plain clone keeps each backup an exact copy of *your* repo with one `origin` remote.
- **Credentials from gh, without touching git config.** Each git command gets `-c credential.helper= -c "credential.helper=!gh auth git-credential"`.
  - The empty value resets the list of credential helpers ([gitcredentials](https://git-scm.com/docs/gitcredentials)), so Git Credential Manager pop-ups are skipped.
  - A value starting with `!` is run as a shell command, here gh's credential helper.
  - `git -c` settings are passed to child processes, so git-lfs authenticates the same way.
- **`core.longpaths=true`** lets Git for Windows check out paths longer than 260 characters ([git-config](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corelongPaths)). The report scanner uses the `\\?\` path prefix for the same reason ([Microsoft](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)).
- **File names Windows can't store** (`:` `"` `<` `>` `|` `?` `*`, for example `SKILL.md:Zone.Identifier` files copied from WSL, or wiki titles like `Foo:_Bar.mediawiki`):
  - Git rejects these paths and abandons the *whole* checkout, so the folder ends up with history but no files.
  - `git.skip_invalid_paths` repairs this in three steps:
    1. Set `core.protectNTFS=false` *in that repo only*, so the index can list those names.
    2. Run `git sparse-checkout set --no-cone` with `git.WINDOWS_SAFE` (everything except names with those characters), so they are never written to disk.
    3. Run `git reset --hard HEAD` to rebuild the checkout.
  - The skipped files remain in git history; only the working folder lacks them.
  - Later pulls in that repo keep working.
  - Fix based on [Atlassian's KB article](https://support.atlassian.com/bitbucket-cloud/kb/invalid-path-fatal-unable-to-checkout-working-tree-while-cloning-the-repository-in-windows/) and the [git-sparse-checkout docs](https://git-scm.com/docs/git-sparse-checkout).
- **Statuses:**
  - `cloned` / `updated`: success.
  - `partial`: full history, plus all files except those with Windows-invalid names. The log shows how many were skipped.
  - `failed`: nothing usable. The download failed, or `pull --ff-only` refused because the branch was rewritten on GitHub (force-push).
- **Fast-forward only.** A backup should never create merge commits. If a branch was force-pushed, the run reports `failed` and you decide what to do. `pull` has already fetched all objects at that point, so nothing is lost.
- **Empty repositories** are cloned so they are recorded. Updating them runs only `git fetch --prune`, because there is no branch to merge yet.
- **Sizes:**
  - "On disk" sizes are `st_size` sums: `.git/` is history and metadata, everything else is checked-out files.
  - "History" sizes are the *uncompressed* size of each file version (`%(objectsize)`). On disk, git stores them compressed and delta-encoded, so they take less.

## Limitations

- Only git data is backed up. Issues, pull-request discussions, wikis (`<repo>.wiki.git`), releases and Actions artifacts are not.
- Git LFS: only the LFS files of the checked-out branch are downloaded (by the LFS filter during checkout). Other branches keep LFS pointer files. Run `git lfs fetch --all` inside a clone to get everything.
- Non-default branches exist as `origin/<name>` remote-tracking branches, not as local branches.
- Repos you collaborate on but don't own are not listed. `gh repo list OWNER` returns only the owner's repos.
