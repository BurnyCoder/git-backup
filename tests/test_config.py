"""Tests for git_backup.config: .env values become a typed Config with sensible defaults."""

from git_backup import config


def test_defaults_when_env_missing(tmp_path, monkeypatch):
    for key in ("GITHUB_OWNERS", "BACKUP_DIR", "INCLUDE_FORKS", "PARALLEL_JOBS", "REPORT_TOP_N", "SKIP_LFS_REPOS"):
        monkeypatch.delenv(key, raising=False)  # isolate from the developer's real .env values
    cfg = config.load_config(tmp_path)
    assert cfg.owners == []
    assert cfg.backup_dir == tmp_path / "repos"
    assert cfg.include_forks is True and cfg.parallel_jobs == 4 and cfg.report_top_n == 40
    assert cfg.skip_lfs_repos == set()


def test_env_values_are_parsed(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OWNERS", " alice, acme-org ,")
    monkeypatch.setenv("INCLUDE_FORKS", "false")
    monkeypatch.setenv("PARALLEL_JOBS", "8")
    monkeypatch.setenv("SKIP_LFS_REPOS", "alice/data, acme-org/data")
    cfg = config.load_config(tmp_path)
    assert cfg.owners == ["alice", "acme-org"]  # trimmed, empty items dropped
    assert cfg.skip_lfs_repos == {"alice/data", "acme-org/data"}
    assert cfg.include_forks is False and cfg.parallel_jobs == 8
