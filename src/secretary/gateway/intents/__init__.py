"""Intent handlers — grouped by domain.

Usage (from inbound.py):

    from secretary.gateway.intents import build_default_registry

    registry = build_default_registry()
    reply, intent, confidence = await registry.dispatch(IntentContext(text=..., repo=..., config=...))
"""

from __future__ import annotations

from secretary.gateway.intents.base import (
    IntentContext,
    IntentRegistry,
)
from secretary.gateway.intents import (
    coldskills,
    goals,
    greetings,
    memory,
    misc,
    system,
    systems,
    tasks,
    wealth,
)


def build_default_registry() -> IntentRegistry:
    """Registry with all built-in intents in dispatch order.

    Order matters: help first (cheap, unambiguous), then social, then
    domain intents (tasks → goals → wealth → system), emotional support
    and quiet-hours last as fallbacks.
    """
    registry = IntentRegistry()
    # Help is unambiguous — register it first.
    # Pass registry reference so it can dynamically generate help text.
    help_handler = misc.HelpHandler(registry=registry)
    registry.register(help_handler)
    for handler in greetings.HANDLERS:
        registry.register(handler)
    for handler in tasks.HANDLERS:
        registry.register(handler)
    for handler in goals.HANDLERS:
        registry.register(handler)
    for handler in wealth.HANDLERS:
        registry.register(handler)
    for handler in system.HANDLERS:
        registry.register(handler)
    for handler in systems.HANDLERS:
        registry.register(handler)
    for handler in memory.HANDLERS:
        registry.register(handler)
    # ColdSkill adoption controls (建议/采纳/技能列表/撤销) — before fallback
    for handler in coldskills.HANDLERS:
        registry.register(handler)
    # Fallbacks last
    registry.register(misc.EmotionHandler())

    # ColdSkill layer — skills insert themselves before the fallback, so
    # core intents always win. Fail-open if the repo is missing.
    from secretary.gateway.coldskill import get_skill_loader

    registry._skill_loader = get_skill_loader()
    registry._skill_loader.sync(registry)

    return registry


__all__ = [
    "IntentContext",
    "IntentRegistry",
    "build_default_registry",
]
