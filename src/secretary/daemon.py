"""Secretary daemon — main event loop and lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime

from secretary.config import Config
from secretary.data.repository import Repository
from secretary.engine import Scheduler
from secretary.engine.audit import AuditLog
from secretary.monitor import CheckerRegistry
from secretary.monitor.deadman import DeadmanChecker
from secretary.monitor.health import HealthChecker
from secretary.monitor.resource_guard import ResourceGuard
from secretary.notify import Dispatcher, HermesQQAdapter

logger = logging.getLogger(__name__)


class SecretaryDaemon:
    """Main daemon process with graceful lifecycle management.

    Orchestrates all components:
    - Repository (data layer)
    - Scheduler (job execution + check pipeline)
    - CheckerRegistry (health/deadman checks)
    - Dispatcher (notifications)
    - ResourceGuard (system resource monitoring)
    - AuditLog (operation recording)
    """

    def __init__(self, config: Config):
        self.config = config
        self.running = False
        self._started_at: datetime | None = None
        self._stop_event = asyncio.Event()
        self._tick_count = 0

        # Components (initialized in start())
        self.repo: Repository | None = None
        self.scheduler: Scheduler | None = None
        self.audit: AuditLog | None = None
        self.checkers: CheckerRegistry | None = None
        self.resource_guard: ResourceGuard | None = None
        self.dispatcher: Dispatcher | None = None

    @property
    def uptime_seconds(self) -> float:
        """Return uptime in seconds since start."""
        if self._started_at is None:
            return 0.0
        return (datetime.now() - self._started_at).total_seconds()

    def _register_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers for graceful shutdown.

        Uses asyncio Event to ensure stop() is only called once even if
        multiple signals arrive.
        """
        loop = asyncio.get_running_loop()

        def _signal_handler(sig: signal.Signals) -> None:
            logger.info("Received signal %s, initiating shutdown...", sig.name)
            self._stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler, sig)

    async def start(self) -> None:
        """Start the daemon — initialize all components and enter main loop."""
        if self.running:
            logger.warning("Daemon already running, ignoring start()")
            return

        logger.info(
            "Secretary daemon starting (tick_interval=%ds)...",
            self.config.tick_interval,
        )
        self.running = True
        self._started_at = datetime.now()

        # Register signal handlers
        self._register_signal_handlers()

        # Initialize components
        self.repo = Repository(self.config.data)
        self.audit = AuditLog(self.repo)
        self.resource_guard = ResourceGuard(self.config.engine.resource_guard, self.audit)
        self.checkers = CheckerRegistry()
        self.checkers.register(HealthChecker(self.config.monitor))
        self.checkers.register(DeadmanChecker(self.config.monitor))
        self.dispatcher = Dispatcher(self.config.notify)
        self.dispatcher.register_adapter("qq", HermesQQAdapter())
        self.scheduler = Scheduler(
            config=self.config,
            repo=self.repo,
            checkers=self.checkers,
            dispatcher=self.dispatcher,
            audit=self.audit,
            resource_guard=self.resource_guard,
        )

        # Open database connections
        await self.repo.initialize()

        # Start scheduler (registers default pipeline jobs)
        await self.scheduler.start()

        # Register morning briefing job (every 8 hours = 28800s; actual
        # run gating is time-of-day inside the action)
        from secretary.engine import Job

        async def _morning_briefing_action() -> None:
            """Run morning briefing if it's between 07:50 and 08:10."""
            now = datetime.now()
            if now.hour == 8 and now.minute <= 10:
                from secretary.coach.morning import generate_morning_briefing
                from secretary.notify import Notification, NotificationLevel

                text = await generate_morning_briefing(self.repo)
                notification = Notification(
                    level=NotificationLevel.INFO,
                    title="早安简报",
                    body=text,
                    channel="qq",
                )
                await self.dispatcher.send(notification)
                logger.info("Morning briefing sent.")

        self.scheduler.add_job(
            Job(
                name="morning_briefing",
                action=_morning_briefing_action,
                interval_seconds=600,  # check every 10 min
                resource_check=False,
            )
        )

        # Register evening review job (21:00, "愿您享受安宁的夜晚")
        async def _evening_review_action() -> None:
            """Run evening review if it's between 20:50 and 21:10."""
            now = datetime.now()
            if now.hour == 21 and now.minute <= 10:
                from secretary.coach.evening import generate_evening_review
                from secretary.notify import Notification, NotificationLevel

                text = await generate_evening_review(self.repo)
                notification = Notification(
                    level=NotificationLevel.INFO,
                    title="今日小结",
                    body=text,
                    channel="qq",
                )
                await self.dispatcher.send(notification)
                logger.info("Evening review sent.")

        self.scheduler.add_job(
            Job(
                name="evening_review",
                action=_evening_review_action,
                interval_seconds=600,  # check every 10 min
                resource_check=False,
            )
        )

        # Register cold-skill candidate mining job (weekly aggregation of
        # repeated "allow" queries into rule_candidates.json). Pure-function
        # pipeline, zero LLM; failures are logged and never disturb the loop.
        async def _candidate_mining_action() -> None:
            """Aggregate queries.jsonl into candidate rules and persist them."""
            try:
                from secretary.coldskill.mining import (
                    aggregate_queries,
                    candidates_path,
                    queries_path,
                    write_candidates,
                )

                mining = self.config.mining
                candidates = aggregate_queries(
                    queries_path(),
                    now=time.time(),
                    min_count=mining.min_count,
                    window_days=mining.window_days,
                    data_span_days=mining.data_span_days,
                )
                write_candidates(candidates, candidates_path())
                logger.info("Candidate mining: %d candidate(s) written", len(candidates))
            except Exception:
                logger.warning("Candidate mining job failed", exc_info=True)

        self.scheduler.add_job(
            Job(
                name="candidate_mining",
                action=_candidate_mining_action,
                interval_seconds=604800,  # weekly
                resource_check=False,
            )
        )

        # Register wealth monitoring jobs (portfolio/national_team/announcement)
        from secretary.wealth.jobs import WealthJobs

        wealth_jobs = WealthJobs(
            dispatcher=self.dispatcher,
            portfolio_path=self.config.data.portfolio_path,
        )
        wealth_jobs.register_jobs(self.scheduler)

        # Audit daemon start
        await self.audit.log(
            action="daemon_start",
            target="system",
            details={"tick_interval": self.config.tick_interval},
            result="success",
        )

        logger.info("All components initialized. Entering main loop.")

        try:
            await self.main_loop()
        except asyncio.CancelledError:
            logger.info("Daemon task cancelled")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown — stop scheduler, close connections, audit."""
        if not self.running:
            return

        logger.info(
            "Secretary daemon stopping (uptime=%.0fs, ticks=%d)...",
            self.uptime_seconds,
            self._tick_count,
        )
        self.running = False

        if self.scheduler:
            await self.scheduler.stop()

        # Audit daemon stop
        if self.audit:
            await self.audit.log(
                action="daemon_stop",
                target="system",
                details={
                    "uptime_seconds": round(self.uptime_seconds, 1),
                    "total_ticks": self._tick_count,
                },
                result="success",
            )

        if self.repo:
            await self.repo.close()

        logger.info("Secretary daemon stopped.")

    async def main_loop(self) -> None:
        """Main event loop — resource check → scheduler tick → sleep.

        Exits cleanly when:
        - self.running is set to False
        - The stop_event is set (by signal handler)
        - The task is cancelled
        """
        while self.running:
            try:
                # Check resource guard before heavy operations
                if self.resource_guard is not None:
                    try:
                        resources_ok = await self.resource_guard.check_resources()
                    except Exception:
                        logger.exception("Resource guard check failed")
                        resources_ok = True  # Don't block on guard failure

                    if not resources_ok:
                        logger.warning("Resources insufficient, backing off...")
                        await self.resource_guard.wait_for_resources()
                        continue

                # Run scheduler tick (executes all due jobs)
                if self.scheduler:
                    tick_result = await self.scheduler.tick()
                    self._tick_count += 1

                    if tick_result.jobs_executed or tick_result.jobs_failed:
                        logger.info(
                            "Tick #%d: executed=%d skipped=%d failed=%d",
                            self._tick_count,
                            tick_result.jobs_executed,
                            tick_result.jobs_skipped,
                            tick_result.jobs_failed,
                        )

            except Exception:
                logger.exception("Error in main loop tick")

            # Wait for next tick or stop signal
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.tick_interval,
                )
                # If we get here, stop_event was set
                break
            except asyncio.TimeoutError:
                # Normal: timeout means we should do another tick
                pass

    async def run_checks(self) -> list:
        """Run all registered checks once (for CLI: secretary check)."""
        self.repo = Repository(self.config.data)
        await self.repo.initialize()

        self.checkers = CheckerRegistry()
        self.checkers.register(HealthChecker(self.config.monitor))
        self.checkers.register(DeadmanChecker(self.config.monitor))

        results = await self.checkers.run_all(self.repo)
        await self.repo.close()
        return results
