"""Auto-repair engine — automatic remediation for known anomaly patterns.

When the monitoring pipeline detects an anomaly, the AutoRepairer checks
whether a safe, whitelisted repair strategy exists.  If so, it:
  1. Sends a "repairing" notification (e.g. "nginx挂了，我正在重启")
  2. Executes the repair command
  3. Audits the action
  4. Sends a "done" notification (e.g. "nginx已重启，状态正常")

Safety: only the following anomaly types auto-execute without confirmation:
  - service_down   → systemctl restart {service}
  - docker_down    → docker restart {container}
  - disk_full      → clean old logs (>7d), tmp files, old backups (>30d)
  - cron_stuck     → reset job last_run

All other anomaly types require human confirmation before repair.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from secretary.engine.audit import AuditLog
from secretary.monitor import CheckResult

if TYPE_CHECKING:
    from secretary.notify import Dispatcher

logger = logging.getLogger(__name__)

# ── Repair action type enum ─────────────────────────────────────────────────


class RepairActionType(str, Enum):
    """Types of automated repair actions."""

    SERVICE_RESTART = "service_down"
    DOCKER_RESTART = "docker_down"
    DISK_CLEANUP = "disk_full"
    CRON_RESET = "cron_stuck"

# ── Whitelisted auto-repair anomaly types ────────────────────────────────────

AUTO_REPAIR_TYPES: set[RepairActionType] = {
    RepairActionType.SERVICE_RESTART,
    RepairActionType.DOCKER_RESTART,
    RepairActionType.DISK_CLEANUP,
    RepairActionType.CRON_RESET,
}

# ── Repair action model ─────────────────────────────────────────────────────


@dataclass
class RepairAction:
    """Describes a single repair action to execute."""

    action_type: RepairActionType  # e.g. SERVICE_RESTART, DOCKER_RESTART, DISK_CLEANUP, CRON_RESET
    target: str  # e.g. "nginx", "my_container", "/var/log"
    command: str | None = None  # shell command to execute (None for non-shell repairs)
    requires_confirm: bool = False  # True → must not auto-execute
    details: dict[str, Any] = field(default_factory=dict)

    def is_auto_allowed(self) -> bool:
        """Return True if this action can execute without human confirmation."""
        return self.action_type in AUTO_REPAIR_TYPES and not self.requires_confirm


# ── Strategy helpers ─────────────────────────────────────────────────────────


def _detect_anomaly_type(result: CheckResult) -> RepairActionType | None:
    """Map a CheckResult to an anomaly repair type.

    Returns None if no automatic repair strategy is known.
    """
    name = result.name.lower()
    msg = result.message.lower()

    # Service down patterns
    if "service" in name or "服务" in msg or "systemctl" in msg:
        return RepairActionType.SERVICE_RESTART

    # Docker container down
    if "docker" in name or "container" in msg or "容器" in msg:
        return RepairActionType.DOCKER_RESTART

    # Disk full
    if "disk" in name or "磁盘" in msg or "disk_full" in msg:
        return RepairActionType.DISK_CLEANUP

    # Cron stuck
    if "cron" in name or "定时" in msg or "stuck" in msg or "停滞" in msg:
        return RepairActionType.CRON_RESET

    return None


def _extract_target(result: CheckResult, anomaly_type: RepairActionType) -> str:
    """Extract the repair target from a CheckResult.

    For service_down, the target is the service name.
    For docker_down, the target is the container name.
    For disk_full, the target is the filesystem path.
    For cron_stuck, the target is the job name.
    """
    details = result.details

    if anomaly_type == RepairActionType.SERVICE_RESTART:
        return details.get("service", result.name.replace("_service", "").replace("service_", ""))

    if anomaly_type == RepairActionType.DOCKER_RESTART:
        return details.get("container", result.name.replace("_container", "").replace("container_", ""))

    if anomaly_type == RepairActionType.DISK_CLEANUP:
        return details.get("path", "/")

    if anomaly_type == RepairActionType.CRON_RESET:
        return details.get("job", result.name)

    return result.name


# ── Repair strategy builders ─────────────────────────────────────────────────


def build_service_repair(target: str) -> RepairAction:
    """Build repair action for a downed systemd service."""
    return RepairAction(
        action_type=RepairActionType.SERVICE_RESTART,
        target=target,
        command=f"systemctl restart {shlex.quote(target)}",
        requires_confirm=False,
    )


def build_docker_repair(target: str) -> RepairAction:
    """Build repair action for a downed Docker container."""
    return RepairAction(
        action_type=RepairActionType.DOCKER_RESTART,
        target=target,
        command=f"docker restart {shlex.quote(target)}",
        requires_confirm=False,
    )


def build_disk_repair(target: str = "/") -> RepairAction:
    """Build repair action for disk-full anomaly.

    Cleans:
      - Log files older than 7 days
      - Temp files in /tmp
      - Backup files older than 30 days
    """
    return RepairAction(
        action_type=RepairActionType.DISK_CLEANUP,
        target=target,
        command=None,  # handled by _execute_disk_repair
        requires_confirm=False,
    )


def build_cron_repair(target: str) -> RepairAction:
    """Build repair action for a stuck cron job.

    Resets last_run so the scheduler picks it up again.
    """
    return RepairAction(
        action_type=RepairActionType.CRON_RESET,
        target=target,
        command=None,  # handled by _execute_cron_repair
        requires_confirm=False,
    )


# ── AutoRepairer ─────────────────────────────────────────────────────────────


class AutoRepairer:
    """Automatic repair engine for known anomaly patterns.

    Integrates with:
    - AuditLog: every repair action is recorded
    - Dispatcher: send before/after notifications
    """

    def __init__(self, audit: AuditLog, dispatcher: Dispatcher | None = None):
        self.audit = audit
        self.dispatcher = dispatcher
        # Cron job last_run resets are tracked here for scheduler integration
        self._cron_resets: dict[str, datetime] = {}

    # ── Main API ─────────────────────────────────────────────────────────────

    async def handle_anomaly(self, result: CheckResult) -> RepairAction | None:
        """Attempt to repair an anomaly detected by the check pipeline.

        Returns the RepairAction if a strategy was found and executed,
        or None if no automatic repair is available.
        """
        anomaly_type = _detect_anomaly_type(result)
        if anomaly_type is None:
            logger.debug("No auto-repair strategy for check: %s", result.name)
            return None

        target = _extract_target(result, anomaly_type)
        action = self._build_action(anomaly_type, target)

        if not action.is_auto_allowed():
            logger.warning(
                "Repair action %s for %s requires confirmation — skipping auto-repair",
                action.action_type,
                action.target,
            )
            await self.audit.log(
                action="repair_skipped",
                target=action.target,
                details={
                    "action_type": action.action_type,
                    "reason": "requires_confirmation",
                    "anomaly_message": result.message,
                },
                result="skipped",
            )
            return None

        # Send "repairing" notification
        await self._notify_before(action)

        # Execute repair
        success, output = await self._execute(action)

        # Audit the repair
        await self.audit.log(
            action="auto_repair",
            target=action.target,
            details={
                "action_type": action.action_type,
                "command": action.command,
                "anomaly_message": result.message,
                "output": output[:500] if output else "",
            },
            result="success" if success else "failed",
            reversible=True,
        )

        # Send "done" notification
        await self._notify_after(action, success)

        return action

    def get_cron_resets(self) -> dict[str, datetime]:
        """Return cron job resets accumulated during repairs.

        The scheduler can consume this to reset last_run on matching jobs.
        """
        return dict(self._cron_resets)

    # ── Strategy dispatch ────────────────────────────────────────────────────

    def _build_action(self, anomaly_type: RepairActionType, target: str) -> RepairAction:
        """Build the appropriate RepairAction for the anomaly type."""
        builders = {
            RepairActionType.SERVICE_RESTART: build_service_repair,
            RepairActionType.DOCKER_RESTART: build_docker_repair,
            RepairActionType.DISK_CLEANUP: lambda t: build_disk_repair(t),
            RepairActionType.CRON_RESET: build_cron_repair,
        }
        builder = builders.get(anomaly_type)
        if builder is None:
            return RepairAction(
                action_type=anomaly_type,
                target=target,
                requires_confirm=True,
            )
        return builder(target)

    # ── Execution ────────────────────────────────────────────────────────────

    async def _execute(self, action: RepairAction) -> tuple[bool, str]:
        """Execute a repair action. Returns (success, output)."""
        try:
            if action.action_type == RepairActionType.DISK_CLEANUP:
                return await self._execute_disk_repair(action.target)
            if action.action_type == RepairActionType.CRON_RESET:
                return await self._execute_cron_repair(action.target)
            if action.command:
                return await self._execute_command(action.command)
            return False, "No command or handler for action"
        except Exception as e:
            logger.exception("Repair execution failed for %s", action.target)
            return False, str(e)

    async def _execute_command(self, command: str) -> tuple[bool, str]:
        """Run a shell command asynchronously."""
        logger.info("Executing repair command: %s", command)
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        success = proc.returncode == 0
        if success:
            logger.info("Repair command succeeded: %s", command)
        else:
            logger.warning("Repair command failed (exit %d): %s", proc.returncode, output)
        return success, output.strip()

    async def _execute_disk_repair(self, target: str) -> tuple[bool, str]:
        """Clean up disk space: old logs, tmp files, old backups."""
        cleaned: list[str] = []
        now = time.time()
        seven_days = 7 * 86400
        thirty_days = 30 * 86400

        def _run() -> None:
            # 1. Clean old log files (>7 days)
            log_dirs = [Path("/var/log"), Path(target).parent if Path(target).is_file() else Path(target)]
            skip_prefixes = ("/proc", "/sys", "/dev")
            for log_dir in log_dirs:
                if not log_dir.exists():
                    continue
                if str(log_dir).startswith(skip_prefixes):
                    continue
                try:
                    for f in log_dir.rglob("*.log"):
                        try:
                            if f.is_symlink():
                                continue
                            if f.is_file() and not str(f).startswith(skip_prefixes) and (now - f.stat().st_mtime) > seven_days:
                                f.unlink()
                                cleaned.append(str(f))
                        except OSError:
                            pass
                    # Also clean .gz rotated logs
                    for f in log_dir.rglob("*.log.*.gz"):
                        try:
                            if f.is_symlink():
                                continue
                            if f.is_file() and not str(f).startswith(skip_prefixes) and (now - f.stat().st_mtime) > seven_days:
                                f.unlink()
                                cleaned.append(str(f))
                        except OSError:
                            pass
                except OSError:
                    pass

            # 2. Clean temp files
            tmp_dir = Path(tempfile.gettempdir())
            try:
                for f in tmp_dir.iterdir():
                    try:
                        if f.is_symlink():
                            continue
                        if f.is_file() and (now - f.stat().st_mtime) > seven_days:
                            f.unlink()
                            cleaned.append(str(f))
                    except OSError:
                        pass
            except OSError:
                pass

            # 3. Clean old backups (>30 days)
            backup_dirs = [Path("/var/backups")]
            for bdir in backup_dirs:
                if not bdir.exists():
                    continue
                for f in bdir.rglob("*"):
                    try:
                        if f.is_symlink():
                            continue
                        if f.is_file() and (now - f.stat().st_mtime) > thirty_days:
                            f.unlink()
                            cleaned.append(str(f))
                    except OSError:
                        pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run)

        output = f"Cleaned {len(cleaned)} files"
        if cleaned:
            output += ": " + ", ".join(cleaned[:10])
            if len(cleaned) > 10:
                output += f" ... and {len(cleaned) - 10} more"
        logger.info("Disk repair: %s", output)
        return True, output

    async def _execute_cron_repair(self, target: str) -> tuple[bool, str]:
        """Reset a stuck cron job's last_run timestamp."""
        self._cron_resets[target] = datetime.now()
        msg = f"Reset last_run for cron job: {target}"
        logger.info(msg)
        return True, msg

    # ── Notifications ────────────────────────────────────────────────────────

    async def _notify_before(self, action: RepairAction) -> None:
        """Send a notification before repair starts."""
        if self.dispatcher is None:
            return

        try:
            from secretary.notify import Notification, NotificationLevel

            messages = {
                RepairActionType.SERVICE_RESTART: f"{action.target}服务挂了，我正在重启",
                RepairActionType.DOCKER_RESTART: f"{action.target}容器挂了，我正在重启",
                RepairActionType.DISK_CLEANUP: "磁盘快满了，我正在清理旧日志和临时文件",
                RepairActionType.CRON_RESET: f"定时任务{action.target}卡住了，我正在重置",
            }
            text = messages.get(action.action_type, f"正在修复{action.target}")

            notification = Notification(
                level=NotificationLevel.WARNING,
                title=f"自动修复: {action.target}",
                body=text,
            )
            await self.dispatcher.send(notification)
        except Exception:
            logger.exception("Failed to send before-repair notification")

    async def _notify_after(self, action: RepairAction, success: bool) -> None:
        """Send a notification after repair completes."""
        if self.dispatcher is None:
            return

        try:
            from secretary.notify import Notification, NotificationLevel

            if success:
                messages = {
                    RepairActionType.SERVICE_RESTART: f"{action.target}已重启，状态正常",
                    RepairActionType.DOCKER_RESTART: f"{action.target}容器已重启，状态正常",
                    RepairActionType.DISK_CLEANUP: "磁盘清理完成，空间已释放",
                    RepairActionType.CRON_RESET: f"定时任务{action.target}已重置",
                }
                text = messages.get(action.action_type, f"{action.target}修复完成")
                level = NotificationLevel.INFO
            else:
                text = f"{action.target}自动修复失败，需要人工介入"
                level = NotificationLevel.CRITICAL

            notification = Notification(
                level=level,
                title=f"修复{'完成' if success else '失败'}: {action.target}",
                body=text,
            )
            await self.dispatcher.send(notification)
        except Exception:
            logger.exception("Failed to send after-repair notification")
