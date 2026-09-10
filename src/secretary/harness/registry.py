"""Harness registry — register, lookup, and default harness management."""

from __future__ import annotations

import logging

from .base import BaseHarness

logger = logging.getLogger(__name__)


class HarnessRegistry:
    """Registry of named harness instances.

    Usage::

        registry = HarnessRegistry()
        registry.register("hermes", HermesHarness())
        harness = registry.get("hermes")
        default = registry.get_default()
    """

    def __init__(self) -> None:
        self._harnesses: dict[str, BaseHarness] = {}
        self._default_name: str | None = None

    def register(self, name: str, harness: BaseHarness, *, default: bool = False) -> None:
        """Register a harness by name.

        Args:
            name: Unique identifier for this harness.
            harness: The harness instance.
            default: If True, set this as the default harness.
        """
        self._harnesses[name] = harness
        logger.info("Registered harness: %s", name)
        if default or self._default_name is None:
            self._default_name = name

    def get(self, name: str) -> BaseHarness | None:
        """Look up a harness by name. Returns None if not found."""
        return self._harnesses.get(name)

    def get_default(self) -> BaseHarness | None:
        """Return the default harness, or None if registry is empty."""
        if self._default_name is None:
            return None
        return self._harnesses.get(self._default_name)

    def set_default(self, name: str) -> None:
        """Set the default harness by name. Raises KeyError if not found."""
        if name not in self._harnesses:
            raise KeyError(f"Harness '{name}' not registered")
        self._default_name = name

    def list_names(self) -> list[str]:
        """Return all registered harness names."""
        return list(self._harnesses.keys())

    def remove(self, name: str) -> bool:
        """Remove a harness by name. Returns True if found."""
        if name in self._harnesses:
            del self._harnesses[name]
            if self._default_name == name:
                self._default_name = next(iter(self._harnesses), None)
            return True
        return False

    async def close_all(self) -> None:
        """Close all registered harnesses that have a close() method."""
        for name, harness in self._harnesses.items():
            close_fn = getattr(harness, "close", None)
            if close_fn is not None and callable(close_fn):
                try:
                    await close_fn()  # type: ignore[misc]
                except Exception:
                    logger.warning("Failed to close harness %s", name, exc_info=True)
