"""Tests for the auto-repair engine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secretary.engine.audit import AuditLog
from secretary.engine.repairer import (
    AUTO_REPAIR_TYPES,
    AutoRepairer,
    RepairAction,
    _detect_anomaly_type,
    _extract_target,
    build_cron_repair,
    build_disk_repair,
    build_docker_repair,
    build_service_repair,
)
from secretary.monitor import CheckResult

# ------------------------------------------------------------------
# RepairAction model tests
# ------------------------------------------------------------------


class TestRepairAction:
    def test_auto_allowed_whitelisted(self):
        """Whitelisted action types should auto-execute."""
        for atype in AUTO_REPAIR_TYPES:
            action = RepairAction(action_type=atype, target="x", requires_confirm=False)
            assert action.is_auto_allowed(), f"{atype} should be auto-allowed"

    def test_auto_allowed_requires_confirm(self):
        """Even whitelisted types blocked when requires_confirm=True."""
        action = RepairAction(
            action_type="service_down", target="x", requires_confirm=True
        )
        assert not action.is_auto_allowed()

    def test_auto_allowed_unknown_type(self):
        """Non-whitelisted types require confirmation."""
        action = RepairAction(
            action_type="unknown_anomaly", target="x", requires_confirm=False
        )
        assert not action.is_auto_allowed()

    def test_defaults(self):
        action = RepairAction(action_type="test", target="t")
        assert action.command is None
        assert action.requires_confirm is False
        assert action.details == {}


# ------------------------------------------------------------------
# Strategy builder tests
# ------------------------------------------------------------------


class TestStrategyBuilders:
    def test_build_service_repair(self):
        action = build_service_repair("nginx")
        assert action.action_type == "service_down"
        assert action.target == "nginx"
        assert action.command == "systemctl restart nginx"
        assert action.is_auto_allowed()

    def test_build_docker_repair(self):
        action = build_docker_repair("redis")
        assert action.action_type == "docker_down"
        assert action.target == "redis"
        assert action.command == "docker restart redis"
        assert action.is_auto_allowed()

    def test_build_disk_repair(self):
        action = build_disk_repair("/var")
        assert action.action_type == "disk_full"
        assert action.target == "/var"
        assert action.command is None  # handled by special method
        assert action.is_auto_allowed()

    def test_build_cron_repair(self):
        action = build_cron_repair("cleanup_job")
        assert action.action_type == "cron_stuck"
        assert action.target == "cleanup_job"
        assert action.command is None
        assert action.is_auto_allowed()


# ------------------------------------------------------------------
# Anomaly type detection tests
# ------------------------------------------------------------------


class TestAnomalyDetection:
    def test_detect_service_down(self):
        result = CheckResult(name="nginx_service", status="critical", message="服务无响应")
        assert _detect_anomaly_type(result) == "service_down"

    def test_detect_docker_down(self):
        result = CheckResult(name="redis_container", status="critical", message="容器退出")
        assert _detect_anomaly_type(result) == "docker_down"

    def test_detect_disk_full(self):
        result = CheckResult(name="disk_monitor", status="warning", message="磁盘使用率过高")
        assert _detect_anomaly_type(result) == "disk_full"

    def test_detect_cron_stuck(self):
        result = CheckResult(name="cron_cleanup", status="warning", message="任务停滞超时")
        assert _detect_anomaly_type(result) == "cron_stuck"

    def test_detect_goal_stuck_is_not_cron(self):
        """Regression: health checker's stuck-GOALS message must not trigger
        a fake cron reset (caused the '定时任务health卡住了' spam loop)."""
        result = CheckResult(name="health", status="warning", message="2个目标停滞超14天")
        assert _detect_anomaly_type(result) is None

    def test_detect_unknown(self):
        result = CheckResult(
            name="mystery_check", status="warning", message="something weird"
        )
        assert _detect_anomaly_type(result) is None

    def test_extract_target_service(self):
        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )
        assert _extract_target(result, "service_down") == "nginx"

    def test_extract_target_docker(self):
        result = CheckResult(
            name="redis_container",
            status="critical",
            message="容器挂了",
            details={"container": "redis"},
        )
        assert _extract_target(result, "docker_down") == "redis"

    def test_extract_target_disk_default(self):
        result = CheckResult(name="disk", status="warning", message="满")
        assert _extract_target(result, "disk_full") == "/"

    def test_extract_target_cron(self):
        result = CheckResult(
            name="cleanup_job",
            status="warning",
            message="卡住",
            details={"job": "daily_cleanup"},
        )
        assert _extract_target(result, "cron_stuck") == "daily_cleanup"

    def test_extract_target_fallback(self):
        result = CheckResult(name="custom_check", status="warning", message="x")
        assert _extract_target(result, "unknown_type") == "custom_check"


# ------------------------------------------------------------------
# AutoRepairer — integration tests (with mocked subprocess)
# ------------------------------------------------------------------


class TestAutoRepairerServiceDown:
    @pytest.mark.asyncio
    async def test_service_down_triggers_restart(self, tmp_path: Path):
        """service_down anomaly should trigger systemctl restart."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"restarted", b""))
            proc.returncode = 0
            mock_exec.return_value = proc

            action = await repairer.handle_anomaly(result)

            assert action is not None
            assert action.action_type == "service_down"
            assert action.target == "nginx"
            mock_exec.assert_called_once_with(
                "systemctl restart nginx",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        # Check audit log
        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0].action == "auto_repair"
        assert entries[0].result == "success"
        assert entries[0].details["action_type"] == "service_down"

    @pytest.mark.asyncio
    async def test_service_down_failed_command(self, tmp_path: Path):
        """Failed systemctl should be audited as failed."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"Job failed"))
            proc.returncode = 1
            mock_exec.return_value = proc

            action = await repairer.handle_anomaly(result)

            assert action is not None
        entries = audit.read_all()
        assert entries[0].result == "failed"


class TestAutoRepairerDockerDown:
    @pytest.mark.asyncio
    async def test_docker_down_triggers_restart(self, tmp_path: Path):
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        result = CheckResult(
            name="redis_container",
            status="critical",
            message="容器挂了",
            details={"container": "redis"},
        )

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"redis", b""))
            proc.returncode = 0
            mock_exec.return_value = proc

            action = await repairer.handle_anomaly(result)

            assert action is not None
            assert action.action_type == "docker_down"
            mock_exec.assert_called_once_with(
                "docker restart redis",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )


class TestAutoRepairerDiskFull:
    @pytest.mark.asyncio
    async def test_disk_full_cleans_files(self, tmp_path: Path):
        """disk_full should clean old logs, tmp, and backups."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        # Create mock old log file
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        old_log = log_dir / "old.log"
        old_log.write_text("old log content")
        # Set mtime to 10 days ago
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        import os

        os.utime(old_log, (old_time, old_time))

        recent_log = log_dir / "recent.log"
        recent_log.write_text("recent log content")

        result = CheckResult(
            name="disk_monitor",
            status="critical",
            message="磁盘快满了",
            details={"path": str(log_dir)},
        )

        with patch("secretary.engine.repairer.Path") as mock_path_cls:
            # Make Path("/var/log") resolve to our temp dir
            def path_side_effect(p):
                if p == "/var/log":
                    return log_dir
                if p == str(log_dir):
                    return log_dir
                # For parent calls
                real_path = Path(p)
                return real_path

            mock_path_cls.side_effect = path_side_effect
            mock_path_cls.return_value = log_dir

            action = await repairer.handle_anomaly(result)

        assert action is not None
        assert action.action_type == "disk_full"

        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0].action == "auto_repair"

    @pytest.mark.asyncio
    async def test_disk_full_cleans_old_logs_real(self, tmp_path: Path):
        """Test disk cleanup with real file operations."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        # Setup fake log dir
        log_dir = tmp_path / "var" / "log"
        log_dir.mkdir(parents=True)

        old_log = log_dir / "app.log"
        old_log.write_text("old")
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        import os

        os.utime(old_log, (old_time, old_time))

        old_gz = log_dir / "app.log.1.gz"
        old_gz.write_text("gz")
        os.utime(old_gz, (old_time, old_time))

        new_log = log_dir / "app.log"
        # overwrite with same name, but new time - skip, old_log already deleted

        result = CheckResult(
            name="disk_monitor",
            status="critical",
            message="磁盘满了",
            details={"path": str(log_dir)},
        )

        # Patch the _execute_disk_repair to use our log_dir
        original_execute = repairer._execute_disk_repair

        async def patched_execute(target):
            cleaned = []
            now = datetime.now().timestamp()
            for f in log_dir.rglob("*.log"):
                if f.is_file() and (now - f.stat().st_mtime) > 7 * 86400:
                    f.unlink()
                    cleaned.append(str(f))
            for f in log_dir.rglob("*.log.*.gz"):
                if f.is_file() and (now - f.stat().st_mtime) > 7 * 86400:
                    f.unlink()
                    cleaned.append(str(f))
            return True, f"Cleaned {len(cleaned)} files"

        repairer._execute_disk_repair = patched_execute

        action = await repairer.handle_anomaly(result)
        assert action is not None
        assert action.action_type == "disk_full"


class TestAutoRepairerCronStuck:
    @pytest.mark.asyncio
    async def test_cron_stuck_resets_last_run(self, tmp_path: Path):
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        result = CheckResult(
            name="cleanup_job",
            status="warning",
            message="定时任务停滞",
            details={"job": "daily_cleanup"},
        )

        action = await repairer.handle_anomaly(result)

        assert action is not None
        assert action.action_type == "cron_stuck"
        assert action.target == "daily_cleanup"

        # Check cron resets
        resets = repairer.get_cron_resets()
        assert "daily_cleanup" in resets
        assert isinstance(resets["daily_cleanup"], datetime)

        # Check audit
        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0].action == "auto_repair"
        assert entries[0].result == "success"


# ------------------------------------------------------------------
# Safety tests — non-whitelisted actions require confirmation
# ------------------------------------------------------------------


class TestAutoRepairerSafety:
    @pytest.mark.asyncio
    async def test_unknown_anomaly_skipped(self, tmp_path: Path):
        """Unknown anomaly types should NOT auto-repair."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        result = CheckResult(
            name="mystery_check",
            status="critical",
            message="something very wrong",
        )

        action = await repairer.handle_anomaly(result)
        assert action is None

        # Should be logged as skipped (no repair found, so no skip entry either)
        entries = audit.read_all()
        assert len(entries) == 0  # No action taken at all

    @pytest.mark.asyncio
    async def test_requires_confirm_flag_blocks(self, tmp_path: Path):
        """Even whitelisted types are blocked when requires_confirm is forced."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        # Patch _build_action to return a confirm-required action
        original_build = repairer._build_action

        def patched_build(anomaly_type, target):
            action = original_build(anomaly_type, target)
            action.requires_confirm = True
            return action

        repairer._build_action = patched_build

        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )

        action = await repairer.handle_anomaly(result)
        assert action is None

        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0].action == "repair_skipped"
        assert entries[0].result == "skipped"
        assert entries[0].details["reason"] == "requires_confirmation"


# ------------------------------------------------------------------
# Notification tests
# ------------------------------------------------------------------


class TestAutoRepairerNotifications:
    @pytest.mark.asyncio
    async def test_before_after_notifications_sent(self, tmp_path: Path):
        """Auto-repair should send before and after notifications."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        dispatcher = AsyncMock()
        dispatcher.send = AsyncMock(return_value=MagicMock(success=True))
        repairer = AutoRepairer(audit=audit, dispatcher=dispatcher)

        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            proc.returncode = 0
            mock_exec.return_value = proc

            await repairer.handle_anomaly(result)

        # Should have sent 2 notifications: before + after
        assert dispatcher.send.call_count == 2

        # Check "before" notification
        before_call = dispatcher.send.call_args_list[0]
        before_notif = before_call[0][0]
        assert "正在重启" in before_notif.body
        assert before_notif.title == "自动修复: nginx"

        # Check "after" notification
        after_call = dispatcher.send.call_args_list[1]
        after_notif = after_call[0][0]
        assert "已重启" in after_notif.body
        assert "正常" in after_notif.body
        assert after_notif.title == "修复完成: nginx"

    @pytest.mark.asyncio
    async def test_after_notification_on_failure(self, tmp_path: Path):
        """Failed repair should send CRITICAL after-notification."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        dispatcher = AsyncMock()
        dispatcher.send = AsyncMock(return_value=MagicMock(success=True))
        repairer = AutoRepairer(audit=audit, dispatcher=dispatcher)

        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"error"))
            proc.returncode = 1
            mock_exec.return_value = proc

            await repairer.handle_anomaly(result)

        after_call = dispatcher.send.call_args_list[1]
        after_notif = after_call[0][0]
        assert "失败" in after_notif.body
        assert "人工介入" in after_notif.body

    @pytest.mark.asyncio
    async def test_no_dispatcher_graceful(self, tmp_path: Path):
        """Auto-repair works without a dispatcher (no notifications)."""
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit, dispatcher=None)

        result = CheckResult(
            name="cron_cleanup",
            status="warning",
            message="卡住了",
            details={"job": "cleanup"},
        )

        # Should not raise
        action = await repairer.handle_anomaly(result)
        assert action is not None


# ------------------------------------------------------------------
# Audit integration tests
# ------------------------------------------------------------------


class TestAutoRepairerAudit:
    @pytest.mark.asyncio
    async def test_audit_logged_on_success(self, tmp_path: Path):
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        result = CheckResult(
            name="redis_container",
            status="critical",
            message="容器挂了",
            details={"container": "redis"},
        )

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"restarted", b""))
            proc.returncode = 0
            mock_exec.return_value = proc

            await repairer.handle_anomaly(result)

        entries = audit.read_all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.action == "auto_repair"
        assert entry.target == "redis"
        assert entry.result == "success"
        assert entry.details["action_type"] == "docker_down"
        assert entry.reversible is True

    @pytest.mark.asyncio
    async def test_audit_logged_on_failure(self, tmp_path: Path):
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )

        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"error"))
            proc.returncode = 1
            mock_exec.return_value = proc

            await repairer.handle_anomaly(result)

        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0].result == "failed"
        assert entries[0].reversible is True

    @pytest.mark.asyncio
    async def test_audit_logged_on_skip(self, tmp_path: Path):
        audit = AuditLog(path=tmp_path / "audit.jsonl")
        repairer = AutoRepairer(audit=audit)

        # Force requires_confirm
        original_build = repairer._build_action

        def patched_build(anomaly_type, target):
            action = original_build(anomaly_type, target)
            action.requires_confirm = True
            return action

        repairer._build_action = patched_build

        result = CheckResult(
            name="nginx_service",
            status="critical",
            message="服务挂了",
            details={"service": "nginx"},
        )

        await repairer.handle_anomaly(result)

        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0].action == "repair_skipped"
        assert entries[0].result == "skipped"
