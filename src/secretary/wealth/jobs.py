"""Wealth scheduler integration — register wealth jobs with the Secretary scheduler."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from secretary.engine import Job
from secretary.notify import Notification, NotificationLevel

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_PORTFOLIO_PATH = Path(__file__).parent.parent.parent.parent / "data" / "portfolio.json"


class WealthJobs:
    """Manages wealth-related scheduled jobs and notification integration.

    Jobs:
        - portfolio_check: daily 09:30 + 15:30 (market open/close)
        - national_team: daily 16:30 (post-market)
        - announcement_monitor: hourly 07:00-16:00 (all holdings)
    """

    # State file path (persists across daemon restarts)
    STATE_FILE = Path.home() / ".secretary" / "wealth_state.json"

    def __init__(
        self,
        dispatcher: Any,
        portfolio_path: str | Path | None = None,
        move_threshold: float = 3.0,
    ):
        self.dispatcher = dispatcher
        self.portfolio_path = Path(portfolio_path) if portfolio_path else DEFAULT_PORTFOLIO_PATH
        self.move_threshold = move_threshold

        # State tracking (loaded from disk)
        self._last_nt_score: float | None = None
        self._seen_announcement_urls: dict[str, set[str]] = {}
        self._load_state()

    def _load_state(self) -> None:
        """Load persistent state from disk."""
        try:
            if self.STATE_FILE.exists():
                data = json.loads(self.STATE_FILE.read_text())
                self._last_nt_score = data.get("last_nt_score")
                # Convert lists back to sets
                seen = data.get("seen_announcement_urls", {})
                self._seen_announcement_urls = {
                    k: set(v) for k, v in seen.items()
                }
                logger.info("Loaded wealth state: nt_score=%s, %d stock URLs",
                          self._last_nt_score, len(self._seen_announcement_urls))
        except Exception as e:
            logger.warning("Failed to load wealth state: %s", e)

    def _save_state(self) -> None:
        """Save persistent state to disk."""
        try:
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "last_nt_score": self._last_nt_score,
                "seen_announcement_urls": {
                    k: list(v) for k, v in self._seen_announcement_urls.items()
                },
                "updated_at": datetime.now().isoformat(),
            }
            self.STATE_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning("Failed to save wealth state: %s", e)

    def register_jobs(self, scheduler: Any) -> None:
        """Register all wealth jobs with the scheduler.

        Args:
            scheduler: Scheduler instance to register jobs with
        """
        # Portfolio check — 1 hour interval (checked at 09:30 and 15:30)
        scheduler.add_job(
            Job(
                name="portfolio_check",
                action=self._run_portfolio_check,
                interval_seconds=3600,  # 1 hour, time-gated internally
                resource_check=False,
            )
        )

        # National team — 1 hour interval (checked at 16:30)
        scheduler.add_job(
            Job(
                name="national_team",
                action=self._run_national_team,
                interval_seconds=3600,
                resource_check=False,
            )
        )

        # Announcement monitor — 1 hour interval (07:00-16:00)
        scheduler.add_job(
            Job(
                name="announcement_monitor",
                action=self._run_announcement_monitor,
                interval_seconds=3600,
                resource_check=False,
            )
        )

        logger.info("Wealth jobs registered: portfolio_check, national_team, announcement_monitor")

        # 东方财富持仓同步 — 按需触发，不自动运行
        # 用户要求更新时才检查 cookies 有效性并同步

    def _is_market_hours(self) -> bool:
        """Check if current time is during market hours."""
        now = datetime.now()
        if now.weekday() >= 5:  # Saturday/Sunday
            return False
        hour = now.hour
        return 9 <= hour <= 15

    def _is_near_time(self, target_hour: int, target_minute: int, window_minutes: int = 30) -> bool:
        """Check if current time is within window of target time."""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        target_minutes = target_hour * 60 + target_minute
        current_minutes = now.hour * 60 + now.minute
        return abs(current_minutes - target_minutes) <= window_minutes

    async def _run_portfolio_check(self) -> None:
        """Run portfolio check and notify on significant moves."""
        from secretary.wealth.portfolio import format_portfolio_report, get_portfolio_snapshot

        # Only run near market open (09:30) or close (15:30)
        if not (self._is_near_time(9, 30) or self._is_near_time(15, 30)):
            logger.debug("Skipping portfolio check — not near market open/close")
            return

        snapshot = await get_portfolio_snapshot(
            self.portfolio_path,
            move_threshold=self.move_threshold,
        )

        if not snapshot.holdings:
            logger.info("No portfolio holdings found")
            return

        # Notify on significant moves
        if snapshot.significant_moves:
            report = format_portfolio_report(snapshot)
            await self.dispatcher.send(
                Notification(
                    level=NotificationLevel.WARNING,
                    title="持仓大幅波动",
                    body=report,
                    channel="qq",
                )
            )
            logger.info("Portfolio significant moves: %s", snapshot.significant_moves)
        else:
            logger.info(
                "Portfolio check: total P&L %.2f%%, no significant moves",
                snapshot.total_pnl_pct,
            )

    async def _run_national_team(self) -> None:
        """Run national team signal check and notify on score change."""
        from secretary.wealth.national_team import get_national_team_signal

        # Only run near 16:30 (post-market)
        if not self._is_near_time(16, 30):
            logger.debug("Skipping national team — not near 16:30")
            return

        result = await get_national_team_signal()

        # Check for score change
        if self._last_nt_score is not None and result.score != self._last_nt_score:
            direction = "↑" if result.score > self._last_nt_score else "↓"
            await self.dispatcher.send(
                Notification(
                    level=NotificationLevel.INFO,
                    title="国家队信号变化",
                    body=(
                        f"国家队信号 {direction} {self._last_nt_score:+.1f} → {result.score:+.1f}\n"
                        f"{result.interpretation}"
                    ),
                    channel="qq",
                )
            )
            logger.info("National team score changed: %.1f -> %.1f", self._last_nt_score, result.score)

        self._last_nt_score = result.score
        self._save_state()  # Persist state
        logger.info("National team signal: %.1f — %s", result.score, result.interpretation)

    async def _run_announcement_monitor(self) -> None:
        """Run announcement monitor for all stock/etf holdings."""
        from secretary.wealth.monitor import check_all_holdings, format_announcement_alert
        from secretary.wealth.portfolio import get_all_stock_codes, load_portfolio_data

        # Only run during 07:00-16:00
        hour = datetime.now().hour
        if hour < 7 or hour > 16:
            logger.debug("Skipping announcement monitor — outside 07:00-16:00")
            return

        # Load portfolio to get all stock/etf holdings
        portfolio_data = load_portfolio_data(self.portfolio_path)
        stock_codes = get_all_stock_codes(portfolio_data)

        if not stock_codes:
            logger.info("No stock/etf holdings to monitor")
            return

        # Build name mapping
        stock_names = {h.code: h.name for h in portfolio_data.holdings if h.code}

        # Check all holdings
        results = await check_all_holdings(
            stock_codes=stock_codes,
            stock_names=stock_names,
            seen_urls_map=self._seen_announcement_urls,
        )

        total_announcements = 0
        total_keyword_matches = 0

        for code, result in results.items():
            name = stock_names.get(code, code)

            if result.error:
                logger.warning("Announcement monitor error for %s: %s", name, result.error)
                continue

            # Track seen URLs
            for ann in result.announcements:
                if ann.url:
                    if code not in self._seen_announcement_urls:
                        self._seen_announcement_urls[code] = set()
                    self._seen_announcement_urls[code].add(ann.url)

            # Persist state after tracking URLs
            self._save_state()

            total_announcements += len(result.announcements)

            # Notify on keyword matches
            if result.new_keyword_matches:
                total_keyword_matches += len(result.new_keyword_matches)
                alert = format_announcement_alert(code, name, result)
                await self.dispatcher.send(
                    Notification(
                        level=NotificationLevel.CRITICAL,
                        title=f"{name} 公告预警",
                        body=alert,
                        channel="qq",
                    )
                )
                logger.warning("Keyword matches for %s: %d", name, len(result.new_keyword_matches))

        if total_keyword_matches == 0:
            logger.info(
                "Announcement monitor: checked %d stocks, %d announcements, no keyword matches",
                len(stock_codes),
                total_announcements,
            )

    async def _run_eastmoney_sync(self) -> None:
        """Sync holdings from 东方财富 trading account."""
        from secretary.wealth.eastmoney_sync import format_sync_report, full_sync, is_cookie_fresh

        # Only sync during market hours or shortly after (09:00-16:30)
        hour = datetime.now().hour
        minute = datetime.now().minute
        if hour < 9 or (hour == 16 and minute > 30) or hour > 16:
            logger.debug("Skipping eastmoney sync — outside 09:00-16:30")
            return

        # Check if cookies exist and are fresh
        if not is_cookie_fresh():
            logger.debug("Skipping eastmoney sync — no fresh cookies (need re-login)")
            return

        result = await full_sync(self.portfolio_path, fetch_trades=True)

        if result.error:
            logger.warning("Eastmoney sync failed: %s", result.error)
            # 不主动通知过期，等用户要求时再检测
            return

        report = format_sync_report(result)
        logger.info("Eastmoney sync: %d holdings, %d trades", len(result.holdings), len(result.trades))

        # Notify on successful sync (only if there are changes)
        if result.holdings:
            await self.dispatcher.send(
                Notification(
                    level=NotificationLevel.INFO,
                    title="东方财富持仓同步",
                    body=report,
                    channel="qq",
                )
            )
