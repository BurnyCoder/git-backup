"""git_backup: clone/update every GitHub repo of the configured owners and report disk usage.

Phases (called in order by backup.py):
  config.load_config -> log.setup_logging -> github.list_repos -> report.check_disk_space
  -> git.backup_all -> report.write_report
"""
