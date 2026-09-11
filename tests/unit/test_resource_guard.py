"""Unit tests for ResourceGuard — mocked psutil."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secretary.config import MonitorThresholds, ResourceGuardConfig
from secretary.monitor.resource_guard import STATE_FILE, ResourceGuard, ResourceStatus

# ── helpers ────────────────────────────────────────────────────────────


def _mem(pct: float) -> SimpleNamespace:
    return SimpleNamespace(percent=pct)


def _disk(pct: float) -> SimpleNamespace:
    return SimpleNamespace(percent=pct)


def _make_guard(
    mem_pct: float = 50.0,
    cpu_pct: float = 30.0,
    disk_pct: float = 60.0,
    state_file_exists: bool = False,
    backoff_initial: int = 600,
    backoff_max: int = 9600,
) -> tuple[ResourceGuard, MagicMock]:
    """Build a ResourceGuard with mocked psutil and audit."""
    config = ResourceGuardConfig(
        backoff_initial=backoff_initial,
        backoff_multiplier=2,
        backoff_max=backoff_max,
    )
    thresholds = MonitorThresholds()
    audit = AsyncMock()

    # Clean state file
    if STATE_FILE.exists():
        STATE_FILE.unlink()

    guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

    # Patch psutil calls
    with patch("secretary.monitor.resource_guard.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = _mem(mem_pct)
        mock_psutil.cpu_percent.return_value = cpu_pct
        mock_psutil.disk_usage.return_value = _disk(disk_pct)
        yield guard, mock_psutil, audit


# ── tests ──────────────────────────────────────────────────────────────


class TestCheckResources:
    """check_resources returns True when all resources are below threshold."""

    @pytest.mark.asyncio
    async def test_all_ok(self):
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(50.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(60.0)
            result = await guard.check_resources()

        assert result is True

    @pytest.mark.asyncio
    async def test_memory_high(self):
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(96.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(60.0)
            result = await guard.check_resources()

        assert result is False

    @pytest.mark.asyncio
    async def test_cpu_high(self):
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(50.0)
            mp.cpu_percent.return_value = 98.0
            mp.disk_usage.return_value = _disk(60.0)
            result = await guard.check_resources()

        assert result is False

    @pytest.mark.asyncio
    async def test_disk_high(self):
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(50.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(97.0)
            result = await guard.check_resources()

        assert result is False

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_is_ok(self):
        """95.0 is not > 95, so it's still OK."""
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(95.0)
            mp.cpu_percent.return_value = 95.0
            mp.disk_usage.return_value = _disk(95.0)
            result = await guard.check_resources()

        assert result is True

    @pytest.mark.asyncio
    async def test_backoff_reset_on_recovery(self):
        """Backoff resets when resources recover."""
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)
        guard._backoff_seconds = 1200

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(50.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(60.0)
            result = await guard.check_resources()

        assert result is True
        assert guard.backoff_seconds == 0


class TestGetStatus:
    """get_status returns a ResourceStatus snapshot."""

    def test_all_ok(self):
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(50.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(60.0)
            status = guard.get_status()

        assert isinstance(status, ResourceStatus)
        assert status.is_sufficient is True
        assert status.memory_percent == 50.0
        assert status.cpu_percent == 30.0
        assert status.disk_percent == 60.0
        assert "资源充足" in status.reason

    def test_multiple_high(self):
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(96.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(97.0)
            status = guard.get_status()

        assert status.is_sufficient is False
        assert "内存" in status.reason
        assert "磁盘" in status.reason

    def test_cpu_in_status(self):
        """get_status includes CPU in the report."""
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(50.0)
            mp.cpu_percent.return_value = 99.0
            mp.disk_usage.return_value = _disk(60.0)
            status = guard.get_status()

        assert status.is_sufficient is False
        assert "CPU" in status.reason


class TestWaitForResources:
    """Exponential backoff: 600 → 1200 → 2400 → 4800 → 9600 → 9600."""

    @pytest.mark.asyncio
    async def test_first_backoff_is_initial(self):
        config = ResourceGuardConfig(backoff_initial=600, backoff_multiplier=2, backoff_max=9600)
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit)

        with patch("secretary.monitor.resource_guard.asyncio") as mock_asyncio:
            mock_asyncio.sleep = AsyncMock()
            await guard.wait_for_resources()

        assert guard.backoff_seconds == 600
        mock_asyncio.sleep.assert_awaited_once_with(600)

    @pytest.mark.asyncio
    async def test_second_backoff_doubles(self):
        config = ResourceGuardConfig(backoff_initial=600, backoff_multiplier=2, backoff_max=9600)
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit)
        guard._backoff_seconds = 600

        with patch("secretary.monitor.resource_guard.asyncio") as mock_asyncio:
            mock_asyncio.sleep = AsyncMock()
            await guard.wait_for_resources()

        assert guard.backoff_seconds == 1200

    @pytest.mark.asyncio
    async def test_backoff_caps_at_max(self):
        config = ResourceGuardConfig(backoff_initial=600, backoff_multiplier=2, backoff_max=9600)
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit)
        guard._backoff_seconds = 9600

        with patch("secretary.monitor.resource_guard.asyncio") as mock_asyncio:
            mock_asyncio.sleep = AsyncMock()
            await guard.wait_for_resources()

        assert guard.backoff_seconds == 9600  # capped, not 19200

    @pytest.mark.asyncio
    async def test_full_backoff_sequence(self):
        """Sequence: 600 → 1200 → 2400 → 4800 → 9600 → 9600."""
        config = ResourceGuardConfig(backoff_initial=600, backoff_multiplier=2, backoff_max=9600)
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit)

        expected = [600, 1200, 2400, 4800, 9600, 9600]
        for exp in expected:
            with patch("secretary.monitor.resource_guard.asyncio") as mock_asyncio:
                mock_asyncio.sleep = AsyncMock()
                await guard.wait_for_resources()
            assert guard.backoff_seconds == exp, f"Expected {exp}, got {guard.backoff_seconds}"

    @pytest.mark.asyncio
    async def test_audit_called_on_backoff(self):
        config = ResourceGuardConfig()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit)

        with patch("secretary.monitor.resource_guard.asyncio") as mock_asyncio:
            mock_asyncio.sleep = AsyncMock()
            await guard.wait_for_resources()

        audit.log.assert_awaited_once()
        call_kwargs = audit.log.call_args
        assert call_kwargs.kwargs["action"] == "resource_backoff"


class TestStatePersistence:
    """Backoff state persists across instances."""

    @pytest.mark.asyncio
    async def test_save_and_load(self):
        config = ResourceGuardConfig()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()

        # Create guard, advance backoff
        guard1 = ResourceGuard(config=config, audit=audit)
        guard1._backoff_seconds = 2400
        guard1._save_state()

        # New guard should load the persisted state
        guard2 = ResourceGuard(config=config, audit=audit)
        assert guard2.backoff_seconds == 2400

        # Cleanup
        STATE_FILE.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_corrupt_state_file_resets(self):
        config = ResourceGuardConfig()
        audit = AsyncMock()

        STATE_FILE.write_text("not valid json!!!")
        guard = ResourceGuard(config=config, audit=audit)
        assert guard.backoff_seconds == 0

        STATE_FILE.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_reset_persists_zero(self):
        """When backoff resets, zero is persisted."""
        config = ResourceGuardConfig()
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()

        guard = ResourceGuard(config=config, audit=audit)
        guard._backoff_seconds = 1200
        guard._save_state()

        # Simulate recovery
        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(50.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(60.0)
            await guard.check_resources()

        # Verify file was updated with 0
        data = json.loads(STATE_FILE.read_text())
        assert data["backoff_seconds"] == 0
        STATE_FILE.unlink(missing_ok=True)


class TestThresholds:
    """Custom thresholds are respected."""

    @pytest.mark.asyncio
    async def test_custom_threshold_allows_lower(self):
        config = ResourceGuardConfig()
        thresholds = MonitorThresholds(memory_percent=80.0)
        audit = AsyncMock()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        guard = ResourceGuard(config=config, audit=audit, thresholds=thresholds)

        with patch("secretary.monitor.resource_guard.psutil") as mp:
            mp.virtual_memory.return_value = _mem(85.0)
            mp.cpu_percent.return_value = 30.0
            mp.disk_usage.return_value = _disk(60.0)
            result = await guard.check_resources()

        assert result is False  # 85 > 80 custom threshold
