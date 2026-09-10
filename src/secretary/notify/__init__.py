"""Notification layer — unified dispatcher with QQ/Email adapters."""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any, Literal, Protocol

from secretary.config import NotifyConfig

logger = logging.getLogger(__name__)

# ── Models ───────────────────────────────────────────────────────────────────


class NotificationLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Notification:
    level: NotificationLevel
    title: str
    body: str
    channel: Literal["qq", "email", "both"] = "qq"
    template: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SendResult:
    success: bool
    channel: str
    error: str | None = None


# ── Adapter Protocol ─────────────────────────────────────────────────────────


class NotifyAdapter(Protocol):
    """Protocol for notification adapters."""

    async def send_text(self, text: str, target: str) -> SendResult: ...
    async def send_html(self, html: str, target: str) -> SendResult: ...
    async def health_check(self) -> bool: ...


# ── Conversation-style formatter ─────────────────────────────────────────────



def _get_user_name() -> str:
    """Read user name from env at call time (testable with monkeypatch)."""
    return os.environ.get("SECRETARY_USER_NAME", "主人")


def format_conversation(notification: Notification) -> str:
    """Format notification into casual Chinese conversation style.

    Examples:
        CRITICAL → '<用户名>，nginx挂了。要我现在重启吗？'
        WARNING  → '注意一下，内存快满了。'
        INFO     → '今天的日报已经生成好了。'
    """
    level = notification.level
    body = notification.body
    user_name = _get_user_name()

    if level == NotificationLevel.CRITICAL:
        # Urgent buddy-talk
        if "？" not in body and "?" not in body:
            return f"{user_name}，{body}。要我现在处理一下吗？"
        return f"{user_name}，{body}"
    elif level == NotificationLevel.WARNING:
        return f"注意一下，{body}。"
    else:
        return body


# ── Hermes QQ Adapter ────────────────────────────────────────────────────────


class HermesQQAdapter:
    """Send QQ messages via Hermes gateway API or hermes CLI."""

    def __init__(
        self,
        gateway_url: str = "http://127.0.0.1:18789",
        target: str = "qqbot",
        use_cli: bool = True,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        self.target = target
        self.use_cli = use_cli

    async def send_text(self, text: str, target: str) -> SendResult:
        """Send plain text message via Hermes."""
        actual_target = target if target != "default" else self.target
        try:
            if self.use_cli:
                return await self._send_via_cli(text, actual_target)
            return await self._send_via_http(text, actual_target)
        except Exception as e:
            logger.error("HermesQQ send failed: %s", e)
            return SendResult(success=False, channel="qq", error=str(e))

    async def send_html(self, html: str, target: str) -> SendResult:
        """QQ doesn't support HTML; fall back to plain text."""
        return await self.send_text(html, target)

    async def health_check(self) -> bool:
        """Check if Hermes gateway is reachable."""
        try:
            if self.use_cli:
                proc = await asyncio.create_subprocess_exec(
                    "hermes", "status",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                return proc.returncode == 0
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.gateway_url}/health", timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _send_via_http(self, text: str, target: str) -> SendResult:
        """Send via Hermes HTTP gateway API."""
        import aiohttp

        url = f"{self.gateway_url}/api/send"
        payload = {"text": text, "target": target, "channel": "qq"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    return SendResult(success=True, channel="qq")
                body = await resp.text()
                return SendResult(success=False, channel="qq", error=f"HTTP {resp.status}: {body}")

    async def _send_via_cli(self, text: str, target: str) -> SendResult:
        """Send via `hermes send` CLI subprocess."""
        proc = await asyncio.create_subprocess_exec(
            "hermes", "send", "--to", target, text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return SendResult(success=True, channel="qq")
        return SendResult(
            success=False,
            channel="qq",
            error=stderr.decode().strip() or f"exit code {proc.returncode}",
        )


# ── SMTP Email Adapter ───────────────────────────────────────────────────────


class SMTPAdapter:
    """Send emails via SMTP. Configure via SMTP_* env vars."""

    def __init__(
        self,
        host: str = "",
        port: int = 465,
        user: str = "",
        password: str | None = None,
        to_addr: str = "",
    ):
        self.host = host or os.environ.get("SMTP_HOST", "smtp.qq.com")
        self.port = port
        self.user = user or os.environ.get("SMTP_USER", "")
        self.password = password or os.environ.get("SMTP_PASSWORD", "")
        self.to_addr = to_addr or os.environ.get("SMTP_TO", self.user)

    async def send_text(self, text: str, target: str) -> SendResult:
        """Send plain text email."""
        return await self._send(text, target, subtype="plain")

    async def send_html(self, html: str, target: str) -> SendResult:
        """Send HTML email."""
        return await self._send(html, target, subtype="html")

    async def health_check(self) -> bool:
        """Check SMTP connectivity."""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._smtp_ping)
        except Exception:
            return False

    async def _send(self, body: str, target: str, subtype: str = "plain") -> SendResult:
        """Compose and send email via SMTP SSL."""
        to = target if target != "default" else self.to_addr
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._smtp_send, body, to, subtype)
            return SendResult(success=True, channel="email")
        except Exception as e:
            logger.error("SMTP send failed: %s", e)
            return SendResult(success=False, channel="email", error=str(e))

    def _smtp_send(self, body: str, to: str, subtype: str) -> None:
        """Blocking SMTP send (runs in executor)."""
        msg = MIMEMultipart("alternative")
        msg["From"] = self.user
        msg["To"] = to
        msg["Subject"] = "Secretary 通知"
        msg.attach(MIMEText(body, subtype, "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30) as server:
            server.login(self.user, self.password)
            server.sendmail(self.user, [to], msg.as_string())

    def _smtp_ping(self) -> bool:
        """Quick SMTP connection check."""
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=5) as server:
            server.login(self.user, self.password)
        return True


# ── Dispatcher ───────────────────────────────────────────────────────────────


class Dispatcher:
    """Dispatch notifications to appropriate channels with conversation-style formatting."""

    def __init__(self, config: NotifyConfig):
        self.config = config
        self._adapters: dict[str, NotifyAdapter] = {}
        # Rate limiting state: {level: [timestamps]}
        self._send_times: dict[str, list[float]] = {
            "warning": [],
            "critical": [],
            "info": [],
        }
        # Rate limits: max per hour
        self._rate_limits = {
            "warning": 5,   # WARNING: 5条/小时
            "critical": 999,  # CRITICAL: 无限制（立即推送）
            "info": 3,       # INFO: 3条/小时（早安+晚间+其他）
        }

    def register_adapter(self, name: str, adapter: NotifyAdapter) -> None:
        """Register a notification adapter for a channel."""
        self._adapters[name] = adapter

    def _check_rate_limit(self, level: str) -> bool:
        """Check if we can send a notification at this level.

        Returns True if allowed, False if rate limited.
        """
        import time
        now = time.time()
        hour_ago = now - 3600

        # Clean old entries
        if level in self._send_times:
            self._send_times[level] = [
                t for t in self._send_times[level] if t > hour_ago
            ]
        else:
            self._send_times[level] = []

        # Check limit
        limit = self._rate_limits.get(level, 5)
        if len(self._send_times[level]) >= limit:
            return False

        # Record this send
        self._send_times[level].append(now)
        return True

    async def send(self, notification: Notification) -> SendResult:
        """Send notification to the appropriate channel(s)."""
        level = notification.level.value

        # Check rate limit
        if not self._check_rate_limit(level):
            logger.info(
                "Notification rate limited: level=%s (limit=%d/hour)",
                level,
                self._rate_limits.get(level, 5),
            )
            return SendResult(
                success=False,
                channel=notification.channel,
                error=f"Rate limited: {level} notifications exceed hourly limit",
            )

        channel = notification.channel
        if channel == "both":
            qq_result = await self._send_to("qq", notification)
            email_result = await self._send_to("email", notification)
            # Return the first successful result, or the last failure
            if qq_result.success:
                return qq_result
            return email_result
        return await self._send_to(channel, notification)

    async def health_check(self) -> dict[str, bool]:
        """Check health of all registered adapters."""
        results = {}
        for name, adapter in self._adapters.items():
            results[name] = await adapter.health_check()
        return results

    async def _send_to(self, channel: str, notification: Notification) -> SendResult:
        adapter = self._adapters.get(channel)
        if not adapter:
            return SendResult(success=False, channel=channel, error=f"No adapter for {channel}")

        text = self._format_message(notification)
        return await adapter.send_text(text, "default")

    def _format_message(self, notification: Notification) -> str:
        """Format notification into conversation-style message."""
        return format_conversation(notification)
