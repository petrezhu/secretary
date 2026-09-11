"""Health checker — migrated from health_monitor.py."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from secretary.config import MonitorConfig
from secretary.monitor import CheckResult

logger = logging.getLogger(__name__)

# Severity ordering for status comparison
_SEVERITY = {"ok": 0, "warning": 1, "critical": 2}

# Default path for the TickTick/Dida365 OAuth token file
_DATA_DIR = os.environ.get("SECRETARY_DATA_DIR", "")
_DEFAULT_TOKEN_PATH = Path(
    os.path.join(_DATA_DIR, "dida365_token.json") if _DATA_DIR else "/tmp/dida365_token.json"
)


def _worst(current: str, candidate: str) -> str:
    """Return the more severe of two statuses."""
    if _SEVERITY.get(candidate, 0) > _SEVERITY.get(current, 0):
        return candidate
    return current


class HealthChecker:
    """Run health checks on goals/tasks system."""

    name = "health"

    def __init__(
        self,
        config: MonitorConfig,
        token_path: Path | str | None = None,
    ):
        self.config = config
        self._token_path = Path(token_path) if token_path else _DEFAULT_TOKEN_PATH

    async def check(self, repo: Any) -> CheckResult:
        """Run all health checks and return aggregate result."""
        issues: list[str] = []
        details: dict[str, Any] = {}
        worst_status = "ok"

        # Check 1: Weekly goals exist
        try:
            weekly = await repo.get_weekly_goals()
            if not weekly:
                issues.append("本周无周目标")
                details["weekly_goals_count"] = 0
                worst_status = _worst(worst_status, "warning")
            else:
                details["weekly_goals_count"] = len(weekly)
        except Exception as e:
            issues.append(f"周目标查询失败: {e}")
            worst_status = _worst(worst_status, "critical")

        # Check 2: Stuck goals (>14 days)
        try:
            stuck = await repo.get_stuck_goals(14)
            if stuck:
                issues.append(f"{len(stuck)}个目标停滞超14天")
                details["stuck_goals"] = [g.title for g in stuck]
                worst_status = _worst(worst_status, "warning")
            else:
                details["stuck_goals"] = []
        except Exception as e:
            issues.append(f"停滞目标查询失败: {e}")
            worst_status = _worst(worst_status, "critical")

        # Check 3: Zombie tasks (>7 days no heartbeat)
        try:
            zombies = await repo.get_zombie_tasks(7)
            if zombies:
                issues.append(f"{len(zombies)}个僵尸任务")
                details["zombie_tasks"] = [t.request_text for t in zombies]
                worst_status = _worst(worst_status, "warning")
            else:
                details["zombie_tasks"] = []
        except Exception as e:
            issues.append(f"僵尸任务查询失败: {e}")
            worst_status = _worst(worst_status, "critical")

        # Check 4: Database size
        try:
            sizes = await repo.get_db_size()
            total = sum(sizes.values())
            details["db_sizes_mb"] = sizes
            details["db_total_mb"] = round(total, 3)
            if total > 50:
                issues.append(f"数据库过大: {total:.1f}MB")
                worst_status = _worst(worst_status, "warning")
        except Exception as e:
            issues.append(f"数据库大小查询失败: {e}")
            worst_status = _worst(worst_status, "critical")

        # Check 5: Token expiry check (180-day Dida365 OAuth token, warn at 2 days)
        try:
            token_info = self._check_token_expiry()
            details["token_expiry"] = token_info
            if token_info["status"] != "ok":
                issues.append(f"Token: {token_info['message']}")
                worst_status = _worst(worst_status, token_info["severity"])
        except Exception as e:
            issues.append(f"Token过期检查失败: {e}")
            worst_status = _worst(worst_status, "critical")

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
            message="所有健康检查通过",
            details=details,
        )

    def _check_token_expiry(self) -> dict[str, Any]:
        """Check if the Dida365 OAuth token has expired or will expire soon.

        Token validity: 180 days. Warn at <= 2 days remaining.
        """
        if not self._token_path.exists():
            return {
                "status": "missing",
                "severity": "warning",
                "message": "dida365_token.json 不存在",
            }

        try:
            with open(self._token_path, encoding="utf-8") as f:
                data = json.load(f)

            expires_at = data.get("expires_at")
            if not expires_at:
                return {"status": "unknown", "severity": "ok", "message": "无过期时间字段"}

            if isinstance(expires_at, str):
                exp_dt = datetime.fromisoformat(expires_at)
            elif isinstance(expires_at, (int, float)):
                exp_dt = datetime.fromtimestamp(expires_at)
            else:
                return {"status": "unknown", "severity": "ok", "message": "过期时间格式未知"}

            now = datetime.now(tz=exp_dt.tzinfo) if exp_dt.tzinfo else datetime.now()
            remaining = exp_dt - now
            days_left = remaining.total_seconds() / 86400

            if days_left <= 0:
                return {
                    "status": "expired",
                    "severity": "critical",
                    "message": f"Token已过期{-days_left:.0f}天",
                    "expires_at": exp_dt.isoformat(),
                }
            elif days_left <= 2:
                return {
                    "status": "expiring_soon",
                    "severity": "warning",
                    "message": f"Token将在{days_left:.1f}天后过期（{exp_dt.strftime('%m-%d')}）",
                    "expires_at": exp_dt.isoformat(),
                }
            else:
                return {
                    "status": "ok",
                    "expires_at": exp_dt.isoformat(),
                    "days_remaining": round(days_left),
                }
        except (json.JSONDecodeError, OSError) as e:
            return {
                "status": "error",
                "severity": "warning",
                "message": f"Token文件读取失败: {e}",
            }
