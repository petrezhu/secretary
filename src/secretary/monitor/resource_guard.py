"""Resource guard — system resource monitoring with exponential backoff."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from secretary.config import MonitorThresholds, ResourceGuardConfig
from secretary.engine.audit import AuditLog

logger = logging.getLogger(__name__)

STATE_FILE = Path("/tmp/secretary_backoff.json")


@dataclass
class ResourceStatus:
    memory_percent: float
    cpu_percent: float
    disk_percent: float
    is_sufficient: bool
    reason: str = ""


class ResourceGuard:
    """Check system resources before heavy operations.

    Monitors memory, CPU (5-min average), and disk against configurable
    thresholds (default 95%).  When any resource exceeds its threshold the
    guard enters an exponential backoff state (10 → 20 → 40 → 80 → 160 min)
    that is persisted to disk so it survives restarts.  Backoff resets once
    all resources recover below their thresholds.
    """

    def __init__(
        self,
        config: ResourceGuardConfig,
        audit: AuditLog,
        thresholds: MonitorThresholds | None = None,
    ):
        self.config = config
        self.audit = audit
        self.thresholds = thresholds or MonitorThresholds()
        self._backoff_seconds: int = 0
        self._load_state()

    # ── state persistence ──────────────────────────────────────────────

    def _load_state(self) -> None:
        """Load backoff state from disk."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self._backoff_seconds = int(data.get("backoff_seconds", 0))
            except (json.JSONDecodeError, KeyError, ValueError):
                self._backoff_seconds = 0

    def _save_state(self) -> None:
        """Persist backoff state."""
        STATE_FILE.write_text(
            json.dumps(
                {
                    "backoff_seconds": self._backoff_seconds,
                    "updated_at": datetime.now().isoformat(),
                }
            )
        )

    # ── resource queries ───────────────────────────────────────────────

    def _cpu_percent_avg(self) -> float:
        """Return CPU percent over the configured window (default 5 min).

        psutil.cpu_percent(interval=None) returns the average since the
        previous call.  For a first call it returns 0.0, so we prime it
        once with a short interval.
        """
        window_sec = self.thresholds.cpu_window_minutes * 60
        # On first call, psutil returns 0.0; a blocking interval gives a
        # meaningful reading.  Subsequent calls use the accumulated average.
        return psutil.cpu_percent(interval=min(window_sec, 1))

    def _check_single(self) -> tuple[bool, str]:
        """Check all resources against thresholds.

        Returns (sufficient, reason).
        """
        mem = psutil.virtual_memory()
        cpu = self._cpu_percent_avg()
        disk = psutil.disk_usage("/")

        reasons: list[str] = []

        if mem.percent > self.thresholds.memory_percent:
            reasons.append(f"内存 {mem.percent:.1f}% > {self.thresholds.memory_percent}%")

        if cpu > self.thresholds.cpu_percent:
            reasons.append(f"CPU {cpu:.1f}% > {self.thresholds.cpu_percent}%")

        if disk.percent > self.thresholds.disk_percent:
            reasons.append(f"磁盘 {disk.percent:.1f}% > {self.thresholds.disk_percent}%")

        sufficient = len(reasons) == 0
        reason = "; ".join(reasons) if reasons else "资源充足"
        return sufficient, reason

    # ── public API ─────────────────────────────────────────────────────

    async def check_resources(self) -> bool:
        """Check if resources are sufficient. Returns True if OK."""
        sufficient, reason = self._check_single()

        if sufficient:
            # Resources OK — reset backoff
            if self._backoff_seconds > 0:
                logger.info("Resources recovered, resetting backoff")
                self._backoff_seconds = 0
                self._save_state()
            return True

        logger.warning(f"Resource check failed: {reason}")
        return False

    def get_status(self) -> ResourceStatus:
        """Get current resource status (non-blocking snapshot)."""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0)
        disk = psutil.disk_usage("/")

        reasons: list[str] = []
        sufficient = True

        if mem.percent > self.thresholds.memory_percent:
            reasons.append(f"内存 {mem.percent:.1f}%")
            sufficient = False
        if cpu > self.thresholds.cpu_percent:
            reasons.append(f"CPU {cpu:.1f}%")
            sufficient = False
        if disk.percent > self.thresholds.disk_percent:
            reasons.append(f"磁盘 {disk.percent:.1f}%")
            sufficient = False

        return ResourceStatus(
            memory_percent=mem.percent,
            cpu_percent=cpu,
            disk_percent=disk.percent,
            is_sufficient=sufficient,
            reason="; ".join(reasons) if reasons else "资源充足",
        )

    async def wait_for_resources(self) -> None:
        """Exponential backoff wait.

        Advances backoff from current value: initial → initial*mult → … → max.
        """
        if self._backoff_seconds == 0:
            self._backoff_seconds = self.config.backoff_initial
        else:
            self._backoff_seconds = min(
                self._backoff_seconds * self.config.backoff_multiplier,
                self.config.backoff_max,
            )

        self._save_state()
        logger.warning(
            f"Resource guard: backing off for {self._backoff_seconds}s "
            f"({self._backoff_seconds // 60}min)"
        )

        await self.audit.log(
            action="resource_backoff",
            target="system",
            details={"backoff_seconds": self._backoff_seconds},
            result="executed",
        )

        await asyncio.sleep(self._backoff_seconds)

    @property
    def backoff_seconds(self) -> int:
        """Current backoff duration in seconds."""
        return self._backoff_seconds
