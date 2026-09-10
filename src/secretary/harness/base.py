"""Base harness — abstract class with shared retry/timeout/error utilities."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class HarnessError(Exception):
    """Base exception for harness failures."""

    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class HarnessTimeoutError(HarnessError):
    """Raised when a harness call exceeds its deadline."""


class HarnessConnectionError(HarnessError):
    """Raised when the harness backend is unreachable."""


class BaseHarness(ABC):
    """Abstract base with retry logic, timeout handling, and error wrapping.

    Subclasses must implement ``_do_llm_call``, ``_do_execute_tool``,
    ``_do_get_context``, and ``_do_health_check``.
    """

    def __init__(
        self,
        *,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
        retry_backoff: float = 1.0,
    ):
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.retry_backoff = retry_backoff

    # ── Public API (satisfies AgentHarness Protocol) ──────────────────

    async def llm_call(self, prompt: str, context: dict[str, Any]) -> str:
        """Call LLM with automatic retry and timeout."""
        return await self._retry_wrap(self._do_llm_call, prompt, context)

    async def execute_tool(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool with automatic retry and timeout."""
        return await self._retry_wrap(self._do_execute_tool, tool, params)

    async def get_context(self, session_id: str) -> dict[str, Any]:
        """Get session context with automatic retry and timeout."""
        return await self._retry_wrap(self._do_get_context, session_id)

    async def health_check(self) -> bool:
        """Check backend availability (no retry — fast fail)."""
        try:
            return await asyncio.wait_for(
                self._do_health_check(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning("Health check timed out for %s", type(self).__name__)
            return False
        except Exception as exc:
            logger.warning("Health check failed for %s: %s", type(self).__name__, exc)
            return False

    # ── Abstract methods for subclasses ───────────────────────────────

    @abstractmethod
    async def _do_llm_call(self, prompt: str, context: dict[str, Any]) -> str:
        ...

    @abstractmethod
    async def _do_execute_tool(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    async def _do_get_context(self, session_id: str) -> dict[str, Any]:
        ...

    @abstractmethod
    async def _do_health_check(self) -> bool:
        ...

    # ── Helpers ───────────────────────────────────────────────────────

    async def _retry_wrap(self, fn, *args: Any) -> Any:
        """Execute *fn* with retry + timeout, wrapping errors."""
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    fn(*args), timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "%s attempt %d/%d timed out",
                    fn.__name__, attempt, self.max_retries,
                )
            except HarnessError:
                raise  # don't retry harness-level errors
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "%s attempt %d/%d failed: %s",
                    fn.__name__, attempt, self.max_retries, exc,
                )
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_backoff * attempt)

        # All retries exhausted
        if isinstance(last_exc, asyncio.TimeoutError):
            raise HarnessTimeoutError(
                f"{fn.__name__} timed out after {self.max_retries} retries",
                cause=last_exc,
            )
        raise HarnessConnectionError(
            f"{fn.__name__} failed after {self.max_retries} retries",
            cause=last_exc,
        )
