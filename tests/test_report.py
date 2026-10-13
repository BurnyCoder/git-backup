"""Tests for git_backup.report: on-disk sizes, largest files, history blobs and the markdown report."""

from conftest import commit_file, git
from git_backup import report
from git_backup.git import Result


def backup_with_one_repo(remote, tmp_path):
    """Clone the fixture remote into backup/alice/proj after adding a big file and a deleted file."""
    bare, work = remote
    commit_file(work, "data/big.bin", 5000, "big")
    commit_file(work, "gone.csv", 3000, "temp")
    git("rm", "-q", "gone.csv", cwd=work)  # deleted from HEAD but still in history
    git("commit", "-q", "-m", "remove", cwd=work)
    git("push", "-q", "origin", "HEAD", cwd=work)
    backup = tmp_path / "backup"
    git("clone", "-q", str(bare), str(backup / "alice" / "proj"), cwd=tmp_path)
    return backup


def test_scan_splits_git_and_working_files(remote, tmp_path):
    backup = backup_with_one_repo(remote, tmp_path)
    sizes = report.repo_sizes(report.scan(backup))
    assert len(sizes) == 1
    row = sizes[0]
    assert row.repo == "alice/proj"
    assert row.files_bytes == 5000 + 10  # big.bin + README.md; gone.csv is not checked out
    assert row.git_bytes > 0


def test_largest_files_sorted_desc(remote, tmp_path):
    backup = backup_with_one_repo(remote, tmp_path)
    top = report.largest_files(report.scan(backup), top_n=1)
    assert top == [(5000, "alice/proj/data/big.bin")]


def test_extension_totals(remote, tmp_path):
    backup = backup_with_one_repo(remote, tmp_path)
    totals = dict(report.extension_totals(report.scan(backup)))
    assert totals[".bin"] == 5000 and totals[".md"] == 10


def test_history_blobs_include_deleted_files(remote, tmp_path):
    backup = backup_with_one_repo(remote, tmp_path)
    blobs = report.history_blobs(backup / "alice" / "proj")
    assert (3000, "gone.csv") in blobs  # found even though it is no longer in HEAD
    assert blobs[0] == (5000, "data/big.bin")  # sorted largest first


def test_write_report_mentions_totals_and_failures(remote, tmp_path):
    backup = backup_with_one_repo(remote, tmp_path)
    failed = [Result("alice/broken", "failed", "fatal: repository not found")]
    path = report.write_report(backup, tmp_path / "reports", top_n=5, results=failed)
    text = path.read_text(encoding="utf-8")
    assert "alice/proj" in text and "data/big.bin" in text and "gone.csv" in text
    assert "alice/broken" in text and "repository not found" in text


def test_check_disk_space_returns_free_and_needed(tmp_path):
    free, needed = report.check_disk_space(total_kb=1024, path=tmp_path)
    assert free > 0 and needed == int(1024 * 1024 * report.SPACE_FACTOR)
