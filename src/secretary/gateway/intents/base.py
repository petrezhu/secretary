"""Intent handler framework — pluggable message intent dispatch.

Each intent is a small handler with regex patterns. The registry walks
handlers in order; the first handler whose patterns match AND whose
handle() returns a reply wins. Everything else falls through to the
Hermes agent (action="allow").

Design rules:
- No LLM/NLU: regex + keywords only (<50ms, zero cost).
- fail-open: any exception in a handler → allow (agent handles it).
- Replies are split into ≤500-char chunks (QQ C2C limit ~700).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from secretary.data.repository import Repository

logger = logging.getLogger(__name__)

# QQ C2C hard truncation budget
MAX_REPLY_LEN = 500


@dataclass
class IntentContext:
    """Everything a handler may need to answer a message."""

    text: str
    user_id: str = "unknown"
    chat_id: str = "unknown"
    repo: Repository | None = None
    config: Any = None  # secretary.config.Config
    extras: dict[str, Any] = field(default_factory=dict)


class IntentHandler(Protocol):
    """A single message intent."""

    name: str
    patterns: list[re.Pattern]

    async def handle(self, ctx: IntentContext) -> str | None:
        """Return reply text to handle the message, or None to fall through."""
        ...


def _matches(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _exact_keywords(text: str, keywords: set[str]) -> bool:
    """Match only if ``text.strip()`` is exactly one of ``keywords`` (ignoring
    trailing CJK/Latin punctuation).  This prevents false positives where a
    keyword appears as a *substring* of a longer sentence (e.g. "持仓" inside
    "我要看一下持仓的详细分析报告")."""
    stripped = text.strip()
    if stripped in keywords:
        return True
    # Strip one trailing punctuation character (CJK or ASCII)
    if stripped and stripped[-1] in "。！？，.!?,;；~～…":
        stripped = stripped[:-1]
    return stripped in keywords


def split_reply(text: str, limit: int = MAX_REPLY_LEN) -> list[str]:
    """Split a long reply into multiple messages at natural boundaries.

    Tries to split at double newlines (paragraph boundaries), then single newlines.
    Each chunk is within the limit.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Try to find a good split point (prefer double newline)
        split_pos = remaining.rfind("\n\n", 0, limit)
        if split_pos == -1:
            # Try single newline
            split_pos = remaining.rfind("\n", 0, limit)
        if split_pos == -1:
            # Try space
            split_pos = remaining.rfind(" ", 0, limit)
        if split_pos == -1:
            # Hard split
            split_pos = limit

        chunk = remaining[:split_pos].rstrip()
        remaining = remaining[split_pos:].lstrip()

        if chunk:
            chunks.append(chunk)

    return chunks if chunks else [text[:limit]]


def quiet_hour(now_hour: int) -> bool:
    """True between 23:30 and 07:50 — non-urgent intents get a soft reply."""
    return now_hour >= 23 or now_hour < 8


class IntentRegistry:
    """Ordered intent dispatch with per-handler fault isolation."""

    def __init__(self) -> None:
        self._handlers: list[IntentHandler] = []
        self._skill_loader: Any | None = None

    def register(self, handler: IntentHandler) -> None:
        self._handlers.append(handler)
        logger.debug(
            "Registered intent '%s' with %d pattern(s)",
            handler.name,
            len(handler.patterns),
        )

    async def dispatch(self, ctx: IntentContext) -> tuple[str | None, str | None, str]:
        """Try every handler in order. Return (reply, intent_name, confidence).

        Confidence levels:
        - "high": handler matched AND returned a reply (data available)
        - "medium": handler matched BUT returned None (data missing/API failed)
        - "low": no handler matched (should fall through to agent)

        Matching strategy per handler:
        - ``handler.keywords`` set → exact keyword match (preferred)
        - ``handler.patterns`` list → regex search (legacy)
        - Neither → catch-all fallback (always invoked, last resort)

        Returns ``(None, None, "low")`` when no intent claimed the message.
        """
        text = ctx.text
        result: str | None = None
        # ColdSkill hot-reload probe (lstat-level cost; no-op unless changed)
        loader = getattr(self, "_skill_loader", None)
        if loader is not None:
            try:
                loader.sync(self)
            except Exception as exc:
                logger.warning("ColdSkill sync failed: %s", exc)
        for handler in self._handlers:
            try:
                kw = getattr(handler, "keywords", None)
                has_patterns = bool(handler.patterns)
                is_fallback = not kw and not has_patterns

                if kw:
                    if not _exact_keywords(text, kw):
                        continue
                elif has_patterns:
                    if not _matches(handler.patterns, text):
                        continue
                # else: fallback — always try

                reply = await handler.handle(ctx)
                if reply:
                    return reply, handler.name, "high"
                if not is_fallback:
                    # Handler claimed the intent but couldn't answer (data missing)
                    return None, handler.name, "medium"
                result = None
            except Exception as exc:
                logger.warning(
                    "Intent '%s' failed for %r: %s", handler.name, text[:40], exc
                )
        return result, None, "low"
