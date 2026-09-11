"""Unit tests for Scheduler notification dedup."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from secretary.engine import Scheduler
from secretary.engine.repairer import AutoRepairer
from secretary.monitor import CheckResult


def _make_scheduler(tmp_path) -> Scheduler:
    """Create a Scheduler with mocked dependencies."""
    config = MagicMock()
    config.check_interval = 300
    repo = MagicMock()
    checkers = MagicMock()
    checkers.run_all = AsyncMock(return_value=[])
    dispatcher = MagicMock()
    dispatcher.send = AsyncMock(return_value=MagicMock(success=True, error=None))
    audit = MagicMock()
    audit.log = AsyncMock()
    resource_guard = MagicMock()
    resource_guard.check_resources = AsyncMock(return_value=True)
    auto_repairer = MagicMock(spec=AutoRepairer)
    auto_repairer.handle_anomaly = AsyncMock(return_value=None)

    return Scheduler(
        config=config,
        repo=repo,
        checkers=checkers,
        dispatcher=dispatcher,
        audit=audit,
        resource_guard=resource_guard,
        auto_repairer=auto_repairer,
    )


class TestNotificationDedup:
    """Notifications should be deduped within the cooldown window."""

    @pytest.mark.asyncio
    async def test_first_notification_sent(self, tmp_path):
        """First anomaly notification should be sent."""
        scheduler = _make_scheduler(tmp_path)
        anomaly = CheckResult(
            name="health",
            status="warning",
            message="test anomaly",
        )
        scheduler.checkers.run_all = AsyncMock(return_value=[anomaly])

        await scheduler.run_check_pipeline()

        scheduler.dispatcher.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_notification_skipped(self, tmp_path):
        """Second identical anomaly within cooldown should be skipped."""
        scheduler = _make_scheduler(tmp_path)
        anomaly = CheckResult(
            name="health",
            status="warning",
            message="test anomaly",
        )
        scheduler.checkers.run_all = AsyncMock(return_value=[anomaly])

        # First tick — should notify
        await scheduler.run_check_pipeline()
        assert scheduler.dispatcher.send.call_count == 1

        # Second tick immediately — should NOT notify (cooldown)
        await scheduler.run_check_pipeline()
        assert scheduler.dispatcher.send.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_notification_after_cooldown_expires(self, tmp_path):
        """After cooldown expires, notification should be sent again."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._notify_cooldown_seconds = 0  # instant expiry for test

        anomaly = CheckResult(
            name="health",
            status="warning",
            message="test anomaly",
        )
        scheduler.checkers.run_all = AsyncMock(return_value=[anomaly])

        # First tick
        await scheduler.run_check_pipeline()
        assert scheduler.dispatcher.send.call_count == 1

        # Second tick — cooldown expired (0s), should notify again
        await scheduler.run_check_pipeline()
        assert scheduler.dispatcher.send.call_count == 2

    @pytest.mark.asyncio
    async def test_different_anomalies_independent(self, tmp_path):
        """Different anomaly names should have independent cooldowns."""
        scheduler = _make_scheduler(tmp_path)
        anomaly_health = CheckResult(name="health", status="warning", message="health issue")
        anomaly_deadman = CheckResult(name="deadman", status="warning", message="deadman issue")
        scheduler.checkers.run_all = AsyncMock(return_value=[anomaly_health, anomaly_deadman])

        await scheduler.run_check_pipeline()
        assert scheduler.dispatcher.send.call_count == 2

    @pytest.mark.asyncio
    async def test_cooldown_state_stored(self, tmp_path):
        """Cooldown dict should be populated after successful notification."""
        scheduler = _make_scheduler(tmp_path)
        anomaly = CheckResult(name="health", status="warning", message="test")
        scheduler.checkers.run_all = AsyncMock(return_value=[anomaly])

        assert "health" not in scheduler._notify_cooldown
        await scheduler.run_check_pipeline()
        assert "health" in scheduler._notify_cooldown

    @pytest.mark.asyncio
    async def test_cooldown_tracks_time(self, tmp_path):
        """Cooldown should store the time of last notification."""
        scheduler = _make_scheduler(tmp_path)
        anomaly = CheckResult(name="health", status="warning", message="test")
        scheduler.checkers.run_all = AsyncMock(return_value=[anomaly])

        before = datetime.now()
        await scheduler.run_check_pipeline()
        after = datetime.now()

        last = scheduler._notify_cooldown["health"]
        assert before <= last <= after
