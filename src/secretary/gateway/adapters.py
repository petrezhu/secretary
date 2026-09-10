"""Platform adapter registry for the message gateway.

Provides direct platform integrations (QQ Bot API, SMTP Email) that the
gateway uses to send messages independently of Hermes.

EmailAdapter reuses secretary.notify.SMTPAdapter to avoid duplicate SMTP logic.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from secretary.notify import SMTPAdapter as _BaseSMTPAdapter

logger = logging.getLogger(__name__)


# ── Adapter Protocol ─────────────────────────────────────────────────────────


class GatewayAdapter(Protocol):
    """Protocol for gateway platform adapters."""

    name: str

    async def send(self, text: str, target: str) -> dict[str, Any]: ...
    async def health_check(self) -> bool: ...


# ── QQ Bot Adapter ───────────────────────────────────────────────────────────


class QQBotAdapter:
    """Send QQ messages via the QQ Bot OpenAPI directly.

    Requires QQ_APP_ID and QQ_CLIENT_SECRET environment variables
    (or explicit constructor args).
    """

    name = "qq"

    def __init__(
        self,
        app_id: str | None = None,
        client_secret: str | None = None,
        api_base: str = "https://api.sgroup.qq.com",
    ):
        self.app_id = app_id or os.environ.get("QQ_APP_ID", "")
        self.client_secret = client_secret or os.environ.get("QQ_CLIENT_SECRET", "")
        self.api_base = api_base.rstrip("/")
        self._token: str | None = None

    async def send(self, text: str, target: str) -> dict[str, Any]:
        """Send a message to a QQ channel/user.

        Args:
            text: Message content.
            target: Channel or user ID.

        Returns:
            {"success": bool, "channel": "qq", "error": str | None}
        """
        try:
            token = await self._get_token()
            return await self._send_message(token, target, text)
        except Exception as e:
            logger.error("QQBot send failed: %s", e)
            return {"success": False, "channel": "qq", "error": str(e)}

    async def health_check(self) -> bool:
        """Check QQ Bot API accessibility by fetching a token."""
        try:
            if not self.app_id or not self.client_secret:
                return False
            await self._get_token()
            return True
        except Exception:
            return False

    async def _get_token(self) -> str:
        """Obtain an access token from QQ Bot OAuth."""
        if self._token:
            return self._token

        import aiohttp

        url = f"{self.api_base}/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.app_id,
            "client_secret": self.client_secret,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                self._token = data.get("access_token", "")
                return self._token

    async def _send_message(
        self, token: str, channel_id: str, content: str
    ) -> dict[str, Any]:
        """Send a message to a QQ channel via the Bot API."""
        import aiohttp

        url = f"{self.api_base}/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"content": content}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status in (200, 201):
                    return {"success": True, "channel": "qq", "error": None}
                body = await resp.text()
                return {
                    "success": False,
                    "channel": "qq",
                    "error": f"HTTP {resp.status}: {body}",
                }


# ── Email Adapter ────────────────────────────────────────────────────────────


class EmailAdapter(_BaseSMTPAdapter):
    """Send emails via SMTP — delegates to secretary.notify.SMTPAdapter.

    Adds the GatewayAdapter-compatible ``send()`` method that returns a dict.
    """

    name = "email"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        to_addr: str | None = None,
    ):
        super().__init__(
            host=host or os.environ.get("SMTP_HOST", "smtp.qq.com"),
            port=port or int(os.environ.get("SMTP_PORT", "465")),
            user=user or os.environ.get("SMTP_USER", ""),
            password=password,
            to_addr=to_addr or os.environ.get("SMTP_TO", ""),
        )
        # If to_addr was empty env fallback, use user
        if not self.to_addr:
            self.to_addr = self.user

    async def send(self, text: str, target: str) -> dict[str, Any]:
        """Send a plain text email.

        Args:
            text: Email body.
            target: Recipient address (or "default" to use self.to_addr).

        Returns:
            {"success": bool, "channel": "email", "error": str | None}
        """
        result = await self.send_text(text, target)
        return {"success": result.success, "channel": result.channel, "error": result.error}


# ── Adapter Registry ─────────────────────────────────────────────────────────


class AdapterRegistry:
    """Registry of platform adapters for the gateway.

    Adapters are registered by name and looked up when dispatching messages.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, GatewayAdapter] = {}

    def register(self, name: str, adapter: GatewayAdapter) -> None:
        """Register an adapter under the given name."""
        self._adapters[name] = adapter

    def get(self, name: str) -> GatewayAdapter | None:
        """Retrieve an adapter by name."""
        return self._adapters.get(name)

    @property
    def available(self) -> list[str]:
        """List registered adapter names."""
        return list(self._adapters.keys())

    async def health_all(self) -> dict[str, bool]:
        """Run health check on all registered adapters."""
        results = {}
        for name, adapter in self._adapters.items():
            try:
                results[name] = await adapter.health_check()
            except Exception:
                results[name] = False
        return results

    async def send(self, channel: str, text: str, target: str) -> dict[str, Any]:
        """Send a message via the named adapter.

        Args:
            channel: Adapter name (e.g. "qq", "email", "both").
            text: Message text.
            target: Platform-specific target (channel ID, email address, etc.)

        Returns:
            {"success": bool, "channel": str, "error": str | None}
        """
        if channel == "both":
            return await self._send_both(text, target)

        adapter = self._adapters.get(channel)
        if not adapter:
            return {"success": False, "channel": channel, "error": f"No adapter for '{channel}'"}
        return await adapter.send(text, target)

    async def _send_both(self, text: str, target: str) -> dict[str, Any]:
        """Send to all registered adapters; return first success or last failure."""
        results = []
        for name, adapter in self._adapters.items():
            result = await adapter.send(text, target)
            results.append(result)
            if result.get("success"):
                return result

        # All failed — return the last result
        return results[-1] if results else {
            "success": False,
            "channel": "both",
            "error": "No adapters registered",
        }
