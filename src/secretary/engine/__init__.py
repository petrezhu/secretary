"""Engine layer — scheduler, triggers, rules, audit."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from secretary.engine.repairer import AutoRepairer
from secretary.monitor import CheckResult

if TYPE_CHECKING:
    from secretary.notify import Dispatcher

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """Scheduled task unit."""

    name: str
    action: Callable[..., Awaitable[Any]]
    interval_seconds: int
    resource_check: bool = True
    max_retries: int = 3
    last_run: datetime | None = None
    enabled: bool = True


@dataclass
class TickResult:
    """Result of a single scheduler tick cycle."""

    jobs_executed: int = 0
    jobs_skipped: int = 0
    jobs_failed: int = 0
    checks_run: int = 0
    anomalies_detected: int = 0
    notifications_sent: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


class Scheduler:
    """Internal task scheduler with full pipeline integration.

    Manages periodic jobs with:
    - Per-job resource guard checks
    - Retry logic with configurable max_retries
    - Audit logging for every job execution
    - Built-in check→anomaly→notify→audit pipeline
    """

    def __init__(
        self,
        config: Any,
        repo: Any,
        checkers: Any,
        dispatcher: Dispatcher,
        audit: Any,
        resource_guard: Any | None = None,
        auto_repairer: AutoRepairer | None = None,
    ):
        self.config = config
        self.repo = repo
        self.checkers = checkers
        self.dispatcher = dispatcher
        self.audit = audit
        self.resource_guard = resource_guard
        self.auto_repairer = auto_repairer or AutoRepairer(audit=audit, dispatcher=dispatcher)
        self._jobs: list[Job] = []
        self._running = False
        self._tick_count = 0
        self._notify_cooldown: dict[str, datetime] = {}
        self._notify_cooldown_seconds: int = 3600  # 1 hour default

    # ── Job management ────────────────────────────────────────────────

    def add_job(self, job: Job) -> None:
        """Register a job with the scheduler."""
        self._jobs.append(job)
        logger.info("Registered job: %s (every %ds)", job.name, job.interval_seconds)

    def remove_job(self, name: str) -> bool:
        """Remove a job by name. Returns True if found."""
        for i, job in enumerate(self._jobs):
            if job.name == name:
                self._jobs.pop(i)
                logger.info("Removed job: %s", name)
                return True
        return False

    @property
    def jobs(self) -> list[Job]:
        """Return a copy of registered jobs."""
        return list(self._jobs)

    # ── Pipeline: check → anomaly → notify → audit ───────────────────

    async def run_check_pipeline(self) -> list[CheckResult]:
        """Execute the full monitoring pipeline.

        1. Run all registered checkers
        2. Detect anomalies (warning/critical results)
        3. Send notifications for anomalies
        4. Audit log everything

        Returns the list of CheckResults.
        """
        logger.info("Running check pipeline...")

        # Step 1: Run all checkers
        results = await self.checkers.run_all(self.repo)
        logger.info("Check pipeline: %d checks completed", len(results))

        # Step 2+3+4: Detect anomalies → auto-repair → notify → audit
        anomalies = [r for r in results if r.status != "ok"]
        for result in anomalies:
            # Audit each anomaly
            await self.audit.log(
                action="check_anomaly",
                target=result.name,
                details={
                    "status": result.status,
                    "message": result.message,
                    "check_details": result.details,
                },
                result="detected",
            )

            # Step 2.5: Attempt auto-repair
            repair_action = None
            if self.auto_repairer is not None:
                try:
                    repair_action = await self.auto_repairer.handle_anomaly(result)
                    if repair_action:
                        logger.info(
                            "Auto-repair executed for %s: %s",
                            result.name,
                            repair_action.action_type,
                        )
                except Exception:
                    logger.exception("Auto-repair failed for %s", result.name)

            # Step 3: Notify (skip if auto-repair already notified)
            if repair_action is None:
                # Check notification cooldown
                last_notified = self._notify_cooldown.get(result.name)
                if last_notified is not None:
                    since_last = (datetime.now() - last_notified).total_seconds()
                    if since_last < self._notify_cooldown_seconds:
                        logger.info(
                            "Notification for %s skipped (cooldown %.0fs remaining)",
                            result.name,
                            self._notify_cooldown_seconds - since_last,
                        )
                        continue
                try:
                    from secretary.notify import Notification, NotificationLevel

                    level_map = {
                        "warning": NotificationLevel.WARNING,
                        "critical": NotificationLevel.CRITICAL,
                    }
                    notification = Notification(
                        level=level_map.get(result.status, NotificationLevel.INFO),
                        title=f"检查异常: {result.name}",
                        body=result.message,
                    )
                    send_result = await self.dispatcher.send(notification)
                    if send_result.success:
                        logger.info("Notification sent for %s", result.name)
                        self._notify_cooldown[result.name] = datetime.now()
                    else:
                        logger.warning(
                            "Notification failed for %s: %s",
                            result.name,
                            send_result.error,
                        )
                except Exception:
                    logger.exception("Failed to send notification for %s", result.name)

        # Step 4: Audit the pipeline run itself
        await self.audit.log(
            action="check_pipeline",
            target="scheduler",
            details={
                "total_checks": len(results),
                "anomalies": len(anomalies),
                "statuses": {r.name: r.status for r in results},
            },
            result="success" if not anomalies else f"{len(anomalies)} anomalies",
        )

        logger.info(
            "Check pipeline complete: %d checks, %d anomalies",
            len(results),
            len(anomalies),
        )
        return results

    # ── Tick ──────────────────────────────────────────────────────────

    async def tick(self) -> TickResult:
        """Execute any jobs that are due.

        For each due job:
        1. Check resource guard (if job.resource_check and guard available)
        2. Execute with retry logic
        3. Audit log success/failure
        4. Notify on final failure

        Returns a TickResult summary.
        """
        self._tick_count += 1
        now = datetime.now()
        result = TickResult(timestamp=now)

        for job in self._jobs:
            if not job.enabled:
                continue

            # Check if job is due
            if job.last_run is not None:
                elapsed = (now - job.last_run).total_seconds()
                if elapsed < job.interval_seconds:
                    continue

            # Per-job resource guard check
            if job.resource_check and self.resource_guard is not None:
                try:
                    if not await self.resource_guard.check_resources():
                        logger.warning("Job %s skipped: insufficient resources", job.name)
                        result.jobs_skipped += 1
                        await self.audit.log(
                            action=f"job:{job.name}",
                            target="scheduler",
                            details={"reason": "resource_guard_blocked"},
                            result="skipped",
                        )
                        continue
                except Exception:
                    logger.exception("Resource guard check failed for job %s", job.name)

            # Execute with retry
            executed = False
            for attempt in range(1, job.max_retries + 1):
                try:
                    logger.debug(
                        "Executing job %s (attempt %d/%d)",
                        job.name,
                        attempt,
                        job.max_retries,
                    )
                    await job.action()
                    job.last_run = now
                    result.jobs_executed += 1
                    executed = True

                    await self.audit.log(
                        action=f"job:{job.name}",
                        target="scheduler",
                        details={"attempt": attempt},
                        result="success",
                    )
                    break
                except Exception as exc:
                    logger.warning(
                        "Job %s failed (attempt %d/%d): %s",
                        job.name,
                        attempt,
                        job.max_retries,
                        exc,
                    )
                    if attempt < job.max_retries:
                        # Brief pause before retry
                        await asyncio.sleep(min(attempt * 0.5, 5.0))

            if not executed:
                result.jobs_failed += 1
                logger.error("Job %s failed after %d attempts", job.name, job.max_retries)
                await self.audit.log(
                    action=f"job:{job.name}",
                    target="scheduler",
                    details={"max_retries": job.max_retries},
                    result="failed",
                )

                # Notify on final failure
                try:
                    from secretary.notify import Notification, NotificationLevel

                    await self.dispatcher.send(
                        Notification(
                            level=NotificationLevel.CRITICAL,
                            title=f"任务失败: {job.name}",
                            body=f"任务 {job.name} 在 {job.max_retries} 次重试后仍然失败",
                        )
                    )
                except Exception:
                    logger.exception("Failed to send failure notification for %s", job.name)

        return result

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the scheduler and register default pipeline job."""
        self._running = True
        # Register the built-in check pipeline as a scheduled job
        check_interval = getattr(self.config, "check_interval", 300)
        self.add_job(
            Job(
                name="check_pipeline",
                action=self.run_check_pipeline,
                interval_seconds=check_interval,
                resource_check=False,  # Pipeline has its own resource-awareness
            )
        )
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        logger.info("Scheduler stopped after %d ticks", self._tick_count)
