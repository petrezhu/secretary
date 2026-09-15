"""Integration tests for daemon, scheduler, and full pipeline."""

from __future__ import annotations

import asyncio
import signal
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secretary.config import (
    Config,
    DataConfig,
    EngineConfig,
    MonitorConfig,
    NotifyConfig,
    ResourceGuardConfig,
)
from secretary.daemon import SecretaryDaemon
from secretary.engine import Job, Scheduler, TickResult
from secretary.engine.audit import AuditLog
from secretary.monitor import CheckerRegistry, CheckResult
from secretary.notify import Notification, NotificationLevel, SendResult

# ── Helpers ───────────────────────────────────────────────────────────


class MockChecker:
    """A mock checker that returns a configurable result."""

    def __init__(self, name: str, result: CheckResult):
        self.name = name
        self._result = result

    async def check(self, repo: Any) -> CheckResult:
        return self._result


class MockDispatcher:
    """A mock dispatcher that records sent notifications."""

    def __init__(self):
        self.sent: list[Notification] = []
        self._adapters: dict = {}

    def register_adapter(self, name: str, adapter) -> None:
        self._adapters[name] = adapter

    async def send(self, notification: Notification) -> SendResult:
        self.sent.append(notification)
        return SendResult(success=True, channel="mock")

    async def health_check(self) -> dict[str, bool]:
        return {"mock": True}


class MockResourceGuard:
    """A mock resource guard with controllable behavior."""

    def __init__(self, resources_ok: bool = True):
        self.resources_ok = resources_ok
        self.check_count = 0
        self.wait_count = 0

    async def check_resources(self) -> bool:
        self.check_count += 1
        return self.resources_ok

    async def wait_for_resources(self) -> None:
        self.wait_count += 1


def make_config(**overrides: Any) -> Config:
    """Build a test Config with sensible defaults."""
    return Config(
        tick_interval=overrides.get("tick_interval", 1),
        data=DataConfig(
            goals_db=":memory:",
            tasks_db=":memory:",
        ),
        monitor=MonitorConfig(),
        notify=NotifyConfig(),
        engine=EngineConfig(
            resource_guard=ResourceGuardConfig(
                backoff_initial=1,
                backoff_multiplier=2,
                backoff_max=10,
            )
        ),
    )


# ── Job & Scheduler Tests ─────────────────────────────────────────────


class TestJob:
    """Test Job dataclass."""

    def test_job_defaults(self):
        async def noop():
            pass

        job = Job(name="test", action=noop, interval_seconds=60)
        assert job.name == "test"
        assert job.interval_seconds == 60
        assert job.resource_check is True
        assert job.max_retries == 3
        assert job.last_run is None
        assert job.enabled is True


class TestSchedulerTick:
    """Test Scheduler.tick() — job scheduling, resource guard, retries, audit."""

    @pytest.fixture
    def components(self, tmp_path):
        """Create mock components for scheduler."""
        audit = AuditLog(path=tmp_path / "test_audit.jsonl")
        return {
            "config": make_config(),
            "repo": MagicMock(),
            "checkers": CheckerRegistry(),
            "dispatcher": MockDispatcher(),
            "audit": audit,
            "resource_guard": MockResourceGuard(resources_ok=True),
        }

    async def test_tick_executes_due_jobs(self, components):
        """Jobs with last_run=None should execute immediately."""
        executed = []

        async def track_action():
            executed.append(True)

        scheduler = Scheduler(**components)
        scheduler.add_job(Job(name="test_job", action=track_action, interval_seconds=60))

        result = await scheduler.tick()
        assert result.jobs_executed == 1
        assert result.jobs_failed == 0
        assert len(executed) == 1

    async def test_tick_skips_not_due_jobs(self, components):
        """Jobs that haven't reached their interval should be skipped."""
        executed = []

        async def track_action():
            executed.append(True)

        scheduler = Scheduler(**components)
        job = Job(name="test_job", action=track_action, interval_seconds=3600)
        job.last_run = datetime.now()  # Just ran
        scheduler.add_job(job)

        result = await scheduler.tick()
        assert result.jobs_executed == 0
        assert len(executed) == 0

    async def test_tick_skips_disabled_jobs(self, components):
        """Disabled jobs should not execute."""
        executed = []

        async def track_action():
            executed.append(True)

        scheduler = Scheduler(**components)
        scheduler.add_job(
            Job(name="disabled", action=track_action, interval_seconds=0, enabled=False)
        )

        result = await scheduler.tick()
        assert result.jobs_executed == 0
        assert len(executed) == 0

    async def test_tick_retries_on_failure(self, components):
        """Failed jobs should retry up to max_retries."""
        attempt_count = 0

        async def flaky_action():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise RuntimeError(f"Fail #{attempt_count}")

        scheduler = Scheduler(**components)
        scheduler.add_job(
            Job(name="flaky", action=flaky_action, interval_seconds=0, max_retries=3)
        )

        result = await scheduler.tick()
        assert result.jobs_executed == 1
        assert result.jobs_failed == 0
        assert attempt_count == 3  # Failed twice, succeeded third time

    async def test_tick_records_failure_after_max_retries(self, components):
        """Job should be marked failed after exhausting retries."""
        async def always_fail():
            raise RuntimeError("permanent failure")

        scheduler = Scheduler(**components)
        scheduler.add_job(
            Job(name="doomed", action=always_fail, interval_seconds=0, max_retries=2)
        )

        result = await scheduler.tick()
        assert result.jobs_executed == 0
        assert result.jobs_failed == 1

    async def test_tick_resource_guard_blocks_jobs(self, components):
        """Jobs with resource_check=True should be skipped when resources low."""
        components["resource_guard"] = MockResourceGuard(resources_ok=False)

        executed = []

        async def track_action():
            executed.append(True)

        scheduler = Scheduler(**components)
        scheduler.add_job(
            Job(name="heavy", action=track_action, interval_seconds=0, resource_check=True)
        )

        result = await scheduler.tick()
        assert result.jobs_executed == 0
        assert result.jobs_skipped == 1
        assert len(executed) == 0

    async def test_tick_resource_guard_skippable_jobs(self, components):
        """Jobs with resource_check=False should run even when resources low."""
        components["resource_guard"] = MockResourceGuard(resources_ok=False)

        executed = []

        async def track_action():
            executed.append(True)

        scheduler = Scheduler(**components)
        scheduler.add_job(
            Job(name="light", action=track_action, interval_seconds=0, resource_check=False)
        )

        result = await scheduler.tick()
        assert result.jobs_executed == 1
        assert len(executed) == 1

    async def test_tick_audit_logs_success(self, components, tmp_path):
        """Successful job executions should be audit logged."""
        scheduler = Scheduler(**components)

        async def ok_action():
            pass

        scheduler.add_job(Job(name="ok_job", action=ok_action, interval_seconds=0))
        await scheduler.tick()

        entries = components["audit"].query(action="job:ok_job")
        assert len(entries) == 1
        assert entries[0].result == "success"

    async def test_tick_audit_logs_failure(self, components):
        """Failed job executions should be audit logged."""
        scheduler = Scheduler(**components)

        async def bad_action():
            raise RuntimeError("boom")

        scheduler.add_job(
            Job(name="bad_job", action=bad_action, interval_seconds=0, max_retries=1)
        )
        await scheduler.tick()

        entries = components["audit"].query(action="job:bad_job")
        assert len(entries) == 1
        assert entries[0].result == "failed"

    async def test_tick_audit_logs_skipped(self, components):
        """Skipped jobs (resource guard) should be audit logged."""
        components["resource_guard"] = MockResourceGuard(resources_ok=False)
        scheduler = Scheduler(**components)

        async def noop():
            pass

        scheduler.add_job(
            Job(name="blocked", action=noop, interval_seconds=0, resource_check=True)
        )
        await scheduler.tick()

        entries = components["audit"].query(action="job:blocked")
        assert len(entries) == 1
        assert entries[0].result == "skipped"

    async def test_tick_notifies_on_final_failure(self, components):
        """Dispatcher should be called when a job fails permanently."""
        scheduler = Scheduler(**components)

        async def bad_action():
            raise RuntimeError("boom")

        scheduler.add_job(
            Job(name="notify_test", action=bad_action, interval_seconds=0, max_retries=1)
        )
        await scheduler.tick()

        assert len(components["dispatcher"].sent) == 1
        notif = components["dispatcher"].sent[0]
        assert notif.level == NotificationLevel.CRITICAL
        assert "notify_test" in notif.title

    async def test_tick_updates_last_run(self, components):
        """Successful execution should update job.last_run."""
        scheduler = Scheduler(**components)

        async def noop():
            pass

        job = Job(name="tracked", action=noop, interval_seconds=0)
        assert job.last_run is None
        scheduler.add_job(job)

        await scheduler.tick()
        assert job.last_run is not None

    async def test_tick_returns_correct_result(self, components):
        """TickResult should accurately reflect execution stats."""
        call_count = 0

        async def mixed_action():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first fail")
            # Second call succeeds

        scheduler = Scheduler(**components)
        scheduler.add_job(
            Job(name="mixed", action=mixed_action, interval_seconds=0, max_retries=2)
        )

        result = await scheduler.tick()
        assert result.jobs_executed == 1
        assert result.jobs_failed == 0
        assert result.jobs_skipped == 0


class TestSchedulerPipeline:
    """Test the check→anomaly→notify→audit pipeline."""

    @pytest.fixture
    def pipeline_components(self, tmp_path):
        """Create components with mock checkers for pipeline testing."""
        audit = AuditLog(path=tmp_path / "pipeline_audit.jsonl")
        dispatcher = MockDispatcher()
        checkers = CheckerRegistry()

        return {
            "config": make_config(),
            "repo": MagicMock(),
            "checkers": checkers,
            "dispatcher": dispatcher,
            "audit": audit,
        }

    async def test_pipeline_all_ok(self, pipeline_components):
        """When all checks pass, no notifications should be sent."""
        pipeline_components["checkers"].register(
            MockChecker("ok_check", CheckResult(name="ok_check", status="ok", message="All good"))
        )

        scheduler = Scheduler(**pipeline_components)
        results = await scheduler.run_check_pipeline()

        assert len(results) == 1
        assert results[0].status == "ok"
        assert len(pipeline_components["dispatcher"].sent) == 0

        # Audit should record the pipeline run
        entries = pipeline_components["audit"].query(action="check_pipeline")
        assert len(entries) == 1

    async def test_pipeline_warning_triggers_notification(self, pipeline_components):
        """Warning-level check results should trigger notifications."""
        pipeline_components["checkers"].register(
            MockChecker(
                "warn_check",
                CheckResult(name="warn_check", status="warning", message="Something iffy"),
            )
        )

        scheduler = Scheduler(**pipeline_components)
        results = await scheduler.run_check_pipeline()

        assert len(results) == 1
        assert results[0].status == "warning"

        # Should send notification
        assert len(pipeline_components["dispatcher"].sent) == 1
        notif = pipeline_components["dispatcher"].sent[0]
        assert notif.level == NotificationLevel.WARNING
        assert "warn_check" in notif.title

    async def test_pipeline_critical_triggers_notification(self, pipeline_components):
        """Critical check results should send CRITICAL notifications."""
        pipeline_components["checkers"].register(
            MockChecker(
                "crit_check",
                CheckResult(name="crit_check", status="critical", message="System down!"),
            )
        )

        scheduler = Scheduler(**pipeline_components)
        await scheduler.run_check_pipeline()

        assert len(pipeline_components["dispatcher"].sent) == 1
        assert pipeline_components["dispatcher"].sent[0].level == NotificationLevel.CRITICAL

    async def test_pipeline_mixed_results(self, pipeline_components):
        """Pipeline should handle mix of ok/warning/critical correctly."""
        pipeline_components["checkers"].register(
            MockChecker("ok1", CheckResult(name="ok1", status="ok", message="fine"))
        )
        pipeline_components["checkers"].register(
            MockChecker("warn1", CheckResult(name="warn1", status="warning", message="warn"))
        )
        pipeline_components["checkers"].register(
            MockChecker("crit1", CheckResult(name="crit1", status="critical", message="crit"))
        )

        scheduler = Scheduler(**pipeline_components)
        results = await scheduler.run_check_pipeline()

        assert len(results) == 3
        # Two anomalies (warning + critical) = two notifications
        assert len(pipeline_components["dispatcher"].sent) == 2

        # Two anomaly audit entries
        anomaly_entries = pipeline_components["audit"].query(action="check_anomaly")
        assert len(anomaly_entries) == 2

    async def test_pipeline_audit_records_details(self, pipeline_components):
        """Pipeline audit should include check details."""
        pipeline_components["checkers"].register(
            MockChecker("detail_check", CheckResult(
                name="detail_check", status="ok", message="All clear"
            ))
        )

        scheduler = Scheduler(**pipeline_components)
        await scheduler.run_check_pipeline()

        entries = pipeline_components["audit"].query(action="check_pipeline")
        assert len(entries) == 1
        assert entries[0].details["total_checks"] == 1
        assert entries[0].details["anomalies"] == 0
        assert "detail_check" in entries[0].details["statuses"]


class TestSchedulerLifecycle:
    """Test Scheduler start/stop and job management."""

    async def test_start_registers_default_pipeline(self):
        """Scheduler.start() should register the check_pipeline job."""
        config = make_config()
        config.check_interval = 120

        scheduler = Scheduler(
            config=config,
            repo=MagicMock(),
            checkers=CheckerRegistry(),
            dispatcher=MockDispatcher(),
            audit=AuditLog(path=Path(tempfile.mktemp(suffix=".jsonl"))),
        )

        await scheduler.start()
        assert len(scheduler.jobs) == 1
        assert scheduler.jobs[0].name == "check_pipeline"

        await scheduler.stop()

    async def test_add_remove_jobs(self):
        """Jobs can be added and removed by name."""
        scheduler = Scheduler(
            config=make_config(),
            repo=MagicMock(),
            checkers=CheckerRegistry(),
            dispatcher=MockDispatcher(),
            audit=AuditLog(path=Path(tempfile.mktemp(suffix=".jsonl"))),
        )

        async def noop():
            pass

        scheduler.add_job(Job(name="a", action=noop, interval_seconds=10))
        scheduler.add_job(Job(name="b", action=noop, interval_seconds=20))
        assert len(scheduler.jobs) == 2

        assert scheduler.remove_job("a") is True
        assert len(scheduler.jobs) == 1
        assert scheduler.jobs[0].name == "b"

        assert scheduler.remove_job("nonexistent") is False

    async def test_stop_sets_running_false(self):
        scheduler = Scheduler(
            config=make_config(),
            repo=MagicMock(),
            checkers=CheckerRegistry(),
            dispatcher=MockDispatcher(),
            audit=AuditLog(path=Path(tempfile.mktemp(suffix=".jsonl"))),
        )
        scheduler._running = True
        await scheduler.stop()
        assert scheduler._running is False


# ── Daemon Tests ──────────────────────────────────────────────────────


class TestSecretaryDaemon:
    """Test SecretaryDaemon lifecycle and main loop."""

    def test_daemon_initial_state(self):
        """Daemon should start in non-running state."""
        daemon = SecretaryDaemon(make_config())
        assert daemon.running is False
        assert daemon.uptime_seconds == 0.0
        assert daemon.repo is None
        assert daemon.scheduler is None

    async def test_daemon_run_checks(self):
        """run_checks should initialize repo, run checkers, and close."""
        with patch("secretary.daemon.Repository") as MockRepo:
            mock_repo = AsyncMock()
            mock_repo.initialize = AsyncMock()
            mock_repo.close = AsyncMock()
            MockRepo.return_value = mock_repo

            with patch("secretary.daemon.HealthChecker") as MockHC, \
                 patch("secretary.daemon.DeadmanChecker") as MockDC:

                # Make checkers return ok
                mock_hc = MagicMock()
                mock_hc.name = "health"
                mock_hc.check = AsyncMock(
                    return_value=CheckResult(name="health", status="ok", message="ok")
                )
                MockHC.return_value = mock_hc

                mock_dc = MagicMock()
                mock_dc.name = "deadman"
                mock_dc.check = AsyncMock(
                    return_value=CheckResult(name="deadman", status="ok", message="ok")
                )
                MockDC.return_value = mock_dc

                daemon = SecretaryDaemon(make_config())
                results = await daemon.run_checks()

                assert len(results) == 2
                mock_repo.initialize.assert_awaited_once()
                mock_repo.close.assert_awaited_once()

    async def test_daemon_main_loop_respects_running_flag(self):
        """Main loop should exit when running is set to False."""
        daemon = SecretaryDaemon(make_config(tick_interval=1))
        daemon.running = True
        daemon.resource_guard = MockResourceGuard(resources_ok=True)

        tick_results = []

        mock_scheduler = AsyncMock()
        mock_scheduler.tick = AsyncMock(return_value=TickResult())
        mock_scheduler.stop = AsyncMock()
        daemon.scheduler = mock_scheduler
        daemon.repo = AsyncMock()
        daemon.audit = AsyncMock()

        # Stop after a brief moment
        async def stop_after_delay():
            await asyncio.sleep(0.1)
            daemon.running = False

        stop_task = asyncio.create_task(stop_after_delay())
        await daemon.main_loop()
        await stop_task

        # Should have ticked at least once
        assert mock_scheduler.tick.await_count >= 1

    async def test_daemon_main_loop_resource_guard_backoff(self):
        """Main loop should back off when resource guard fails."""
        daemon = SecretaryDaemon(make_config(tick_interval=1))
        daemon.running = True
        daemon.resource_guard = MockResourceGuard(resources_ok=False)

        mock_scheduler = AsyncMock()
        mock_scheduler.tick = AsyncMock(return_value=TickResult())
        daemon.scheduler = mock_scheduler
        daemon.repo = AsyncMock()
        daemon.audit = AsyncMock()

        call_count = 0

        async def counting_check():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                daemon.running = False
            return False

        daemon.resource_guard.check_resources = counting_check
        daemon.resource_guard.wait_for_resources = AsyncMock()

        await daemon.main_loop()

        # Scheduler should NOT have been called (resources insufficient)
        mock_scheduler.tick.assert_not_awaited()

    async def test_daemon_stop_is_idempotent(self):
        """Calling stop() multiple times should not raise."""
        daemon = SecretaryDaemon(make_config())
        daemon.running = True
        daemon.repo = AsyncMock()
        daemon.audit = AsyncMock()
        daemon.scheduler = AsyncMock()

        await daemon.stop()
        assert daemon.running is False

        # Second stop should be a no-op
        await daemon.stop()
        assert daemon.running is False

    async def test_daemon_uptime_tracking(self):
        """Uptime should increase while daemon is running."""
        daemon = SecretaryDaemon(make_config())
        daemon._started_at = datetime.now() - timedelta(seconds=42)

        assert daemon.uptime_seconds >= 41.0
        assert daemon.uptime_seconds < 44.0


class TestSchedulerTickIntegration:
    """Integration tests combining scheduler + real audit log."""

    async def test_full_tick_cycle_with_audit(self, tmp_path):
        """Complete tick cycle: multiple jobs, some succeed, some fail."""
        audit = AuditLog(path=tmp_path / "integration_audit.jsonl")
        dispatcher = MockDispatcher()

        scheduler = Scheduler(
            config=make_config(),
            repo=MagicMock(),
            checkers=CheckerRegistry(),
            dispatcher=dispatcher,
            audit=audit,
            resource_guard=MockResourceGuard(resources_ok=True),
        )

        run_log = []

        async def good_job():
            run_log.append("good")

        async def bad_job():
            run_log.append("bad")
            raise RuntimeError("intentional failure")

        scheduler.add_job(Job(name="good", action=good_job, interval_seconds=0))
        scheduler.add_job(
            Job(name="bad", action=bad_job, interval_seconds=0, max_retries=1)
        )

        result = await scheduler.tick()

        assert result.jobs_executed == 1
        assert result.jobs_failed == 1
        assert "good" in run_log
        assert "bad" in run_log

        # Verify audit trail
        all_entries = audit.read_all()
        assert len(all_entries) == 2
        success_entries = [e for e in all_entries if e.result == "success"]
        failed_entries = [e for e in all_entries if e.result == "failed"]
        assert len(success_entries) == 1
        assert len(failed_entries) == 1

        # Failed job should trigger notification
        assert len(dispatcher.sent) == 1
        assert dispatcher.sent[0].level == NotificationLevel.CRITICAL

    async def test_scheduler_tick_multiple_cycles(self, tmp_path):
        """Jobs should only run again after their interval elapses."""
        audit = AuditLog(path=tmp_path / "multi_cycle_audit.jsonl")
        scheduler = Scheduler(
            config=make_config(),
            repo=MagicMock(),
            checkers=CheckerRegistry(),
            dispatcher=MockDispatcher(),
            audit=audit,
        )

        run_count = 0

        async def counting_job():
            nonlocal run_count
            run_count += 1

        # 0-second interval = runs every tick
        scheduler.add_job(
            Job(name="every_tick", action=counting_job, interval_seconds=0)
        )

        await scheduler.tick()
        await scheduler.tick()
        await scheduler.tick()

        assert run_count == 3

    async def test_scheduler_start_registers_check_pipeline(self, tmp_path):
        """After start(), the built-in check_pipeline job should exist."""
        audit = AuditLog(path=tmp_path / "start_audit.jsonl")
        scheduler = Scheduler(
            config=make_config(),
            repo=MagicMock(),
            checkers=CheckerRegistry(),
            dispatcher=MockDispatcher(),
            audit=audit,
        )

        await scheduler.start()
        assert len(scheduler.jobs) == 1
        assert scheduler.jobs[0].name == "check_pipeline"
        assert scheduler.jobs[0].resource_check is False

        await scheduler.stop()


# ── Daemon E2E Tests ────────────────────────────────────────────────


class TestDaemonE2E:
    """End-to-end tests for the full daemon lifecycle.

    These tests exercise the daemon as a black box:
    - starts → runs one check cycle → outputs results → stops
    - SIGTERM triggers graceful shutdown
    - resource backoff when resources are low
    - notification dispatch with mock adapter

    All tests must complete in <30 seconds.
    """

    @pytest.fixture
    def daemon_config(self, tmp_path):
        """Config with tick_interval=1 and in-memory DBs for fast E2E."""
        return make_config(tick_interval=1)

    @pytest.fixture
    def audit_path(self, tmp_path):
        """Dedicated audit log path per test."""
        return tmp_path / "e2e_audit.jsonl"

    # ── E2E lifecycle: start → check cycle → output → stop ──────────

    async def test_daemon_full_lifecycle_single_cycle(self, daemon_config, audit_path):
        """Start daemon → run one check cycle → verify output → stop.

        This is the core acceptance criterion: the daemon starts, executes
        a tick that runs the check pipeline, produces audit output, and
        stops cleanly.
        """
        mock_repo = AsyncMock()
        mock_repo.initialize = AsyncMock()
        mock_repo.close = AsyncMock()

        audit = AuditLog(path=audit_path)
        dispatcher = MockDispatcher()
        checkers = CheckerRegistry()
        checkers.register(
            MockChecker(
                "e2e_health",
                CheckResult(name="e2e_health", status="ok", message="系统正常"),
            )
        )
        checkers.register(
            MockChecker(
                "e2e_deadman",
                CheckResult(name="e2e_deadman", status="ok", message="无卡住任务"),
            )
        )

        scheduler = Scheduler(
            config=daemon_config,
            repo=mock_repo,
            checkers=checkers,
            dispatcher=dispatcher,
            audit=audit,
        )

        # Register the default check_pipeline job (normally done by scheduler.start())
        await scheduler.start()

        # Wire daemon with pre-built components to avoid real DB paths
        daemon = SecretaryDaemon(daemon_config)
        daemon.repo = mock_repo
        daemon.scheduler = scheduler
        daemon.audit = audit
        daemon.resource_guard = MockResourceGuard(resources_ok=True)
        daemon.dispatcher = dispatcher

        # Run main_loop for a few ticks then stop via stop_event
        daemon.running = True
        daemon._started_at = datetime.now()

        async def stop_after_ticks():
            """Wait for at least one tick, then trigger shutdown."""
            while daemon._tick_count < 1:
                await asyncio.sleep(0.1)
            daemon._stop_event.set()

        stopper = asyncio.create_task(stop_after_ticks())
        await asyncio.wait_for(daemon.main_loop(), timeout=10)
        await stopper

        # Manual stop (main_loop doesn't set running=False itself)
        await daemon.stop()

        # Verify: scheduler executed at least one tick
        assert daemon._tick_count >= 1

        # Verify: audit log contains daemon lifecycle entries
        all_entries = audit.read_all()
        actions = [e.action for e in all_entries]
        assert "check_pipeline" in actions, f"Expected check_pipeline audit, got: {actions}"

        # Verify: pipeline recorded check results
        pipeline_entries = audit.query(action="check_pipeline")
        assert len(pipeline_entries) >= 1
        assert pipeline_entries[0].details["total_checks"] == 2
        assert pipeline_entries[0].details["anomalies"] == 0

        # Verify: no notifications (all checks passed)
        assert len(dispatcher.sent) == 0

    async def test_daemon_start_stop_roundtrip(self, daemon_config, audit_path):
        """daemon.start() → (runs briefly) → daemon.stop() full roundtrip.

        Patches Repository to avoid real DB connections; lets the daemon
        run its natural start → main_loop → stop lifecycle.
        """
        with patch("secretary.daemon.Repository") as MockRepo, \
             patch("secretary.daemon.HealthChecker") as MockHC, \
             patch("secretary.daemon.DeadmanChecker") as MockDC, \
             patch("secretary.daemon.Dispatcher") as MockDisp:

            mock_repo = AsyncMock()
            mock_repo.initialize = AsyncMock()
            mock_repo.close = AsyncMock()
            MockRepo.return_value = mock_repo

            mock_hc = MagicMock()
            mock_hc.name = "health"
            mock_hc.check = AsyncMock(
                return_value=CheckResult(name="health", status="ok", message="ok")
            )
            MockHC.return_value = mock_hc

            mock_dc = MagicMock()
            mock_dc.name = "deadman"
            mock_dc.check = AsyncMock(
                return_value=CheckResult(name="deadman", status="ok", message="ok")
            )
            MockDC.return_value = mock_dc

            mock_dispatcher = MockDispatcher()
            MockDisp.return_value = mock_dispatcher

            daemon = SecretaryDaemon(daemon_config)

            # Stop the daemon after 2.5 seconds (enough for 2+ ticks at 1s interval)
            async def stop_later():
                await asyncio.sleep(2.5)
                daemon._stop_event.set()

            stopper = asyncio.create_task(stop_later())

            await asyncio.wait_for(daemon.start(), timeout=10)

            await stopper

            # Daemon should have completed its full lifecycle
            assert daemon.running is False
            assert daemon._tick_count >= 1

            # Audit log should have daemon_start and daemon_stop entries
            if daemon.audit:
                start_entries = daemon.audit.query(action="daemon_start")
                stop_entries = daemon.audit.query(action="daemon_stop")
                assert len(start_entries) >= 1
                assert len(stop_entries) >= 1
                # Most recent stop entry should reflect our ticks
                assert stop_entries[-1].details["total_ticks"] >= 1

    # ── Signal handling: SIGTERM → graceful shutdown ─────────────────

    async def test_sigterm_triggers_graceful_shutdown(self, daemon_config, audit_path):
        """Sending SIGTERM should set the stop_event and trigger clean shutdown.

        We test the signal→stop_event→main_loop→stop chain by having the
        daemon register its real signal handlers on the running loop, then
        delivering SIGTERM to the current process after a brief delay.
        """
        mock_repo = AsyncMock()
        mock_repo.initialize = AsyncMock()
        mock_repo.close = AsyncMock()

        audit = AuditLog(path=audit_path)
        scheduler = Scheduler(
            config=daemon_config,
            repo=mock_repo,
            checkers=CheckerRegistry(),
            dispatcher=MockDispatcher(),
            audit=audit,
        )

        daemon = SecretaryDaemon(daemon_config)
        daemon.repo = mock_repo
        daemon.scheduler = scheduler
        daemon.audit = audit
        daemon.resource_guard = MockResourceGuard(resources_ok=True)
        daemon.running = True
        daemon._started_at = datetime.now()

        # Register the real signal handlers on the running loop
        daemon._register_signal_handlers()

        # Send SIGTERM to ourselves after 1.5s (enough for 1+ tick)
        async def send_sigterm():
            await asyncio.sleep(1.5)
            import os
            os.kill(os.getpid(), signal.SIGTERM)

        sigterm_task = asyncio.create_task(send_sigterm())

        await asyncio.wait_for(daemon.main_loop(), timeout=10)

        await sigterm_task

        # main_loop exits when stop_event is set; call stop() for cleanup
        await daemon.stop()

        # Daemon should have shut down cleanly
        assert daemon.running is False
        assert daemon._tick_count >= 1

        # Verify audit captured the tick(s)
        all_entries = audit.read_all()
        assert len(all_entries) >= 1

    # ── Resource backoff: low resources → backoff → recover ─────────

    async def test_resource_backoff_blocks_scheduler(self, daemon_config, audit_path):
        """When resources are low, daemon should back off and NOT tick scheduler.

        Resources start low → daemon enters backoff (calls wait_for_resources).
        Then resources recover → daemon proceeds with normal ticks.
        """
        mock_repo = AsyncMock()
        audit = AuditLog(path=audit_path)

        mock_resource_guard = MockResourceGuard(resources_ok=False)
        mock_resource_guard.wait_for_resources = AsyncMock()

        scheduler = Scheduler(
            config=daemon_config,
            repo=mock_repo,
            checkers=CheckerRegistry(),
            dispatcher=MockDispatcher(),
            audit=audit,
        )
        mock_tick = AsyncMock(return_value=TickResult())
        scheduler.tick = mock_tick

        daemon = SecretaryDaemon(daemon_config)
        daemon.repo = mock_repo
        daemon.scheduler = scheduler
        daemon.audit = audit
        daemon.resource_guard = mock_resource_guard

        tick_calls = 0
        original_check = mock_resource_guard.check_resources

        async def transitioning_guard():
            """Resources are low for first 2 checks, then recover."""
            nonlocal tick_calls
            tick_calls += 1
            if tick_calls <= 2:
                return False
            # Recover — stop daemon after this
            daemon.running = False
            return True

        mock_resource_guard.check_resources = transitioning_guard
        daemon.running = True
        daemon._started_at = datetime.now()

        await asyncio.wait_for(daemon.main_loop(), timeout=10)

        # Resources were low for 2 checks → wait_for_resources called twice
        assert mock_resource_guard.wait_for_resources.await_count == 2

        # Scheduler tick should have been called once (after recovery)
        assert mock_tick.await_count == 1

    async def test_resource_guard_exponential_backoff(self, tmp_path):
        """ResourceGuard.wait_for_resources() should use exponential backoff.

        backoff_initial=1 → 1s, then 1*2=2s, then 2*2=4s.
        We mock asyncio.sleep to verify the sequence without real waits.
        """
        from secretary.config import ResourceGuardConfig
        from secretary.engine.audit import AuditLog
        from secretary.monitor.resource_guard import ResourceGuard

        config = ResourceGuardConfig(
            backoff_initial=1,
            backoff_multiplier=2,
            backoff_max=10,
        )
        audit = AuditLog(path=tmp_path / "backoff_audit.jsonl")

        with patch("secretary.monitor.resource_guard.psutil") as mock_psutil:
            # Mock psutil to always report high resource usage
            mock_mem = MagicMock()
            mock_mem.percent = 98.0
            mock_psutil.virtual_memory.return_value = mock_mem
            mock_psutil.cpu_percent.return_value = 97.0
            mock_disk = MagicMock()
            mock_disk.percent = 96.0
            mock_psutil.disk_usage.return_value = mock_disk

            guard = ResourceGuard(config, audit)

            # Patch asyncio.sleep to capture sleep durations
            sleep_durations = []

            async def fake_sleep(seconds):
                sleep_durations.append(seconds)

            with patch("asyncio.sleep", side_effect=fake_sleep):

                # 3 backoff rounds
                for _ in range(3):
                    ok = await guard.check_resources()
                    assert ok is False
                    await guard.wait_for_resources()

            # Exponential: 1 → 2 → 4
            assert sleep_durations == [1, 2, 4]
            assert guard.backoff_seconds == 4

            # Audit should have recorded each backoff
            backoff_entries = audit.query(action="resource_backoff")
            assert len(backoff_entries) == 3

    # ── Notification dispatch with mock adapter ─────────────────────

    async def test_notification_dispatch_on_anomaly(self, daemon_config, audit_path):
        """When checks detect anomalies, notifications should be dispatched.

        Sets up a checker that returns CRITICAL, runs a scheduler tick,
        and verifies the mock dispatcher received the notification.
        """
        audit = AuditLog(path=audit_path)
        dispatcher = MockDispatcher()
        checkers = CheckerRegistry()

        checkers.register(
            MockChecker(
                "disk_check",
                CheckResult(
                    name="disk_check",
                    status="critical",
                    message="磁盘使用率 98%，超过阈值 95%",
                ),
            )
        )
        checkers.register(
            MockChecker(
                "memory_check",
                CheckResult(
                    name="memory_check",
                    status="warning",
                    message="内存使用率 92%",
                ),
            )
        )

        scheduler = Scheduler(
            config=daemon_config,
            repo=MagicMock(),
            checkers=checkers,
            dispatcher=dispatcher,
            audit=audit,
            resource_guard=MockResourceGuard(resources_ok=True),
        )

        # Run the check pipeline
        results = await scheduler.run_check_pipeline()

        # Both checks ran
        assert len(results) == 2

        # Auto-repairer intercepts CRITICAL disk → sends WARNING (repairing) + INFO (repaired)
        # memory_check WARNING passes through unchanged
        assert len(dispatcher.sent) >= 2

        levels = {n.level for n in dispatcher.sent}
        # With auto-repair, CRITICAL is consumed → at least WARNING present
        assert NotificationLevel.WARNING in levels

        # Verify notification content includes repair actions or checker names
        titles = [n.title for n in dispatcher.sent]
        all_text = " ".join(titles)
        assert "修复" in all_text or "清理" in all_text or "disk" in all_text.lower()
        assert "memory" in all_text.lower()

        # Verify body contains repair or error messages
        bodies = [n.body for n in dispatcher.sent]
        all_bodies = " ".join(bodies)
        # With auto-repair, disk body is replaced by repair message
        assert "清理" in all_bodies or "修复" in all_bodies or "98%" in all_bodies
        assert "92%" in all_bodies

        # Audit should record anomalies (at least disk + memory, repair may add more)
        anomaly_entries = audit.query(action="check_anomaly")
        assert len(anomaly_entries) >= 2

    async def test_notification_dispatch_on_job_failure(self, daemon_config, audit_path):
        """When a job fails after all retries, CRITICAL notification is dispatched."""
        audit = AuditLog(path=audit_path)
        dispatcher = MockDispatcher()

        scheduler = Scheduler(
            config=daemon_config,
            repo=MagicMock(),
            checkers=CheckerRegistry(),
            dispatcher=dispatcher,
            audit=audit,
            resource_guard=MockResourceGuard(resources_ok=True),
        )

        async def always_fail():
            raise ConnectionError("网络不可达")

        scheduler.add_job(
            Job(name="net_check", action=always_fail, interval_seconds=0, max_retries=1)
        )

        result = await scheduler.tick()

        assert result.jobs_failed == 1
        assert result.jobs_executed == 0

        # One CRITICAL notification dispatched
        assert len(dispatcher.sent) == 1
        notif = dispatcher.sent[0]
        assert notif.level == NotificationLevel.CRITICAL
        assert "net_check" in notif.title

        # Audit logged the failure
        fail_entries = audit.query(action="job:net_check")
        assert len(fail_entries) == 1
        assert fail_entries[0].result == "failed"

    async def test_no_notification_when_all_ok(self, daemon_config, audit_path):
        """When all checks pass, no notifications should be dispatched."""
        audit = AuditLog(path=audit_path)
        dispatcher = MockDispatcher()
        checkers = CheckerRegistry()

        checkers.register(
            MockChecker("ok1", CheckResult(name="ok1", status="ok", message="正常"))
        )
        checkers.register(
            MockChecker("ok2", CheckResult(name="ok2", status="ok", message="正常"))
        )

        scheduler = Scheduler(
            config=daemon_config,
            repo=MagicMock(),
            checkers=checkers,
            dispatcher=dispatcher,
            audit=audit,
        )

        results = await scheduler.run_check_pipeline()
        assert len(results) == 2
        assert all(r.status == "ok" for r in results)

        # No notifications
        assert len(dispatcher.sent) == 0

        # Pipeline still audit-logged
        pipeline_entries = audit.query(action="check_pipeline")
        assert len(pipeline_entries) == 1
        assert pipeline_entries[0].details["anomalies"] == 0

    # ── All E2E tests complete in <30s ──────────────────────────────

    async def test_e2e_completes_within_timeout(self, daemon_config, audit_path):
        """Meta-test: the full E2E lifecycle must complete in <30 seconds.

        Runs start→one tick→stop with a hard timeout.  If this test
        itself takes >30s the pytest timeout will fail it.
        """
        import time

        start_time = time.monotonic()

        with patch("secretary.daemon.Repository") as MockRepo, \
             patch("secretary.daemon.HealthChecker") as MockHC, \
             patch("secretary.daemon.DeadmanChecker") as MockDC, \
             patch("secretary.daemon.Dispatcher") as MockDisp:

            mock_repo = AsyncMock()
            mock_repo.initialize = AsyncMock()
            mock_repo.close = AsyncMock()
            MockRepo.return_value = mock_repo

            mock_hc = MagicMock()
            mock_hc.name = "health"
            mock_hc.check = AsyncMock(
                return_value=CheckResult(name="health", status="ok", message="ok")
            )
            MockHC.return_value = mock_hc

            mock_dc = MagicMock()
            mock_dc.name = "deadman"
            mock_dc.check = AsyncMock(
                return_value=CheckResult(name="deadman", status="ok", message="ok")
            )
            MockDC.return_value = mock_dc

            MockDisp.return_value = MockDispatcher()

            daemon = SecretaryDaemon(daemon_config)

            async def stop_after_one_tick():
                """Wait for at least one tick, then signal stop."""
                while daemon._tick_count < 1:
                    await asyncio.sleep(0.1)
                daemon._stop_event.set()

            stopper = asyncio.create_task(stop_after_one_tick())

            await asyncio.wait_for(daemon.start(), timeout=15)

            await stopper

            elapsed = time.monotonic() - start_time
            assert elapsed < 30.0, f"E2E took {elapsed:.1f}s, must be <30s"
            assert daemon._tick_count >= 1
