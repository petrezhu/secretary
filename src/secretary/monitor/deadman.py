"""Dead-man switch — check if critical cron jobs ran within expected window."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from secretary.config import MonitorConfig
from secretary.monitor import CheckResult

logger = logging.getLogger(__name__)

# Default log paths and freshness windows per job
_LOGS_DIR = os.environ.get("SECRETARY_LOGS_DIR", "")
_DEFAULT_JOBS = [
    {
        "name": "dida365_sync",
        "log_path": os.path.join(_LOGS_DIR, "dida365_sync.log") if _LOGS_DIR else "",
        "max_age_minutes": 45,
    },
    {
        "name": "health_monitor",
        "log_path": os.path.join(_LOGS_DIR, "health_monitor.log") if _LOGS_DIR else "",
        "max_age_minutes": 26 * 60,  # 26 hours
    },
    {
        "name": "gtd_review",
        "log_path": os.path.join(_LOGS_DIR, "gtd_review.log") if _LOGS_DIR else "",
        "max_age_minutes": 26 * 60,  # 26 hours
    },
]


class DeadmanChecker:
    """Check if critical jobs have run in their expected window.

    For each monitored job, reads the log file's mtime. If the file is
    older than the configured window, the job is considered stale.
    """

    name = "deadman"

    def __init__(self, config: MonitorConfig):
        self.config = config
        self._jobs = list(_DEFAULT_JOBS)

    async def check(self, repo: Any) -> CheckResult:
        """Check all critical job windows."""
        now = datetime.now()
        issues: list[str] = []
        details: dict[str, Any] = {}
        worst_status = "ok"

        _severity = {"ok": 0, "warning": 1, "critical": 2}

        def _worse(current: str, candidate: str) -> str:
            if _severity.get(candidate, 0) > _severity.get(current, 0):
                return candidate
            return current

        for job in self._jobs:
            name = job["name"]
            log_path = Path(job["log_path"])
            max_age = timedelta(minutes=job["max_age_minutes"])

            if not log_path.exists():
                details[name] = {"status": "no_log", "path": str(log_path)}
                continue

            mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
            age = now - mtime

            if age > max_age:
                age_hours = age.total_seconds() / 3600
                issues.append(
                    f"{name}: 已{age_hours:.1f}小时未更新"
                )
                details[name] = {
                    "status": "stale",
                    "last_modified": mtime.isoformat(),
                    "age_hours": round(age_hours, 1),
                }
                worst_status = _worse(worst_status, "warning")
            else:
                details[name] = {
                    "status": "ok",
                    "last_modified": mtime.isoformat(),
                }

        if issues:
            return CheckResult(
                name=self.name,
                status=worst_status,
                message="; ".join(issues),
                details=details,
            )

        return CheckResult(
            name=self.name,
            status="ok",
            message="所有关键任务运行正常",
            details=details,
        )
