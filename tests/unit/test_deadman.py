"""Unit tests for DeadmanChecker."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from secretary.config import MonitorConfig
from secretary.monitor.deadman import DeadmanChecker


@pytest.fixture
def checker() -> DeadmanChecker:
    """DeadmanChecker with default config."""
    return DeadmanChecker(config=MonitorConfig())


class TestMissingLogFiles:
    """Missing log files should NOT trigger warnings."""

    async def test_no_log_files_returns_ok(self, checker: DeadmanChecker, tmp_path: Path):
        """When no log files exist, checker should return ok (not warning)."""
        # Point all jobs to non-existent paths
        for job in checker._jobs:
            job["log_path"] = str(tmp_path / "nonexistent.log")

        result = await checker.check(repo=None)
        assert result.status == "ok"
        assert "关键任务运行正常" in result.message

    async def test_missing_file_shows_no_log_status(self, checker: DeadmanChecker, tmp_path: Path):
        """Missing log files should show status 'no_log' in details."""
        for job in checker._jobs:
            job["log_path"] = str(tmp_path / "nonexistent.log")

        result = await checker.check(repo=None)
        for job_name in ["dida365_sync", "health_monitor", "gtd_review"]:
            assert result.details[job_name]["status"] == "no_log"


class TestStaleLogFiles:
    """Existing log files that are too old should trigger warnings."""

    async def test_stale_log_warns(self, checker: DeadmanChecker, tmp_path: Path):
        """A log file older than max_age should trigger a warning."""
        stale_log = tmp_path / "stale.log"
        stale_log.write_text("old content")
        # Set mtime to 2 days ago
        old_time = (datetime.now() - timedelta(days=2)).timestamp()
        import os
        os.utime(stale_log, (old_time, old_time))

        # Only configure one job pointing to the stale log
        checker._jobs = [
            {
                "name": "test_job",
                "log_path": str(stale_log),
                "max_age_minutes": 60,
            }
        ]

        result = await checker.check(repo=None)
        assert result.status == "warning"
        assert "test_job" in result.message
        assert result.details["test_job"]["status"] == "stale"

    async def test_fresh_log_ok(self, checker: DeadmanChecker, tmp_path: Path):
        """A recently modified log file should be ok."""
        fresh_log = tmp_path / "fresh.log"
        fresh_log.write_text("recent content")

        checker._jobs = [
            {
                "name": "test_job",
                "log_path": str(fresh_log),
                "max_age_minutes": 60,
            }
        ]

        result = await checker.check(repo=None)
        assert result.status == "ok"
        assert result.details["test_job"]["status"] == "ok"


class TestMixedScenario:
    """Mix of missing and stale log files."""

    async def test_only_stale_triggers_warning(self, checker: DeadmanChecker, tmp_path: Path):
        """Missing files are ok, but stale files should still warn."""
        stale_log = tmp_path / "stale.log"
        stale_log.write_text("old content")
        old_time = (datetime.now() - timedelta(days=2)).timestamp()
        import os
        os.utime(stale_log, (old_time, old_time))

        checker._jobs = [
            {
                "name": "missing_job",
                "log_path": str(tmp_path / "nonexistent.log"),
                "max_age_minutes": 60,
            },
            {
                "name": "stale_job",
                "log_path": str(stale_log),
                "max_age_minutes": 60,
            },
        ]

        result = await checker.check(repo=None)
        assert result.status == "warning"
        assert "stale_job" in result.message
        assert result.details["missing_job"]["status"] == "no_log"
        assert result.details["stale_job"]["status"] == "stale"
