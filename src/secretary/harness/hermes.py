"""HermesHarness — concrete harness backed by CLIProxyAPI."""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

from .base import BaseHarness, HarnessConnectionError

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:8317/v1"
_DEFAULT_API_KEY = ""
_DEFAULT_MODEL_POOL = [
    "hermes-3-llama-3.1-70b",
    "qwen/qwen3-235b-a22b",
    "deepseek-chat",
]


class HermesHarness(BaseHarness):
    """Agent harness backed by CLIProxyAPI.

    - llm_call: POST to /v1/chat/completions with model fallback pool.
    - execute_tool: placeholder — delegates to internal registry.
    - get_context: returns empty dict (session integration pending).
    - health_check: GET /v1/models.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model_pool: list[str] | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.base_url = (base_url or os.getenv("CLI_PROXY_URL", _DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.getenv("CLI_PROXY_API_KEY", _DEFAULT_API_KEY)
        self.model_pool = model_pool or list(_DEFAULT_MODEL_POOL)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── Internal implementations ──────────────────────────────────────

    async def _do_llm_call(self, prompt: str, context: dict[str, Any]) -> str:
        """POST to /v1/chat/completions, trying each model in the pool."""
        session = await self._get_session()
        messages = []
        if "system" in context:
            messages.append({"role": "system", "content": context["system"]})
        messages.append({"role": "user", "content": prompt})

        last_error: str | None = None
        for model in self.model_pool:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": context.get("temperature", 0.7),
                "max_tokens": context.get("max_tokens", 2048),
            }
            try:
                async with session.post(
                    f"{self.base_url}/chat/completions", json=payload
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    body = await resp.text()
                    last_error = f"model={model} status={resp.status}: {body[:200]}"
                    logger.warning("LLM call failed: %s", last_error)
            except aiohttp.ClientError as exc:
                last_error = f"model={model}: {exc}"
                logger.warning("LLM connection error: %s", last_error)

        raise HarnessConnectionError(
            f"All models in pool exhausted. Last error: {last_error}"
        )

    async def _do_execute_tool(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool — placeholder implementation.

        In production this would delegate to a subprocess or function registry.
        """
        logger.info("Tool execution requested: %s(%s)", tool, params)
        return {"tool": tool, "params": params, "result": "not_implemented"}

    async def _do_get_context(self, session_id: str) -> dict[str, Any]:
        """Get session context — placeholder.

        In production this would read from Hermes session store.
        """
        return {"session_id": session_id, "history": []}

    async def _do_health_check(self) -> bool:
        """GET /v1/models to verify CLIProxyAPI is reachable."""
        session = await self._get_session()
        try:
            async with session.get(f"{self.base_url}/models") as resp:
                return resp.status == 200
        except aiohttp.ClientError:
            return False
