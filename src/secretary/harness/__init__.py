"""Harness layer — Agent execution engine abstraction."""

from __future__ import annotations

from typing import Any, Protocol

from .base import (
    BaseHarness,
    HarnessConnectionError,
    HarnessError,
    HarnessTimeoutError,
)
from .hermes import HermesHarness
from .registry import HarnessRegistry

__all__ = [
    "AgentHarness",
    "BaseHarness",
    "HarnessConnectionError",
    "HarnessError",
    "HarnessTimeoutError",
    "HarnessRegistry",
    "HermesHarness",
]


class AgentHarness(Protocol):
    """Unified Agent interface."""

    async def llm_call(self, prompt: str, context: dict[str, Any]) -> str:
        """Call LLM with prompt and context."""
        ...

    async def execute_tool(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool."""
        ...

    async def get_context(self, session_id: str) -> dict[str, Any]:
        """Get session context/memory."""
        ...

    async def health_check(self) -> bool:
        """Check if harness is available."""
        ...
